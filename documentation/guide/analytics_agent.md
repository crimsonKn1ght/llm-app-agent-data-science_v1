# Analytics Agent & Tools — In-Depth Guide
 
This document covers the full analytics pipeline: the entry-point tool (`analytical_tool.py`), the analytics agent (`apps/agents/analytics/`), and the individual analytics tools (`apps/tools/analytics_tools/`).
 
---
 
## 1) High-level architecture
 
```
orchestrator → analytical_node
                 └→ analytical_tool(query, sources, filters, runtime)
                      └→ run_analytics_agent(...)
                           ├── Phase 1: Planner (LLM) → PlannerOutput
                           ├── Phase 2: Executor (parallel per source)
                           │     ├── fetch_records (Azure AI Search)
                           │     ├── get_column_schema
                           │     ├── score_text_relevance (optional)
                           │     ├── run_absa (optional)
                           │     ├── run_analysis_code (LLM-generated pandas)
                           │     └── generate_chart (optional)
                           ├── Phase 2.5: Recompute unpacked MVOC sub-sources
                           ├── Phase 3: Narrator (LLM per source)
                           └── Phase 4: Reflector (LLM per source, with retry loops)
                                 └→ AgentResult[] → compiler
```
 
### Key design principle
**Plan → Execute → Narrate → Reflect** — the analytics agent separates computation from prose. The executor runs code on data to produce structured results (JSON/DataFrames), then the narrator converts those results to English, then the reflector validates the narrative matches the data. This prevents hallucinated numbers.
 
---
 
## 2) `analytical_tool.py` — the orchestrator-facing wrapper
 
### What it does
Wraps the analytics agent pipeline with the same call signature as `insights_tool`, so both the orchestrator's `analytical_node` and the ReAct agent's `_call_tool` can invoke it identically.
 
### Function: `analytical_tool(...)`
 
```python
async def analytical_tool(
    query, data_sources, filters, runtime,
    prior_context=None, retrieval_params=None,
    parent_id="", conversation_id=""
) -> Dict[str, Any]
```
 
### Input processing
- Converts Pydantic `filters` → plain dict via `filters.model_dump()` (with fallback to `dict(filters)`)
- Forwards `retrieval_params` from prior `insights_tool` calls for data consistency
 
### Output conversion
The analytics agent returns `AnalyticsState` (its own state class). `analytical_tool` converts this to the standard `source_results` format:
 
```python
# AnalyticsState.agent_results → source_results
{
    "source_results": [
        {"source": "Patient MVOC", "answer": "...", "status": "success", "tool_metadata": {...}},
        {"source": "HCP MVOC", "answer": "...", "status": "success", "tool_metadata": {...}},
    ],
    "status": "success"  # overall
}
```
 
### Error handling
Two-tier soft failure:
1. `AgentException` → known analytics failure → returns error result with descriptive message
2. Generic `Exception` → unexpected error → returns generic error result
 
Neither tier raises to the caller — the compiler always receives something.
 
---
 
## 3) Analytics state types (`apps/agents/analytics/state.py`)
 
### `PlannerOutput`
The planner's full output:
 
| Field | Type | Purpose |
|---|---|---|
| `intent` | `str` | e.g. `"count"`, `"trend"`, `"comparison"`, `"sentiment"` |
| `is_feasible` | `bool` | Whether the query can be answered with available data |
| `feasibility_reason` | `str` | Why infeasible (shown to user) |
| `query` | `str` | Shared retrieval query for all sources |
| `filters` | `Dict` | Shared metadata filters |
| `source_plans` | `List[SourcePlan]` | Per-source execution plans |
| `requires_semantic` | `bool` | Whether semantic relevance scoring is needed |
| `semantic_concept` | `str` | The concept to score relevance against |
| `needs_chart` | `bool` | Whether to generate a chart |
| `chart_type` | `str \| None` | e.g. `"bar"`, `"line"`, `"pie"` |
| `needs_absa` | `bool` | Whether aspect-based sentiment analysis is needed |
| `data_quality_risks` | `List[str]` | Known risks for the narrator to caveat |
 
### `SourcePlan`
Per-source plan:
 
| Field | Purpose |
|---|---|
| `source` | e.g. `"Patient MVOC"` |
| `execution_plan` | Tool sequence: `["fetch_records", "get_column_schema", "run_analysis_code"]` |
| `analysis_steps` | Ordered `AnalysisStep` list (step descriptions, columns, params) |
| `code_hint` | 1–2 sentence natural-language description of what the code should do |
| `expected_row_type` | `"grouped_summary"` / `"time_series"` / `"scalar_summary"` / `"record_list"` |
| `_merged_sources` | Set by executor when MVOC sources are merged |
 
### `AnalysisStep`
```python
{"step": 1, "operation": "filter", "description": "Filter by disease area",
 "columns_used": ["Indication"], "params": {"value": "Asthma"}}
```
 
### `SourceExecutionState` (class, not TypedDict)
Mutable per-source execution tracker:
 
| Field | Purpose |
|---|---|
| `df` | Working DataFrame (set by `fetch_records`) |
| `vectors` | Extracted embedding vectors (for semantic scoring) |
| `schema_info` | Column schema (set by `get_column_schema`) |
| `tool_step_map` | Which steps map to which tools (set by router) |
| `tool_results` | Chronological `ToolResult` list |
| `analysis_result` | Final structured output from `run_analysis_code` |
| `chart_artifact` | Base64 chart image from `generate_chart` |
| `narrative` | English prose from narrator |
| `reflection_result` | Verdict from reflector |
| `_generated_code` | LLM-generated pandas code (for code sharing + reflector re-runs) |
| `status` | `"pending"` / `"running"` / `"completed"` / `"failed"` / `"no_data"` / `"partial"` |
 
### `AnalyticsState` (top-level)
```python
lifecycle: pending → planning → executing → narrating → reflecting → completed/failed
```
 
Contains `source_states: Dict[str, SourceExecutionState]` and `agent_results: List[Dict]`.
 
---
 
## 4) Planner (`planner.py`)
 
### `create_plan(user_query, data_sources, runtime, ...) -> (PlannerOutput, was_retried, issues)`
 
1. Builds input string with query, sources, filters, and field schema context (from `planner_utility.build_schema_context`).
2. Calls `llm_factory.run(prompt_name="analytics_planner", ...)`.
3. Parses JSON with `_safe_json_parse` (handles markdown fences, trailing commas, partial JSON).
4. Validates with `validate_plan(plan, data_sources, filters)`.
5. If validation fails → retry up to `_MAX_PLANNER_RETRIES=2` times, feeding back issues.
6. Returns `(plan, was_retried, remaining_issues)`.
 
### `_safe_json_parse(text) -> Dict`
Robust JSON extractor:
- Direct `json.loads` → markdown fence extraction → brace-delimited extraction → trailing comma cleanup.
- Returns `{"_parse_error": True}` sentinel on total failure.
 
### `_cast_to_planner_output(raw) -> PlannerOutput`
Normalizes raw LLM output to typed `PlannerOutput`. Supports both new compact schema (top-level `query`/`filters`, per-source `code_hint`) and legacy verbose schema (per-source `query`/`filters`/`compute_config`).
 
---
 
## 5) Router (`router.py`)
 
### `route_steps_to_tools(source_plan) -> List[ToolStepMapping]`
 
Deterministic positional mapping — NOT keyword matching.
 
The planner enforces canonical execution patterns:
```
Simple:   fetch_records → get_column_schema → run_analysis_code
Semantic: fetch_records → score_text_relevance → get_column_schema → run_analysis_code
ABSA:     fetch_records → score_text_relevance → get_column_schema → run_absa → run_analysis_code
Chart:    ... → generate_chart
```
 
Tool categories:
| Category | Tools | Steps consumed |
|---|---|---|
| **Passthrough** | `get_column_schema`, `generate_chart` | 0 (no steps assigned) |
| **Single-step** | `fetch_records`, `score_text_relevance`, `run_absa` | 1 (pops front of queue) |
| **Catch-all** | `run_analysis_code` | All remaining steps |
 
### Example
Given `execution_plan=["fetch_records", "get_column_schema", "run_analysis_code"]` and 3 `analysis_steps`:
```
fetch_records     → [step 1]
get_column_schema → []          (passthrough)
run_analysis_code → [step 2, step 3]  (absorbs rest)
```
 
---
 
## 6) Executor (`executor.py`)
 
### MVOC source merging: `_merge_same_index_plans(source_plans)`
 
When 2+ MVOC sub-sources (Patient MVOC, HCP MVOC, PAG MVOC) are selected, they share the same Azure Search index. Instead of querying the MVOC index N times, the executor merges them into one `MVOC_merged` plan, queries once, then splits results back.
 
```
Before: [Patient MVOC plan, HCP MVOC plan, PAG MVOC plan]
After:  [MVOC_merged plan]  +  merge_map: {"MVOC_merged": ["Patient MVOC", "HCP MVOC", "PAG MVOC"]}
```
 
### Code-sharing groups: `_compute_code_sharing_groups(merged_plans)`
 
When multiple sources have identical `execution_plan` AND all referenced columns exist in all sources (checked via `COLUMN_REGISTRY`), one source is designated as **template** and generates the code; others are **followers** that reuse the generated code. This saves 1+ LLM calls per follower.
 
```
Group: template="Social Listening", followers=["Publication Abstracts"]
→ Social Listening generates code, Publication Abstracts reuses it
```
 
### `execute_analytics(...)` — main entry point
 
1. Merge same-index plans.
2. Create `AnalyticsState`.
3. Route steps → tools for each source.
4. Compute code-sharing groups + set up `asyncio.Event`s for synchronization.
5. Execute all source plans in parallel (`asyncio.gather`, max 6 concurrent via semaphore).
6. Unpack merged results back to original source names:
   - Split DataFrame by `Datasource` column.
   - Sub-sources with `< _MIN_ROWS (10)` rows → `status="no_data"`.
   - Sub-sources with enough rows → `_needs_recompute=True` (Phase 2.5 will re-run code).
7. Assess overall status.
 
### `_execute_source_plan(source_state, analytics_state, runtime)`
 
Sequential per-source tool execution:
 
```
for each tool in tool_step_map:
    if status == "no_data" → skip
    if tool == "run_analysis_code" and role == "follower":
        await code_ready_event.wait()  # wait for template to generate code
        inject shared_code into context
    result = tool.execute(source_state, runtime, context)
    if tool == "run_analysis_code" and role == "template":
        cache generated code + signal event
    if result.failed and tool is critical:
        mark source as failed, return
    if df rows < _MIN_ROWS after any tool:
        demote to "no_data"
```
 
Critical tools (failure = source failure): `fetch_records`, `get_column_schema`, `run_analysis_code`.
Non-critical (failure = logged, continue): `generate_chart`, `score_text_relevance`.
 
---
 
## 7) Analytics tools (`apps/tools/analytics_tools/`)
 
### ToolRegistry pattern
All tools inherit from `BaseTool` and self-register at import time:
```python
class FetchRecordsTool(BaseTool):
    name = "fetch_records"
    async def execute(self, source_state, runtime, context) -> ToolResult: ...
ToolRegistry.register(FetchRecordsTool())
```
 
### Tool inventory
 
| Tool | File | Purpose |
|---|---|---|
| `fetch_records` | `fetch_records.py` | Retrieves data from Azure AI Search into `source_state.df` |
| `get_column_schema` | `get_column_schema.py` | Inspects DataFrame dtypes/uniques → `source_state.schema_info` |
| `score_text_relevance` | `score_text_relevance.py` | Cosine-similarity scoring against `semantic_concept` → adds `relevance_score` column |
| `run_absa` | `run_absa.py` | Aspect-based sentiment analysis → adds sentiment columns |
| `run_analysis_code` | `run_analysis_code.py` | LLM generates pandas code, executed in sandbox → `source_state.analysis_result` |
| `generate_chart` | `generate_chart.py` | LLM generates matplotlib code, executed in sandbox → base64 image |
 
### Sandbox safety (`__init__.py`)
 
Code generated by LLM for `run_analysis_code` and `generate_chart` runs in a controlled sandbox:
 
**Allowed libraries:**
```
pandas==2.3.2, numpy==2.2.6, scikit-learn==1.7.2, scipy==1.15.3,
matplotlib==3.10.6, nltk==3.9.4, regex==2025.9.18, rapidfuzz==3.14.1,
+ stdlib: re, json, datetime, collections, math, statistics
```
 
**Blocked modules (via AST validation):**
- Filesystem: `os`, `pathlib`, `glob`, `shutil`, `io`
- Process: `subprocess`, `sys`, `multiprocessing`
- Network: `socket`, `requests`, `aiohttp`, `urllib`
- Serialization: `pickle`, `marshal`
- Code execution: `exec`, `eval`, `compile`, `__import__` (of blocked modules)
- Also blocks `open()` calls
 
`validate_code_safety(code)` parses the code with Python's `ast` module and walks every node to check imports and dangerous builtins before execution.
 
---
 
## 8) Agent main loop (`agent.py`)
 
### `run_analytics_agent(...)` — public API
 
**Phase 1: Planning**
```python
plan, was_retried, plan_issues = await create_plan(...)
if not plan.is_feasible:
    return AnalyticsState with infeasibility AgentResult
```
 
**Phase 2: Execution**
```python
state = await execute_analytics(...)
```
 
**Phase 2.5: MVOC sub-source recompute**
After MVOC merge-unpack, each sub-source has its own DataFrame slice but still carries the merged `analysis_result`. `_recompute_unpacked_sources()` re-executes the generated code on each sub-source's own DF so numbers reflect that sub-source only.
 
**Phase 3+4: Narrate → Reflect (pipelined per source, parallel)**
Each source runs its own pipeline as a single coroutine:
```
narrate_source() → reflect_source() → retry loop
```
All sources run in parallel via `asyncio.gather` with a 180s wall-clock timeout.
 
### Reflection retry loop (`_retry_source_reflection`)
 
```
Initial reflection
├── PASS → done
└── REVISE
    ├── retry_target="narrate" → re-narrate with issues → re-reflect → loop (max 3)
    └── retry_target="analysis_code" → re-run code with feedback → re-narrate → re-reflect → loop (max 3)
```
 
**Hollow-result dedup:** If the reflector flags the same "hollow result" issue (all zeros, 0 matches) on consecutive iterations, the agent accepts the result — the data genuinely has no matches. This prevents burning all 3 loops on a legitimate zero-match scenario.
 
### `_build_agent_result(source_name, source_state, ...)`
 
Converts `SourceExecutionState` → `AgentResult` dict:
- `agent_type = "internal_insights"` (so compiler routes it through standard buckets)
- `tool_metadata.intent = "analytical"` (so compiler knows it's analytical)
- `status`: `"failed"` → `"error"`, `"partial"` or `"completed"` → `"success"`
- `is_partial` flag preserved in `tool_metadata` for visibility
 
---
 
## 9) Narrator (`narrator.py`)
 
### `narrate_source(...)`
 
Converts structured `analysis_result` into English prose for one source.
 
**Guardrails:**
- `df_rows == 0` → canned "no records" narrative (no LLM call)
- `df_rows < 10` → canned "insufficient data" narrative
- `df_rows < _MIN_RECORD_THRESHOLD` → adds low-confidence caveat to prompt
 
**LLM call:** `llm_factory.run(prompt_name="analytics_narrator", ...)` with:
- User query, intent, source name
- Truncated analysis result (max 12K chars, semantically truncated for lists)
- Record count, expected output description
- Reflection issues (if re-narrating after failed reflection)
 
### `_truncate_json_semantically(obj, max_chars)`
Instead of slicing mid-JSON (which corrupts structure), keeps first N list entries that fit within budget and appends `{"_truncated": "... 47 more entries omitted out of 50 total"}`.
 
---
 
## 10) Reflector (`reflector.py`)
 
### `reflect_source(...) -> Dict`
 
Quality gate after narration. Makes one LLM call checking:
 
| Check | What it detects |
|---|---|
| **Faithfulness** | Numbers in narrative don't match `analysis_result` |
| **Answerability** | Narrative doesn't address the user's query |
| **Plan audit** | Planned tools vs actually executed tools mismatch |
| **Sample caveat** | Missing small-sample warning when records < 20 |
| **Hollow result** | Code returned empty/zero from substantial data |
| **Circular analysis** | Code uses `relevance_score` (a filter column) as the analysis metric |
 
### Return structure
```python
{
    "verdict": "pass" | "revise",
    "retry_target": "none" | "narrate" | "analysis_code",
    "issues": ["Numbers don't match: narrative says 45%, data shows 42%"],
    "suggestions": ["Use exact percentages from analysis_result"]
}
```
 
### Circular analysis detection
When `score_text_relevance` was executed, the reflector receives explicit context:
```
SEMANTIC FILTER APPLIED: ... Using relevance_score as the analysis metric is CIRCULAR and must be flagged.
```
 
---
 
## 11) End-to-end execution trace
 
### Simple count query: "How many patients mentioned fatigue?"
 
```
1. Planner → intent="count", source_plans=[{source: "Patient MVOC",
              execution_plan: ["fetch_records", "get_column_schema", "run_analysis_code"]}]
 
2. Router → fetch_records gets step 1, get_column_schema gets [],
             run_analysis_code gets steps 2-3
 
3. Executor:
   fetch_records   → queries MVOC index → df (500 rows)
   get_column_schema → schema_info = {"Text": "object", "Date": "datetime64", ...}
   run_analysis_code → LLM generates:
     df_filtered = df[df['Text'].str.contains('fatigue', case=False)]
     result = {"total": len(df), "matching": len(df_filtered),
               "percentage": round(len(df_filtered)/len(df)*100, 1)}
   → analysis_result = {"total": 500, "matching": 73, "percentage": 14.6}
 
4. Narrator → "Based on analysis of 500 Patient MVOC records, 73 (14.6%)
               mentioned fatigue."
 
5. Reflector → verdict="pass" (numbers match, query answered)
 
6. _build_agent_result → AgentResult with narrative as result
```
 
### Multi-source with MVOC merge
 
```
Sources: [Patient MVOC, HCP MVOC, Social Listening]
 
1. Executor merges Patient+HCP MVOC → MVOC_merged
2. Parallel execution: MVOC_merged + Social Listening
3. MVOC_merged unpacked → Patient MVOC (200 rows), HCP MVOC (150 rows)
4. Phase 2.5: re-run analysis code on each sub-source's own DF
5. Parallel narration: Patient MVOC, HCP MVOC, Social Listening
6. Parallel reflection: all 3 sources
7. 3 AgentResults → compiler
```