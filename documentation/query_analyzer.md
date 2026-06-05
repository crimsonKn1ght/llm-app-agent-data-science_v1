# `query_analyzer.py` — In-Depth Component Understanding
 
This document explains the internal design and behavior of:
 
- **File:** `apps/orchestrator/query_analyzer.py`
- **Primary role:** Perform a single unified LLM call that classifies, decomposes, and scope-checks the user's query — producing the structured artifacts that drive all downstream routing and execution.
 
---
 
## 1) Why this component exists
 
Before the query analyzer was introduced, classification and decomposition were handled by two separate files (`query_decomposer.py` and `scope_classifier.py`) requiring two LLM calls. The query analyzer consolidates these into **one LLM call** that performs three jobs simultaneously:
 
1. **Query type classification:** Is this an analytical question (counting, statistics, trends) or an insights question (qualitative QA, summarization)?
2. **Sub-query decomposition:** Break complex multi-part questions into independently executable chunks.
3. **Scope classification:** Determine whether each sub-query is in-scope (answerable from available data) or out-of-scope.
 
This component is the **first decision point** in the orchestrator after routing. Its output directly determines:
- Which agent handles execution (analytics node vs ReAct agent vs skip-to-compiler)
- How many tool calls will be made
- Whether queries are processed in parallel or sequentially
 
---
 
## 2) Position in the orchestrator flow
 
```
router_node → query_analyzer_node → type_router
                                       ├── "analytical" → analytical_node
                                       ├── "insights"  → insights_react_node
                                       └── "compiler"  → compiler_node (all out-of-scope)
```
 
The `query_analyzer_node` in `orchestrator_workflow.py` calls `analyze_query(...)` from this file, then processes the result to split out-of-scope sub-queries (immediate `AgentResult`) from in-scope ones (`DecomposedQuery` for downstream agents).
 
---
 
## 3) Public API
 
### `analyze_query(query, data_sources, state, runtime, parent_id, conversation_id) -> Dict[str, Any]`
 
This is the sole public function. It's called by `query_analyzer_node` in the orchestrator workflow.
 
#### Parameters
 
| Parameter | Type | Purpose |
|---|---|---|
| `query` | `str` | The user's natural-language question |
| `data_sources` | `List[str]` | Selected data sources (e.g., `["Patient MVOC", "Social Listening"]`) |
| `state` | `Dict[str, Any]` | Full `GraphState` — used to extract conversation history |
| `runtime` | `RuntimeContext` | Provides `llm_factory` and `logger` |
| `parent_id` | `str` | Parent conversation ID for log correlation |
| `conversation_id` | `str` | Child conversation ID for log correlation |
 
#### Return shape
 
```python
{
    "query_type": "analytical" | "insights" | "web" | "out_of_scope",
    "is_complex": bool,
    "sub_queries": [
        {
            "query": str,           # The sub-query text
            "intent": str,          # "qa" | "summary" | "analytical" | "trend_analysis" | "comparative"
            "topic": str,           # Topic label (e.g., "side effects")
            "scope": str,           # "in_scope" | "out_of_scope"
            "scope_reasoning": str, # Why the scope decision was made
            "tool_hint": str,       # "insights_tool" | "analytical_tool"
        }
    ],
    "reasoning": str,  # LLM's reasoning about its classification
}
```
 
---
 
## 4) Internal execution flow
 
### Step 1: Build conversation history context
 
```python
history_context = merge_source_histories(state)
```
 
Uses `merge_source_histories` from `apps/memory_management/memory.py` to build a text block of prior conversation context. This helps the LLM understand follow-up queries like "What about in children?" by providing the conversation thread.
 
If history extraction fails, silently continues with empty context (defensive).
 
### Step 2: LLM call
 
```python
result = await runtime.llm_factory.run(
    prompt_name="query_analyzer_prompt",
    user_input=f"Query to analyze: {query}\nAvailable data sources: {...}",
    variables={
        "data_sources": "...",
        "history_context": history_context,
    },
)
```
 
The prompt (`query_analyzer_prompt`) is loaded from the prompt registry. It receives:
- The user query
- Available data source names
- Conversation history context
 
The LLM returns a JSON object with classification, sub-queries, and reasoning.
 
### Step 3: Parse and normalize
 
```python
cleaned = result.strip().strip("```json").strip("```").strip()
parsed = json.loads(cleaned)
return _normalize_result(parsed, query, state)
```
 
Strips markdown code fences that the LLM sometimes adds, then normalizes the raw output.
 
---
 
## 5) Function-by-function explanation
 
### `analyze_query(...)` — main entry point
 
**Happy path:**
1. Extract conversation history (silent failure on error).
2. Log analysis start.
3. Call LLM with query + context.
4. Strip markdown fences, parse JSON.
5. Normalize and validate via `_normalize_result()`.
6. Log completion with type/complexity/count.
7. Return structured result.
 
**Error handling (three tiers):**
 
| Error type | Handling | Outcome |
|---|---|---|
| `RetryError` | LLM call failed after all retries | Raises `AgentExecutionException` with `LLM_API_ERROR` code |
| `AgentException` | Known application error | Re-raised unchanged (already well-typed) |
| Generic `Exception` | JSON parse error, unexpected failures | **Graceful fallback** — returns a safe default result |
 
**The graceful fallback** is critical: if analysis fails entirely, the system still proceeds by treating the query as a single in-scope insights question. This prevents analyzer failures from blocking the entire pipeline.
 
```python
# Fallback result on unexpected error:
{
    "query_type": "insights",
    "is_complex": False,
    "sub_queries": [{
        "query": original_query,
        "intent": "qa",
        "topic": "general",
        "scope": "in_scope",
        "scope_reasoning": "Fallback — assuming in scope due to analysis error",
        "tool_hint": "insights_tool",
    }],
    "reasoning": f"Fallback due to error: {str(e)}",
}
```
 
---
 
### `_normalize_result(parsed, original_query, state) -> Dict[str, Any]`
 
**Purpose:** Validate and normalize the raw LLM output to ensure downstream code always receives consistent, well-typed data.
 
**Key normalizations performed:**
 
#### 1. Query type validation
```python
valid_types = {"analytical", "insights", "web", "out_of_scope"}
if query_type not in valid_types:
    query_type = "insights"  # safe default
```
 
#### 2. Sub-query hard cap
```python
MAX_SUB_QUERIES = 3
if len(sub_queries) > MAX_SUB_QUERIES:
    sub_queries = sub_queries[:MAX_SUB_QUERIES]
```
 
**Why this exists:** The LLM sometimes over-decomposes broad questions into 5+ sub-queries, which causes excessive tool calls, high latency, and diminishing returns. Capping at 3 is a pragmatic cost/latency guard.
 
#### 3. Analytical passthrough (no decomposition)
```python
if query_type == "analytical":
    return {
        "query_type": "analytical",
        "is_complex": False,
        "sub_queries": [{
            "query": original_query,  # FULL original query, not decomposed
            "intent": "analytical",
            "tool_hint": "analytical_tool",
            ...
        }],
    }
```
 
**Critical design decision:** Analytical queries are **never decomposed**. Even if the LLM suggests sub-queries, they're discarded. The full original query is passed as-is to `analytical_tool`, which has its own internal planner that understands how to handle multi-part analytical questions in a single execution pass.
 
**Why:** The analytics pipeline (planner → executor → narrator → reflector) is designed to handle complex analytical queries holistically. Decomposing them externally would lose cross-source context and prevent the analytics planner from making optimal execution decisions.
 
#### 4. Empty sub-queries safety
```python
if not sub_queries:
    sub_queries = [{
        "query": original_query,
        "intent": "qa",
        "scope": "in_scope",
        "tool_hint": "insights_tool",
    }]
```
 
Ensures there's always at least one sub-query to process, even if the LLM returns an empty list.
 
#### 5. Per-sub-query normalization
For each sub-query, ensures all fields exist with valid defaults:
- `query` → falls back to `original_query`
- `intent` → defaults to `"qa"`
- `tool_hint` → auto-assigned from intent if missing (via `_intent_to_tool_hint`)
- `scope` → defaults to `"in_scope"`
- `topic` → defaults to empty string
 
---
 
### `_intent_to_tool_hint(intent: str) -> str`
 
**Purpose:** Map intent labels to the recommended execution tool when the LLM doesn't provide an explicit `tool_hint`.
 
**Mapping table:**
 
| Intent | Tool hint | Reasoning |
|---|---|---|
| `"qa"` | `"insights_tool"` | Question-answering → qualitative retrieval + LLM |
| `"summary"` | `"insights_tool"` | Summarization → qualitative retrieval + LLM |
| `"analytical"` | `"analytical_tool"` | Counting/stats → code generation pipeline |
| `"trend_analysis"` | `"analytical_tool"` | Time-series analysis → code generation |
| `"comparative"` | `"analytical_tool"` | Comparison queries → code generation |
| *(anything else)* | `"insights_tool"` | Safe default for unknown intents |
 
---
 
## 6) How the output is consumed downstream
 
The `query_analyzer_node` in `orchestrator_workflow.py` processes the return value like this:
 
```python
result = await analyze_query(query, data_sources, state, runtime, ...)
 
# Extract classification
query_type = result.get("query_type", "insights")
is_complex = result.get("is_complex", False)
sub_queries = result.get("sub_queries", [])
 
# Split into out-of-scope (→ immediate AgentResult) and in-scope (→ DecomposedQuery)
for sq in sub_queries:
    if sq["scope"] == "out_of_scope":
        # Create AgentResult with status="out_of_scope" → goes directly to compiler
    else:
        # Create DecomposedQuery → goes to downstream agents
```
 
The `type_router` then inspects `query_type` and the remaining in-scope sub-queries:
- If all out-of-scope → routes to compiler
- If `query_type == "analytical"` → routes to `analytical_node`
- Otherwise → routes to `insights_react_node`
 
---
 
## 7) Concrete examples
 
### Example A: Simple insights question
**Query:** "What are common side effects of Drug X?"
 
**LLM output (normalized):**
```python
{
    "query_type": "insights",
    "is_complex": False,
    "sub_queries": [{
        "query": "What are common side effects of Drug X?",
        "intent": "qa",
        "topic": "side effects",
        "scope": "in_scope",
        "scope_reasoning": "Query relates to patient/medical feedback data",
        "tool_hint": "insights_tool",
    }],
    "reasoning": "Simple single-topic QA question"
}
```
 
### Example B: Analytical question
**Query:** "How many patients mentioned fatigue in the last 6 months?"
 
**LLM output (normalized):**
```python
{
    "query_type": "analytical",
    "is_complex": False,
    "sub_queries": [{
        "query": "How many patients mentioned fatigue in the last 6 months?",
        "intent": "analytical",
        "topic": "analytical",
        "scope": "in_scope",
        "scope_reasoning": "Analytical queries are always in scope",
        "tool_hint": "analytical_tool",
    }],
    "reasoning": "Counting query requiring data aggregation"
}
```
 
### Example C: Complex multi-part question
**Query:** "What are the treatment outcomes and what is the sentiment distribution?"
 
**LLM output (normalized):**
```python
{
    "query_type": "insights",
    "is_complex": True,
    "sub_queries": [
        {
            "query": "What are the treatment outcomes?",
            "intent": "qa",
            "topic": "treatment outcomes",
            "scope": "in_scope",
            "tool_hint": "insights_tool",
        },
        {
            "query": "What is the sentiment distribution?",
            "intent": "analytical",
            "topic": "sentiment",
            "scope": "in_scope",
            "tool_hint": "analytical_tool",
        },
    ],
    "reasoning": "Two distinct sub-questions requiring different tools"
}
```
 
### Example D: Out-of-scope question
**Query:** "What is the stock price of GSK?"
 
**LLM output (normalized):**
```python
{
    "query_type": "out_of_scope",
    "is_complex": False,
    "sub_queries": [{
        "query": "What is the stock price of GSK?",
        "intent": "qa",
        "topic": "stock price",
        "scope": "out_of_scope",
        "scope_reasoning": "Financial/stock queries are not related to patient data science",
        "tool_hint": "insights_tool",
    }],
    "reasoning": "Query is unrelated to pharmaceutical patient data"
}
```
 
---
 
## 8) Design patterns and key takeaways
 
| Pattern | Detail |
|---|---|
| **Single LLM call** | Classification + decomposition + scoping in one call saves ~2-3s latency |
| **Analytical passthrough** | Analytics queries bypass decomposition — the analytics planner handles complexity internally |
| **Hard cap at 3** | Prevents cost/latency explosion from over-decomposition |
| **Graceful fallback** | Any unexpected error → safe single-query insights default |
| **History-aware** | Conversation context enables correct analysis of follow-up queries |
| **Tool-hint auto-mapping** | Missing hints are inferred from intent, so downstream agents always have a tool recommendation |
 
---
 
## 9) Dependencies
 
| Import | Source | Purpose |
|---|---|---|
| `DecomposedQuery` | `apps.state.state` | Type reference (used by caller, not directly here) |
| `merge_source_histories` | `apps.memory_management.memory` | Build conversation context for LLM |
| `RuntimeContext` | `linked_services.runtime.runtime_context` | Access to LLM factory and logger |
| `AgentExecutionException`, `AgentException` | `utils.error_handling.error_handler` | Typed error raising |
| `ErrorCode` | `config.constants` | Standard error code constants |
| `RetryError` | `tenacity` | Detect retry exhaustion from LLM calls |
