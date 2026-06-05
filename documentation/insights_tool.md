# `insights_tool.py` — In-Depth Component Understanding
 
This document explains the internal design and behavior of:
 
- **File:** `apps/tools/insights_tool.py`
- **Primary role:** The combined QA/Summarization tool with built-in Azure AI Search retrieval. Processes each data source independently and supports both direct (single-call) and map-reduce (chunked) answer generation paths.
 
---
 
## 1) Why this component exists
 
The insights tool is the workhorse for **qualitative questions** — QA and summarization queries that need natural-language answers grounded in retrieved documents. It:
 
1. **Retrieves** relevant documents from Azure AI Search (no separate retrieval step needed by callers).
2. **Builds** structured text records from the retrieved DataFrames.
3. **Generates answers** per source using LLM — either in one call (direct) or via map-reduce for large contexts.
4. **Returns** per-source results that the compiler can render independently.
 
The tool is called by:
- The `insights_react_agent` (via `_call_tool`)
- Directly, as the primary answer-generation path for QA/summary intent
 
---
 
## 2) High-level architecture
 
```
insights_tool(query, data_sources, filters, ...)
    │
    ├── Step 1: _retrieve(query, data_sources, filters, intent)
    │       └── get_aisearch_data(...) → per-source DataFrames
    │       └── _build_records(df, source) → formatted text records
    │       └── _extract_doc_ids(df, source) → document ID lists
    │
    └── Step 2: For each source (parallel via asyncio.gather):
            └── _process_source(source, records, ...)
                    ├── tokens < 100K → _direct_answer(...)
                    │       └── Single LLM call
                    │       └── On failure → fall back to map-reduce
                    │
                    └── tokens ≥ 100K OR direct failed → _map_reduce_answer(...)
                            ├── _create_chunks(records)
                            ├── MAP: parallel LLM calls per chunk
                            └── REDUCE: combine findings into final answer
```
 
---
 
## 3) Constants
 
| Constant | Value | Purpose |
|---|---|---|
| `DIRECT_CONTEXT_THRESHOLD` | `100,000` tokens | If source text fits below this, use direct single-call path |
| `CHUNK_SIZE` | `100,000` tokens | Max tokens per chunk in map-reduce |
| `LLM_SEMAPHORE` | `asyncio.Semaphore(N)` | Limits concurrent LLM calls (from `constants.LLM_CONCURRENCY_LIMIT()`) |
 
---
 
## 4) Public API
 
### `insights_tool(query, data_sources, filters, context, runtime, intent, prior_context, conversation_history, conversation_summary, parent_id, conversation_id) -> Dict`
 
#### Parameters
 
| Parameter | Type | Purpose |
|---|---|---|
| `query` | `str` | User's natural-language question |
| `data_sources` | `List[str]` | Selected sources (e.g., `["Patient MVOC", "Social Listening"]`) |
| `filters` | `Any` | Metadata filters (disease area, geography, time period, etc.) |
| `context` | `Dict[str, Any]` | Normalized filter context for LLM prompts (from `_build_context`) |
| `runtime` | `RuntimeContext` | Logger and LLM factory |
| `intent` | `str` | `"qa"` or `"summary"` (affects retrieval volume and prompt) |
| `prior_context` | `Optional[str]` | Prior answers from dependent queries (enables chained reasoning) |
| `conversation_history` | `Optional[List]` | Recent message history for follow-up context |
| `conversation_summary` | `Optional[str]` | Compressed prior conversation summary |
| `parent_id` | `str` | Log correlation ID |
| `conversation_id` | `str` | Log correlation ID |
 
#### Return shape
 
```python
{
    "source_results": [
        {"source": "Patient MVOC", "answer": "...", "status": "success", "record_count": 87},
        {"source": "Social Listening", "answer": "...", "status": "success", "record_count": 45},
    ],
    "sources": ["Patient MVOC", "Social Listening"],
    "record_counts": {"Patient MVOC": 87, "Social Listening": 45},
    "context_docs": {"Patient MVOC": ["doc_001", "doc_002"], "Social Listening": ["sl_100"]},
    "retrieval_params": {"k_nearest_neighbors": 100, "top": 100, "result_threshold": 100, "intent": "qa"},
    "status": "success"  # "success" | "no_data" | "error"
}
```
 
---
 
## 5) Function-by-function explanation
 
### `_estimate_tokens(text) -> int`
 
Fast token approximation: `len(text) / 3.5`. Used for path selection (direct vs map-reduce) and chunking decisions.
 
---
 
### `_extract_json(text) -> Any`
 
Best-effort JSON extraction from LLM output. Handles:
1. Direct `json.loads` (clean output)
2. Strip markdown code fences
3. Brace-scanning fallback (finds first valid JSON object/array in text)
 
Returns the raw text if no JSON can be extracted (used for map phase chunks).
 
---
 
### `_create_chunks(records) -> List[str]`
 
Splits a list of record strings into chunks that fit within `CHUNK_SIZE` (100K tokens).
 
**Algorithm:**
- Accumulate records into a current chunk until adding the next record would exceed the token budget.
- When exceeded, seal the current chunk and start a new one.
- Final partial chunk is always included.
 
---
 
### `_build_records(df, source) -> List[str]`
 
Converts a retrieved DataFrame into formatted text records suitable for LLM consumption.
 
**Per-source formatting logic:**
 
| Source type | ID extraction | Metadata columns |
|---|---|---|
| Patient MVOC / HCP MVOC / PAG MVOC / Patient MIQs | `df["Name"]` | Id, Date, Indication, TherapyArea, HCPSpecialty, CountryName, Region |
| Publication Abstracts | Extracted from `Dimensions_Url` (regex: `pub.\d+`) | Date, Indication, Product, TherapyArea, Id |
| Social Listening | `df["Url"]` (fallback to `df["Domain"]`) | Date, Indication, CountryName, Region, Domain, overallSentiment, Id |
 
**Output format per record:**
```
Text: The patient reported significant fatigue after treatment...
Metadata- Id: doc_001, Date: 2024-03-15, Indication: Asthma, CountryName: UK
```
 
Only columns that exist in the DataFrame and have non-empty values are included in metadata.
 
---
 
### `_extract_doc_ids(df, source) -> List[str]`
 
Extracts deduplicated document `Name` values from a DataFrame. Used for `context_docs` tracking (retrieval traceability). Order-preserving deduplication via set.
 
---
 
### `_retrieve(query, data_sources, filters, intent, runtime, ...) -> Tuple[Dict, Dict, Dict]`
 
**The retrieval step.** Calls `get_aisearch_data(...)` and converts raw DataFrames to text records.
 
**Retrieval volume by intent:**
 
| Intent | k_nearest_neighbors | top | result_threshold |
|---|---|---|---|
| `"qa"` | 100 | 100 | 100 |
| `"summary"`, `"analytical"`, `"trend_analysis"`, `"comparative"` | 250 | 250 | 250 |
 
**Why higher for summary/analytical:** These intents need broader coverage to be comprehensive. QA can be more targeted.
 
**Returns:**
1. `sources: Dict[str, List[str]]` — source → formatted text records
2. `doc_ids: Dict[str, List[str]]` — source → document IDs for traceability
3. `retrieval_params: Dict` — parameters used (captured by the insights ReAct agent's scratchpad for consistency with analytical_tool)
 
**Error handling:**
- `DataFetchException` (all sources empty) → returns empty dicts, logs warning
- Generic exception → returns empty dicts, logs error
- In both cases, `retrieval_params` is still returned so the scratchpad can capture it
 
---
 
### `_process_source(source, records, query, context, intent, prior_block, runtime, ...) -> Dict`
 
Orchestrates answer generation for **one source**. Decision logic:
 
1. **No records** → immediate `"no_data"` response (no LLM call)
2. **Tokens < 100K** → `_direct_answer()` (single LLM call)
   - If direct fails → **automatic fallback** to map-reduce
3. **Tokens ≥ 100K** → `_map_reduce_answer()` (chunked processing)
 
**Return shape:**
```python
{"source": "Patient MVOC", "answer": "...", "status": "success", "record_count": 87}
```
 
---
 
### `_direct_answer(query, text, source, context, intent, prior_block, runtime, ...) -> Dict[str, str]`
 
**Single LLM call** for sources where all records fit in one prompt.
 
**Prompt construction:**
```
USER QUERY:
{query}
 
SOURCE: {source}
 
RECORDS:
{all records as text}
 
PRIOR CONTEXT FROM RELATED ANALYSIS:
{prior_block if present}
```
 
**Prompt used:** `insights_direct_answer_prompt`
 
**Variables passed to prompt:**
- All context variables (disease_area, region, etc.)
- `intent` (affects system prompt behavior for QA vs summary)
- `conversation_history` and `conversation_summary`
 
**Post-processing:** If the answer contains "no relevant information" (case-insensitive), normalizes to a standard message.
 
**Error handling:** On any exception → returns `{"text": "...", "status": "error"}` which triggers map-reduce fallback in `_process_source`.
 
**Concurrency control:** Uses `LLM_SEMAPHORE` to limit concurrent LLM calls across all sources being processed in parallel.
 
---
 
### `_map_reduce_answer(query, records, source, context, intent, prior_block, runtime, ...) -> Dict[str, str]`
 
**Chunked processing** for large contexts that don't fit in one LLM call.
 
#### MAP Phase
 
1. Split records into chunks via `_create_chunks()`.
2. For each chunk, make a parallel LLM call:
 
**Prompt:** `insights_chunk_analysis_prompt`
 
**User input format:**
```
USER QUERY:
{query}
 
SOURCE: {source}  |  CHUNK: {chunk_id}/{total}
 
RECORDS:
{chunk text}
```
 
**Expected LLM response (JSON):**
```json
{
    "key_findings": ["Finding 1", "Finding 2"],
    "summary": "Brief summary of relevant content in this chunk"
}
```
 
**Error handling per chunk:** On failure → returns `{"summary": "NO_RELEVANT_DATA"}`. Failed chunks are silently excluded from the reduce phase.
 
#### REDUCE Phase
 
1. Collect all `key_findings` and `summaries` from map results.
2. Build reduce input:
```
USER QUERY:
{query}
 
KEY FINDINGS (JSON):
[... all findings ...]
 
CHUNK SUMMARIES:
{all summaries joined}
 
PRIOR CONTEXT FROM RELATED ANALYSIS:
{prior_block if present}
```
 
3. Call LLM with `insights_combine_chunks_prompt`.
 
**Error handling in reduce:** If the reduce LLM call fails, falls back to concatenating the first 10 usable chunk summaries as a `"partial"` result.
 
---
 
### `insights_tool(...)` — main orchestration
 
**Complete flow:**
 
1. Log invocation with intent and query.
2. Call `_retrieve()` → get records and doc IDs per source.
3. If total records is 0 → return `"no_data"` immediately.
4. Build `prior_block` from `prior_context` if provided.
5. Create parallel tasks: one `_process_source()` per source.
6. Execute all via `asyncio.gather(return_exceptions=True)`.
7. Collect results; log and skip any that raised exceptions.
8. Determine overall status:
   - All `"no_data"` → `"no_data"`
   - Any `"success"` → `"success"`
   - Otherwise → `"error"`
9. Return complete result dict.
 
---
 
## 6) LLM concurrency control
 
```python
LLM_SEMAPHORE = asyncio.Semaphore(constants.LLM_CONCURRENCY_LIMIT())
```
 
All LLM calls (`_direct_answer`, `_map_chunk`, reduce call) acquire this semaphore before calling `llm_factory.run()`. This prevents overwhelming the LLM endpoint when processing multiple sources with multiple chunks in parallel.
 
The semaphore limit is configurable via environment/KeyVault.
 
---
 
## 7) How `prior_context` enables dependent queries
 
When the insights ReAct agent runs queries sequentially (sub-query B depends on sub-query A), the scratchpad's `get_prior_context()` is passed as `prior_context` to this tool. It's appended to the user input as:
 
```
PRIOR CONTEXT FROM RELATED ANALYSIS:
Q: What are the treatment outcomes?
A: Patients reported improved symptoms in 72% of cases...
```
 
This allows the LLM to reference prior findings when answering follow-up questions, enabling chained reasoning across tool calls.
 
---
 
## 8) Prompt dependencies
 
| Prompt | Used by | Purpose |
|---|---|---|
| `insights_direct_answer_prompt` | `_direct_answer` | Single-call QA/summary generation |
| `insights_chunk_analysis_prompt` | Map phase of `_map_reduce_answer` | Extract findings from one chunk |
| `insights_combine_chunks_prompt` | Reduce phase of `_map_reduce_answer` | Synthesize all chunk findings into final answer |
 
---
 
## 9) Error handling philosophy
 
The tool follows **graceful degradation**:
 
| Failure | Recovery |
|---|---|
| All retrieval fails | Return `"no_data"` status (no LLM calls) |
| One source retrieval fails | That source gets empty records → `"no_data"` per-source |
| Direct answer LLM fails | Automatic fallback to map-reduce |
| Individual chunk fails | Excluded from reduce; other chunks still contribute |
| Reduce LLM fails | Fallback to first 10 usable chunk summaries (`"partial"` status) |
| Entire source processing fails | Exception logged; other sources still return results |
 
**No exception propagates to the caller** — the tool always returns a structured result dict.
 
---
 
## 10) Concrete execution example
 
### Query: "What are common side effects?" (QA intent, 2 sources)
 
```
1. _retrieve(query, ["Patient MVOC", "Social Listening"], filters, "qa")
   → Patient MVOC: 87 records, Social Listening: 45 records
   → retrieval_params: {k_nearest: 100, top: 100, threshold: 100, intent: "qa"}
 
2. Parallel source processing:
   ├── _process_source("Patient MVOC", 87 records)
   │    tokens ≈ 25,000 < 100,000 → _direct_answer()
   │    → LLM returns comprehensive answer
   │    → {"source": "Patient MVOC", "answer": "Patients reported...", "status": "success"}
   │
   └── _process_source("Social Listening", 45 records)
        tokens ≈ 15,000 < 100,000 → _direct_answer()
        → LLM returns answer
        → {"source": "Social Listening", "answer": "Social media posts...", "status": "success"}
 
3. Overall status: "success" (at least one source succeeded)
4. Return full result dict with source_results, context_docs, retrieval_params
```
 
### Query: "Summarize all patient feedback" (summary intent, large dataset)
 
```
1. _retrieve(query, ["Patient MVOC"], filters, "summary")
   → Patient MVOC: 250 records (summary uses k=250)
 
2. _process_source("Patient MVOC", 250 records)
   tokens ≈ 120,000 > 100,000 → _map_reduce_answer()
   → _create_chunks() → 2 chunks
   → MAP: 2 parallel LLM calls → key_findings + summaries
   → REDUCE: 1 LLM call combining all findings
   → {"source": "Patient MVOC", "answer": "Key themes include...", "status": "success"}
 
3. Return result
```
