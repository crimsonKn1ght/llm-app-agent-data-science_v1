# `web_agent.py` — In-Depth Function-by-Function Description
 
This document explains every function in `apps/agents/web_agent.py` (556 lines).
 
---
 
## File-level role
 
`web_agent.py` is the **external web search branch** of the orchestrator. When the user requests web search (alone or alongside internal sources), the orchestrator routes to `web_agent(state)` which:
 
1. Optionally validates and rewrites the query using conversation context (follow-up turns only).
2. Builds a structured prompt with user filters.
3. Calls the **Gemini LLM** (via `llm_factory.run` with `prompt_name="web_agent_prompt"`) which has Google Search grounding built in.
4. Formats grounding citations into the response.
5. Optionally generates follow-up questions (web-only mode).
6. Returns an `AgentResult` with `agent_type="web_search"`.
 
### Key difference from insights agent
 
The web agent uses **Gemini** (Google), not Azure OpenAI. The Gemini response includes `groundingMetadata` with `groundingChunks` (source URLs) and `groundingSupports` (which text segments map to which sources). The `format_medical_citations` function processes this metadata.
 
---
 
## Types used
 
### `AgentResult` (from `apps/state/state.py`)
 
The web agent always produces exactly **one** `AgentResult`:
 
```python
{
    "query_id": 1,
    "sub_query_id": 1,
    "query": "What are new treatments for asthma?",
    "agent_type": "web_search",
    "result": "According to recent studies... [1, 2]\n\n**Sources:**\n1. [title-abc123](url)...",
    "data_sources": ["google_search"],
    "tool_metadata": {},
    "suggested_questions": [{"id": "1", "question": "..."}],  # only in web-only mode
    "status": "success"  # or "error"
}
```
 
---
 
## 1) `async def build_user_prompt(...)`
 
### What it does
Converts user query + filter selections into a structured text block for the Gemini prompt.
 
### Parameters
| Parameter | Type | Purpose |
|---|---|---|
| `user_query` | `str` | Original user question |
| `disease_area` | `list[str] \| None` | Disease area filters → become "SEARCH INTENTS" |
| `start_date` | `str \| None` | Time period start |
| `end_date` | `str \| None` | Time period end |
| `age_group` | `list[str] \| None` | Age group filters (skipped if contains "all") |
| `geography` | `list[str] \| None` | Global-level geo filter |
| `country` | `list[str] \| None` | Country-level geo filter (skipped if geography is "global") |
| `disease_stage` | `str \| None` | Disease stage filter |
 
### Output format
```
FILTER_ENFORCEMENT: STRICT
 
SEARCH INTENTS:
- intent_type: disease_area
  values:
    - Asthma
 
SEARCH FILTERS:
geography:
  - United Kingdom
date_range:
  start_date: 2024-01-01
  end_date: 2024-12-31
 
USER QUERY:
What are new treatments for asthma?
```
 
### Why "FILTER_ENFORCEMENT: STRICT"?
This instruction tells the Gemini model to only return results matching the specified filters, preventing it from returning generic web results that don't match the user's disease area or geography selections.
 
---
 
## 2) `async def format_medical_citations(response, response_data)`
 
### What it does
Parses Gemini's grounding metadata and inserts inline citation numbers + a **Sources** section into the response text.
 
### How Gemini grounding works
 
Gemini returns response metadata like:
```python
{
    "candidates": [{
        "groundingMetadata": {
            "groundingChunks": [
                {"web": {"title": "NIH Study", "uri": "https://..."}},
                {"web": {"title": "WHO Report", "uri": "https://..."}},
            ],
            "groundingSupports": [
                {
                    "segment": {"startIndex": 0},     # character offset in response
                    "groundingChunkIndices": [0, 1],    # which chunks support this segment
                },
            ]
        }
    }]
}
```
 
### Algorithm
 
1. **Split response into lines**, tracking character offsets per line.
2. **Classify each line as header or content**:
   - Header criteria: short (<50 chars + ends with `:`) OR bold-wrapped (`**...**`) OR very short (<30 chars).
3. **Map grounding supports to lines**: For each support segment, find which line it falls in. If it falls on a header line, **push the citation to the next line** (headers shouldn't have citation markers).
4. **Reconstruct text**: For each non-header line with sources, append `[1, 2, 3]` (max 3 citations per paragraph).
5. **Build Sources section**: Numbered list with `<a>` tags. Each source gets a unique 6-char UUID suffix to prevent duplicate link collapsing.
 
### Concrete example
```
Input:  "Asthma affects millions worldwide."  (with chunk 0 mapped to offset 0)
Output: "Asthma affects millions worldwide. [1]"
        + "\n\n**Sources:**\n1. <a href='...' target='_blank'>NIH Study-a1b2c3</a>"
```
 
### Why UUID suffix on source links?
Multiple sources may have the same title. The UUID prevents the browser/frontend from deduplicating links that point to different URLs.
 
---
 
## 3) `async def validate_and_rewrite_web_query(state: GraphState) -> str`
 
### What it does
Combines query validation and rewriting in a **single LLM call** (saves latency vs two separate calls). Only called for follow-up conversations (`len(conversation_history) > 1`).
 
### Input context
- `web_memory.summary` — compressed prior conversation summary.
- `web_memory.history[-6:]` — last 6 messages formatted as `role: content`.
- `request.userQuery` — current query.
 
### LLM call
Calls `llm_factory.run(prompt_name="web_agent_validate_and_rewrite", ...)`.
 
### Response parsing
Expects JSON:
```json
{"valid": true, "rewritten_query": "expanded standalone query"}
```
 
Falls back to treating the raw response as the rewritten query if JSON parsing fails (backward compatibility).
 
### Return values
| Scenario | Returns |
|---|---|
| Valid query | Rewritten (contextualized) query string |
| Invalid query (not medical/pharma) | `""` (empty string) |
| LLM error | Original query unchanged |
 
### Why rewriting matters
Follow-up queries like "What about in children?" are ambiguous without context. Rewriting turns it into "What about asthma treatments in children?" using conversation history.
 
---
 
## 4) `async def generate_followup_questions(state, user_query, current_answer)`
 
### What it does
Generates up to 4 follow-up questions. **Only called in web-only mode** (`state['only_web'] == True`).
 
### Skip conditions
Returns `[]` (no follow-ups) when:
- Answer is shorter than 100 words.
- Answer contains skip keywords: `"out of scope"`, `"cannot be answered"`, `"not related"`, `"error"`, `"clarification"`.
 
### Pre-processing
Strips the `**Sources:**` section from the answer before sending to the LLM — follow-up questions should be based on content, not citations.
 
### LLM call
Calls `llm_factory.run(prompt_name="web_agent_followup_generator", ...)` with:
- User query + cleaned answer.
- Last 6 conversation history messages.
- Conversation summary.
 
### Return
Parsed JSON `{"selected_followups": ["Q1", "Q2", ...]}` → passed through `assign_ids()`.
 
---
 
## 5) `async def web_agent(state: GraphState) -> Dict`
 
### What it does
This is the **main entry point** called by `orchestrator_workflow.web_search_node`. Orchestrates the full web search pipeline.
 
### Complete flow
 
```
1. Check if follow-up → validate_and_rewrite_web_query()
   ├── Invalid → return "not medical" AgentResult immediately
   └── Valid → use rewritten query
2. Extract filters from request (safe getattr chain)
3. build_user_prompt(query, filters...)
4. Retry loop (max 3 attempts):
   ├── Call Gemini via llm_factory.run("web_agent_prompt", ...)
   ├── Check response has candidates and non-empty text
   └── If empty → sleep(0.5 * 1.5^attempt) and retry
5. format_medical_citations(response, response_data)
6. If web-only → generate_followup_questions()
7. Return AgentResult with status="success"
```
 
### Filter extraction safety
Uses `getattr(filters, "timePeriod", None)` chains instead of direct attribute access. This prevents `AttributeError` when filters or sub-objects are `None`.
 
### Retry backoff
| Attempt | Sleep |
|---|---|
| 1 → 2 | 0.75s |
| 2 → 3 | 1.125s |
 
### Error handling (soft failure)
On any exception, returns an `AgentResult` with:
- `status = "error"`
- `result = constants.WEB_AGENT_ERROR` (canned error message)
- `tool_metadata = {"error_message": str(ex)}`
 
This is a **soft failure** — the compiler receives the error result and can still compile a response from other branches (insights). The web branch never crashes the pipeline.
 
### State returned
```python
{"agent_results": [AgentResult]}
```
Always exactly one result. Uses `operator.add` reducer to merge with other branch results.
 
---
 
## 6) `def assign_ids(questions) -> List[Dict]`
 
### What it does
Converts a list of question strings to numbered dicts: `[{"id": "1", "question": "..."}]`.
 
Caps at 4 questions (hardcoded).
 
---
 
## Cross-cutting patterns
 
| Pattern | Detail |
|---|---|
| **Single-result agent** | Web agent always returns exactly 1 `AgentResult` (vs insights which returns 1 per source) |
| **Gemini, not OpenAI** | Uses Google's grounding API for search; response format differs from Azure OpenAI |
| **Soft failure** | Never raises to the orchestrator; wraps errors in `status="error"` AgentResult |
| **Follow-up questions** | Only generated in web-only mode; mixed mode leaves follow-ups to the compiler |
| **Conversation memory** | Uses `source_memory["web_search"]` bucket (loaded by memory_loader_node) |
| **Batch processing** | No batching — single query, single Gemini call, single response |
