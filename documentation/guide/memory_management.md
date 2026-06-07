# Memory Management — In-Depth Guide
 
This document covers `apps/memory_management/memory.py` (985 lines) and `apps/memory_management/conversation_summarizer.py` (155 lines).
 
---
 
## 1) High-level role
 
The memory system handles **multi-turn conversation persistence** for the Patient+ orchestrator. It:
 
1. **Loads** prior conversation context from SQL Server at the start of each request.
2. **Hydrates** per-source memory (summary + history) into `state["source_memory"]`.
3. **Saves** the response, per-source summaries, and context IDs back to SQL Server after compilation.
4. **Compresses** conversation history when token budget is exceeded, using LLM summarization.
 
### Why per-source memory?
 
Each data source (Patient MVOC, Social Listening, etc.) maintains its own conversation history. This means:
- The insights tool for Patient MVOC sees only Patient MVOC conversation context.
- The web agent sees only web_search conversation context.
- Summaries are source-specific, preventing cross-source context bleed.
 
---
 
## 2) Database tables
 
| Table | Purpose | Key |
|---|---|---|
| `dbo.ai_gen_query` | Parent session record — one per conversation thread | `parentConversationID` |
| `dbo.ai_gen_query_conversations` | One row per conversation turn (user query + response) | `conversationID` + `parentConversationID` |
| `dbo.ai_gen_query_summaries` | One row per (parentConversationID, dataSource) pair | Composite: `parentConversationID` + `dataSource` |
 
### `jsonResponse` column format
Stored as a JSON string in `ai_gen_query_conversations`:
```json
{"social_listening": "Formatted text answer for SL…", "dimensions": "Formatted text answer for DIM…"}
```
This per-source map is what enables source-specific history reconstruction.
 
---
 
## 3) `source_memory` state structure
 
```python
state["source_memory"] = {
    "Patient MVOC": {
        "summary": "In previous turns, the user asked about fatigue side effects...",
        "history": [
            {"role": "user", "content": "What are common side effects?"},
            {"role": "assistant", "content": "Based on Patient MVOC data..."},
            {"role": "user", "content": "What about in children?"},
            {"role": "assistant", "content": "For pediatric patients..."},
        ]
    },
    "Social Listening": {
        "summary": "",
        "history": [
            {"role": "user", "content": "What are common side effects?"},
            {"role": "assistant", "content": "Social listening data shows..."},
        ]
    },
    "web_search": {
        "summary": "",
        "history": [...]
    }
}
```
 
---
 
## 4) `AsyncSQLConversationStore` — DB access class
 
Initialized with `parent_id` and `conversation_id`. Uses `AsyncSQLExecutor.get()` (singleton connection pool).
 
### Methods
 
#### `ensure_parent_record(parent_id, mudid, ...)`
Checks if a row exists in `ai_gen_query` for this `parentConversationID`. If not, inserts one with `status='active'`.
 
- Idempotent — safe to call on every request.
- Records metadata: `mudid`, `queryName`, `userSelection` (serialized filters), `persona`, `isWebSearch`.
 
#### `load_all_summaries(thread_id) -> Dict[str, str]`
Loads all rows from `ai_gen_query_summaries` for this thread.
 
Returns:
```python
{"Patient MVOC": "Previous turns discussed fatigue...", "Social Listening": "..."}
```
Ordered by `sequenceNumber DESC` — latest summary per source wins.
 
#### `load_conversation_rows(thread_id) -> List[Dict]`
Loads all **completed, non-error** rows from `ai_gen_query_conversations`.
 
Key processing:
- Filters out `status != 'completed'` and rows with `Error_Code`.
- Parses `jsonResponse` from JSON string to dict via `_parse_json_response`.
- Returns chronologically ordered list (by `SequenceNumber`).
 
#### `save_user_query(thread_id, conversation_id, user_query, ...)`
Inserts a `status='processing'` row immediately when the user sends a query — before the LLM responds. `update_with_response()` stamps the answer later.
 
- Verifies parent record exists first (raises `AgentExecutionException` if not).
- Records `sequence_num`, `rephrased_query`, `contextList`, `Input_Time_Stamp`.
 
#### `update_with_response(conversation_id, ui_final_response, json_response, ...)`
Stamps the final answer onto the existing `processing` row:
- `uifinalResponse` — combined human-readable answer.
- `jsonResponse` — per-source response dict serialized to JSON string.
- `suggestedQuestions` — follow-up questions.
- `contextList` — retrieved document IDs (JSON string).
- `Error_Code` / `Error_Message` — error metadata if applicable.
 
#### `get_conversation_sequence(parent_id) -> int`
Returns `MAX(SequenceNumber)` for this parent, or 0 if none. Used to compute the next sequence number.
 
#### `save_summary(thread_id, summary, sequence_num, data_source)`
Upserts one row in `ai_gen_query_summaries`. Composite key is `(parentConversationID, dataSource)` — one row per source per conversation thread.
 
- Checks for existing row first.
- Updates if exists, inserts if new.
 
#### `save_all_source_summaries(thread_id, source_memory, sequence_num)`
Concurrently upserts summaries for all sources that have non-empty summaries. Uses `asyncio.gather` via `_gather_with_exceptions`.
 
---
 
## 5) `_build_source_memory_from_rows(conversation_rows, db_summaries)`
 
### What it does
Reconstructs `source_memory` from database data at session start.
 
### Algorithm
1. Collect all source names from both `db_summaries` and `jsonResponse` fields across all rows.
2. Initialize each source with its persisted summary (from `ai_gen_query_summaries`).
3. Replay every completed conversation row into per-source histories:
   - For each row's `jsonResponse` dict, add a `user` message (the row's `userQuery`) and an `assistant` message (the source-specific response text).
 
### Example reconstruction
Given 2 completed rows:
```
Row 1: userQuery="side effects?", jsonResponse={"Patient MVOC": "fatigue...", "SL": "posts show..."}
Row 2: userQuery="in children?",  jsonResponse={"Patient MVOC": "pediatric...", "SL": "no data"}
```
Produces:
```python
{
    "Patient MVOC": {
        "summary": "...(from DB)...",
        "history": [
            {"role": "user", "content": "side effects?"},
            {"role": "assistant", "content": "fatigue..."},
            {"role": "user", "content": "in children?"},
            {"role": "assistant", "content": "pediatric..."},
        ]
    },
    "SL": {
        "summary": "...",
        "history": [
            {"role": "user", "content": "side effects?"},
            {"role": "assistant", "content": "posts show..."},
            {"role": "user", "content": "in children?"},
            {"role": "assistant", "content": "no data"},
        ]
    }
}
```
 
---
 
## 6) LangGraph nodes
 
### `async_memory_loader_node(state) -> GraphState`
 
Called **before** the orchestrator graph starts processing. Steps:
 
1. `ensure_parent_record()` — create session row if first turn.
2. `save_user_query()` — insert `processing` row for this turn.
3. Concurrent DB fetch (with sequential fallback on failure):
   - `load_all_summaries(thread_id)` → per-source compressed narratives.
   - `load_conversation_rows(thread_id)` → recent turn history.
4. `_build_source_memory_from_rows()` → hydrate `state["source_memory"]`.
5. Merge any pre-existing `source_memory` entries (edge case).
 
### `async_memory_saver_node(state) -> GraphState`
 
Called **after** compilation (in the `finally` block of `orchestrator_entry.py`). Steps:
 
1. For each source in `source_insights_map` (per-source compiled text):
   - `ConversationMemoryManager.update_source_memory()` — appends new user/assistant messages to history, triggers compression if needed.
2. Concurrent DB writes:
   - `update_with_response()` — stamp answer onto the `processing` row.
   - `save_all_source_summaries()` — persist compressed summaries.
 
### Why `finally` block?
The memory saver runs regardless of whether the graph succeeded or raised an exception. This ensures the user's query is always recorded (with error metadata if applicable).
 
---
 
## 7) `ConversationMemoryManager` (`conversation_summarizer.py`)
 
### Constructor
```python
ConversationMemoryManager(
    runtime,
    max_history_tokens=70000,   # token budget for summary + history combined
    keep_last_turns=6,           # keep 6 most recent messages verbatim
)
```
 
### `estimate_tokens(text) -> int`
Fast approximation: `int(1.3 * len(text) / 4)`. Uses 1.3x multiplier as safety margin over the standard ~4 chars/token heuristic.
 
### `update_source_memory(state, source_name, user_query, agent_result)`
1. Appends new user + assistant messages to the source's history.
2. Estimates total token count (summary + all history).
3. If over `max_history_tokens` (70K) → triggers `_compress_memory()`.
 
### `_compress_memory(source_memory, current_query, source_name)`
LLM-based conversation compression.
 
**Algorithm:**
1. Split history into `recent` (last 6 messages) and `older` (everything before).
2. Format `older` as text: `"USER: query1\nASSISTANT: response1\n..."`.
3. Call `llm_factory.run(prompt_name="conversation_summary_prompt", ...)`.
4. Append new summary to existing summary (accumulative, not replacement).
5. Replace history with only `recent` (the 6 most recent messages).
 
**Fallback on LLM failure:**
If the LLM summarization call fails, takes the first 3 assistant messages from `older`, truncates each to 80 chars, and appends as `"Previous context: preview1; preview2; preview3"`.
 
### `get_context(state, source_name) -> Dict`
Simple accessor returning `{"summary": "...", "history": [...]}` for a source.
 
---
 
## 8) Helper functions
 
### `merge_source_histories(state) -> str`
Builds a text block of conversation context for LLM prompts. Used by `query_analyzer`, `insights_agent`, and `compiler_agent`.
 
For each source in the user's selected `dataSources`:
- Appends the source's summary (if any).
- Appends the last 6 history messages.
 
### `_serialize_json_response(json_response) -> Optional[str]`
Serializes per-source response dict to JSON string for DB storage. Returns `None` on failure.
 
### `_parse_json_response(raw) -> Dict[str, str]`
Parses JSON string from DB back to dict. Returns `{}` on any failure.
 
### `_gather_with_exceptions(*coros, ...) -> List[Any]`
Runs coroutines concurrently, logs any failures, and raises the first exception. Used for concurrent DB operations.
 
---
 
## 9) Concrete lifecycle example
 
### Turn 1 (first message in conversation)
 
```
memory_loader_node:
  1. ensure_parent_record() → INSERT into ai_gen_query
  2. save_user_query() → INSERT into ai_gen_query_conversations (status='processing')
  3. load_all_summaries() → {} (no prior summaries)
  4. load_conversation_rows() → [] (no prior turns)
  5. source_memory = {} (empty)
 
... orchestrator processes query ...
 
memory_saver_node:
  1. update_source_memory() for each source → appends user+assistant to history
     (no compression needed — just 2 messages per source)
  2. update_with_response() → UPDATE conversations row (status='completed')
  3. save_all_source_summaries() → no summaries to save (history too small)
```
 
### Turn 10 (compression triggers)
 
```
memory_loader_node:
  1. load_all_summaries() → {"Patient MVOC": "Prior summary from turn 7..."}
  2. load_conversation_rows() → 9 completed rows
  3. _build_source_memory_from_rows() → 18 messages per source (9 user + 9 assistant)
     + summary from DB
 
... orchestrator processes query ...
 
memory_saver_node:
  1. update_source_memory("Patient MVOC") →
     history now has 20 messages → estimate_tokens = 85000 > 70000
     → _compress_memory() triggered:
       - older = first 14 messages
       - recent = last 6 messages
       - LLM compresses older into ~200 word summary
       - source_memory["Patient MVOC"]["summary"] = "prior summary\n\nnew summary"
       - source_memory["Patient MVOC"]["history"] = [last 6 messages]
  2. save_all_source_summaries() → upserts compressed summary to DB
```
 
---
 
## 10) Cross-cutting patterns
 
| Pattern | Detail |
|---|---|
| **Per-source isolation** | Each source has independent summary + history — no cross-contamination |
| **Accumulative summaries** | New summaries append to old ones (not replace), preserving long-term context |
| **Graceful compression fallback** | If LLM fails, uses truncated previews instead of losing context |
| **Always-run saver** | Memory saver runs in `finally` block — errors are recorded, not lost |
| **Concurrent DB ops** | Uses `asyncio.gather` for parallel reads/writes with structured error handling |
| **Processing → completed** | Two-phase write: insert `processing` row at start, update to `completed` at end |
