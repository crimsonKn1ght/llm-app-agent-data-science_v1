# `orchestrator_workflow.py` — In-Depth Function-by-Function Description
 
This document explains every function in `apps/orchestrator/orchestrator_workflow.py` based on the code in that file.
 
---
 
## File-level role
 
`orchestrator_workflow.py` defines a LangGraph `StateGraph` that coordinates end-to-end request execution for the orchestrator. Its responsibilities are:
 
- decide whether to run insights, web, or both paths,
- classify and prepare internal sub-queries,
- route analytical requests to a direct tool path,
- route broader insights requests to the ReAct agent,
- run web search when needed,
- synchronize parallel branches,
- hand off combined results to the compiler.
 
The file is **orchestration logic only**; specialized work is delegated to imported components:
 
- `analyze_query` from `apps.orchestrator.query_analyzer`
- `analytical_tool` from `apps.tools.analytical_tool`
- `insights_react_agent` from `apps.agents.insights_react_agent`
- `web_agent.web_agent` from `apps.agents.web_agent`
- `compiler_agent.compiler_agent` from `apps.agents.compiler_agent`
 
---
 
## Key types used throughout (from `apps/state/state.py`)
 
Understanding these types is essential for reading the functions below.
 
### `GraphState` (TypedDict)
 
The shared state dictionary threaded through every node. Key fields:
 
| Field | Type | Purpose |
|---|---|---|
| `request` | `ChatRequest` | User query, data sources, filters, conversation IDs |
| `query_id` | `int` | Unique identifier for this query |
| `query_type` | `str` | `"analytical"` / `"insights"` / `"web"` / `"out_of_scope"` |
| `is_complex_query` | `bool` | Whether the query needs complex handling |
| `sub_queries` | `List[DecomposedQuery]` | In-scope decomposed sub-queries |
| `agent_results` | `Annotated[List[AgentResult], operator.add]` | Results from all agents. **Uses `operator.add` reducer** — results from different nodes are concatenated, not overwritten |
| `execution_path` | `Annotated[List[str], operator.add]` | Tracks which nodes executed (audit trail) |
| `expected_branches` | `List[str]` | Which branches must complete before compilation |
| `completed_branches` | `Annotated[List[str], operator.add]` | Which branches have finished |
| `runtime` | `RuntimeContext` | Logger, LLM factory, config |
| `source_insights_map` | `Dict[str, str]` | Per-source compiled text (for conversation memory) |
| `source_memory` | `Dict[str, Dict]` | Prior conversation history per source |
| `context_list` | `Dict[str, Dict[str, List[str]]]` | Retrieved doc IDs per sub-query per source |
| `error` | `ErrorInfo` | Error details if a node fails |
| `is_error_state` | `bool` | Whether the graph is in error state |
 
**Critical detail about `operator.add` reducers:** Fields annotated with `Annotated[List, operator.add]` (like `agent_results`, `execution_path`, `completed_branches`) use LangGraph's reducer pattern. When a node returns `{"agent_results": [...]}`, the returned list is **appended** to the existing list, not replaced. This is what enables parallel branches to both contribute results.
 
### `DecomposedQuery` (TypedDict)
 
```python
{
    "query_id": 1,
    "sub_query_id": 1,
    "query": "What are common side effects?",
    "intent": "qa",           # "qa" | "summary" | "analytical" | ...
    "topic": "side effects",
    "scope": "in_scope",      # "in_scope" | "out_of_scope"
    "scope_reasoning": "Query relates to patient data",
    "tool_hint": "insights_tool"  # "insights_tool" | "analytical_tool"
}
```
 
### `AgentResult` (TypedDict)
 
```python
{
    "query_id": 1,
    "sub_query_id": 1,
    "query": "What are common side effects?",
    "agent_type": "internal_insights",  # "internal_insights" | "web_search" | "orchestrator"
    "result": "Patients report fatigue...",
    "data_sources": ["Patient MVOC"],
    "tool_metadata": {"source": "Patient MVOC", "intent": "qa"},
    "suggested_questions": [{"id": "1", "question": "..."}],
    "status": "success"  # "success" | "error" | "out_of_scope"
}
```
 
### What is `handle_async_node_exception`?
 
A decorator from `utils/error_handling/error_handler.py` that wraps async graph nodes with standardized error handling:
 
1. Catches `AgentException` → logs error, records in `state["error"]`, sets `state["is_error_state"] = True`, re-raises as `AgentExecutionException`
2. Catches `asyncio.TimeoutError` → similar treatment with timeout-specific error code
3. Catches generic `Exception` → similar treatment with `UNEXPECTED_ERROR` code
 
Every major node (`query_analyzer_node`, `analytical_node`, `insights_react_node`, `web_search_node`, `compiler_node`) is wrapped with this decorator so errors are consistently captured in state and logged before propagation.
 
---
 
## Architecture diagram
 
```
router_node
    ├── insights_path → query_analyzer → type_router
    │       ├── "analytical" → analytical_node ──┐
    │       ├── "insights"  → insights_react    ─┤
    │       └── "compiler"  (all out-of-scope)  ─┤
    │                                            ▼
    │                                   branch_router → compiler
    ├── web_only → web_search → branch_router → compiler
    └── both → parallel_start
                ├── query_analyzer → type_router → (analytical | insights_react | compiler)
                └── web_search → branch_router → compiler
 
    compiler → END
```
 
---
 
## 1) `async def router_node(state: GraphState)`
 
### What it does
This is the **first executable node**. It inspects request flags and sets the branch plan for the rest of the graph.
 
### Inputs it uses
- `request.webSearch` — boolean: whether user requested web search
- `request.dataSources` — list: selected data sources (can be empty)
 
### Branching logic
 
| Condition | `expected_branches` | Meaning |
|---|---|---|
| `webSearch == False` | `["insights"]` | Internal data only |
| `webSearch == True` AND no `dataSources` | `["web"]` | Web search only |
| `webSearch == True` AND `dataSources` present | `["insights", "web"]` | Both paths in parallel |
 
### Output/state updates
```python
{
    "execution_path": ["router_node"],
    "expected_branches": ["insights"]  # or ["web"] or ["insights", "web"]
}
```
 
### Why it matters
`expected_branches` creates the execution contract. It's later compared against `completed_branches` by `branch_router` to decide when compilation can proceed.
 
---
 
## 2) `async def router(state: GraphState)`
 
### What it does
A **conditional-edge selector** immediately after `router_node`. Converts the list-based planning output into graph edge labels.
 
### Return values
 
| `expected_branches` | Returns | Routes to |
|---|---|---|
| `["insights"]` | `"insights_path"` | `query_analyzer` node |
| `["web"]` | `"web_only"` | `web_search` node |
| anything else | `"both"` | `parallel_start` node |
 
### Why it matters
`router` converts the list-based planning output from `router_node` into graph edge labels used by `add_conditional_edges(...)`. It's the bridge between routing logic and LangGraph's edge API.
 
---
 
## 3) `async def parallel_start_node(state: GraphState)`
 
### What it does
A lightweight fan-out node used only for the `"both"` route. Returns just `{"execution_path": ["parallel_start"]}`.
 
### Why it matters
LangGraph needs a single predecessor node to fan out from. This node provides a clean structural split point so LangGraph can dispatch to both `query_analyzer` and `web_search` in parallel from one shared predecessor.
 
Without this node, there would be no way to express "run these two nodes concurrently" in the graph structure.
 
---
 
## 4) `query_analyzer_node(state: GraphState)`
 
Decorated with `@handle_async_node_exception("query_analyzer")`.
 
### What it does
Performs a single LLM analysis pass (classification + decomposition + scope handling) and prepares normalized sub-query artifacts for downstream routing.
 
### External component used
- `analyze_query(...)` from `apps.orchestrator.query_analyzer` — does a single LLM call to classify, decompose, and scope-check the query.
 
### Main flow
1. Logs analysis start.
2. Calls `analyze_query(query, data_sources, state, runtime, ...)`.
3. Reads from analyzer result:
   - `query_type` (default `"insights"`)
   - `is_complex` (default `False`)
   - `sub_queries` list
4. Iterates through each returned sub-query and bifurcates:
 
**Out-of-scope sub-query** → immediately creates an `AgentResult`:
```python
{
    "agent_type": "orchestrator",
    "result": constants.OUT_OF_SCOPE_ERROR,
    "status": "out_of_scope",
    "tool_metadata": {"scope": "out_of_scope", "reasoning": "..."},
}
```
This result goes directly to the compiler — no agent processing needed.
 
**In-scope sub-query** → creates a `DecomposedQuery`:
```python
{
    "query_id": 1, "sub_query_id": 1,
    "query": "What are side effects?",
    "intent": "qa", "topic": "side effects",
    "scope": "in_scope", "tool_hint": "insights_tool",
}
```
 
### Error fallback behavior
If the analyzer fails (LLM error, parse error, etc.):
- Forces `query_type = "insights"`, `is_complex = False`
- Creates one fallback in-scope `DecomposedQuery` containing the full original query with `intent="qa"` and `tool_hint="insights_tool"`
- Returns no pre-built `agent_results`
 
This ensures the pipeline always has something to work with.
 
### Output/state updates
```python
{
    "execution_path": ["query_analyzer"],
    "query_type": "insights",        # or "analytical"
    "is_complex_query": False,
    "sub_queries": [DecomposedQuery, ...],   # only in-scope
    "agent_results": [AgentResult, ...],     # pre-populated out-of-scope results
}
```
 
### Why it matters
This function is the core preparation stage for the insights side. It ensures downstream nodes receive clean, structured state and that out-of-scope parts are preserved as explicit result records instead of being silently dropped.
 
---
 
## 5) `def type_router(state: GraphState) -> str`
 
### What it does
Synchronous conditional-edge selector after `query_analyzer_node`. Routes to the appropriate execution node.
 
### Routing logic
 
| Condition | Returns | Routes to |
|---|---|---|
| `sub_queries` is empty (all were out-of-scope) | `"compiler"` | Skip agents, compile what we have |
| `query_type == "analytical"` | `"analytical"` | Direct tool invocation |
| Anything else | `"insights"` | ReAct agent loop |
 
### Why it matters
It enforces a **performance-sensitive split**: simple analytical requests avoid full ReAct planning overhead (saving 1+ LLM calls and ~2-3 seconds).
 
---
 
## 6) `analytical_node(state: GraphState)`
 
Decorated with `@handle_async_node_exception("analytical_node")`.
 
### What it does
Executes analytical requests by calling `analytical_tool` directly (no ReAct loop, no planner) and converts tool outputs into the shared `AgentResult` schema.
 
### External component used
- `analytical_tool(...)` from `apps.tools.analytical_tool`
 
### Main flow
1. Calls `analytical_tool(query, data_sources, filters, runtime, ...)`.
2. Tool returns `{"source_results": [{source, answer, status, tool_metadata}, ...]}`.
3. For each source result, creates one `AgentResult` with `agent_type = "internal_insights"`.
4. If no source results returned, injects one synthetic error `AgentResult` to prevent empty downstream payloads.
 
### Why `agent_type = "internal_insights"` (not "analytical")
The compiler routes results based on `agent_type`. By using `"internal_insights"`, analytical results flow through the compiler's standard internal success/error buckets. The compiler then uses `tool_metadata.intent == "analytical"` to distinguish them for synthesis decisions.
 
### Output/state updates
```python
{
    "execution_path": ["analytical"],
    "completed_branches": ["insights"],   # marks insights branch as done
    "agent_results": [AgentResult, ...],
}
```
 
Note: `completed_branches = ["insights"]` is critical — it tells `branch_router` that the insights branch is complete, enabling compilation in parallel mode.
 
---
 
## 7) `insights_react_node(state: GraphState)`
 
Decorated with `@handle_async_node_exception("insights_react_node")`.
 
### What it does
Delegates insights execution to the ReAct agent loop and normalizes its return into graph state updates.
 
### External component used
- `insights_react_agent(state)` from `apps.agents.insights_react_agent` — see the insights_react_agent_detailed.md doc for full internals.
 
### Main flow
1. Logs start indicating ReAct execution.
2. Awaits `insights_react_agent(state)` — this runs the full fast-path / uniform / ReAct loop internally.
3. Logs completion.
4. Returns `agent_results` and marks insights branch complete.
 
### Output/state updates
```python
{
    "execution_path": ["insights_react"],
    "completed_branches": ["insights"],
    "agent_results": agent_output.get("agent_results", []),
}
```
 
---
 
## 8) `web_search_node(state: GraphState)`
 
Decorated with `@handle_async_node_exception("web_search_node")`.
 
### What it does
Runs the external web branch through the web agent.
 
### External component used
- `web_agent.web_agent(state)` from `apps.agents.web_agent`
 
### Output/state updates
```python
{
    "execution_path": ["web_search"],
    "completed_branches": ["web"],     # marks web branch as done
    "agent_results": agent_output.get("agent_results", []),
}
```
 
### Why it matters
This is the sole web branch execution node, critical for both web-only requests and mixed requests where web supplements insights results.
 
---
 
## 9) `def branch_router(state: GraphState)`
 
### What it does
Implements **branch synchronization** logic before compilation.
 
### How it works
```python
expected = set(state.get("expected_branches", []))   # e.g. {"insights", "web"}
completed = set(state.get("completed_branches", []))  # e.g. {"insights"}
 
if expected.issubset(completed):
    return "compiler"
else:
    return END
```
 
### Concrete synchronization example
 
**Parallel mode (`expected = ["insights", "web"]`):**
1. `insights_react_node` finishes → `completed_branches = ["insights"]`
2. `branch_router` called from insights → `{"insights"} ⊄ {"insights", "web"}` → returns `END` (waits)
3. `web_search_node` finishes → `completed_branches = ["insights", "web"]` (appended via reducer)
4. `branch_router` called from web → `{"insights", "web"} ⊆ {"insights", "web"}` → returns `"compiler"` ✓
 
**Single mode (`expected = ["insights"]`):**
1. `insights_react_node` finishes → `completed_branches = ["insights"]`
2. `branch_router` called → `{"insights"} ⊆ {"insights"}` → returns `"compiler"` immediately ✓
 
### Important nuance
Each agent node (`analytical`, `insights_react`, `web_search`) uses this router on its outgoing conditional edge. This means the branch_router is called **multiple times** — once per completing node — and only the last one (when all branches are done) actually routes to the compiler.
 
### Why it matters
It acts as a **fan-in barrier** for parallel mode while still supporting single-branch mode with the same mechanism. No separate synchronization code needed.
 
---
 
## 10) `compiler_node(state: GraphState)`
 
Decorated with `@handle_async_node_exception("compiler")`.
 
### What it does
Invokes compiler logic to merge and format all accumulated `agent_results` into the final response.
 
### External component used
- `compiler_agent.compiler_agent(state)` — see compiler_agent_detailed.md for full internals.
 
### Main flow
1. Logs compilation start.
2. Awaits compiler execution.
3. Overwrites `results["execution_path"] = ["compiler"]`.
4. Returns compiled state object.
 
### Why it matters
This is the terminal aggregation stage where branch outputs become one coherent API response payload with markdown formatting, redaction, and follow-up questions.
 
---
 
## 11) `def build_orchestrator_graph()`
 
### What it does
Constructs, wires, and compiles the entire LangGraph `StateGraph(GraphState)`.
 
### Node registration (7 nodes)
```python
graph.add_node("router_node", router_node)
graph.add_node("parallel_start", parallel_start_node)
graph.add_node("query_analyzer", query_analyzer_node)
graph.add_node("analytical", analytical_node)
graph.add_node("insights_react", insights_react_node)
graph.add_node("web_search", web_search_node)
graph.add_node("compiler", compiler_node)
```
 
### Entry point
`graph.set_entry_point("router_node")`
 
### Edge wiring
 
**1. Router → three paths:**
```python
router_node → router() → {
    "insights_path": "query_analyzer",
    "web_only": "web_search",
    "both": "parallel_start",
}
```
 
**2. Parallel fan-out (both path):**
```python
parallel_start → query_analyzer  (edge 1)
parallel_start → web_search      (edge 2)
```
These two edges run concurrently in LangGraph.
 
**3. Type routing after analyzer:**
```python
query_analyzer → type_router() → {
    "analytical": "analytical",
    "insights": "insights_react",
    "compiler": "compiler",        # all sub-queries were out-of-scope
}
```
 
**4. Branch synchronization from each agent:**
```python
analytical     → branch_router() → {"compiler": "compiler", END: END}
insights_react → branch_router() → {"compiler": "compiler", END: END}
web_search     → branch_router() → {"compiler": "compiler", END: END}
```
 
**5. Terminal edge:**
```python
compiler → END
```
 
### Return value
Returns `graph.compile()` — a compiled runnable graph object that can be invoked with initial state.
 
---
 
## Cross-function execution traces
 
### Insights-only request
```
router_node("insights") → query_analyzer_node → type_router
  → analytical_node OR insights_react_node
  → branch_router("compiler") → compiler_node → END
```
 
### Web-only request
```
router_node("web") → web_search_node → branch_router("compiler") → compiler_node → END
```
 
### Mixed request (insights + web)
```
router_node("both") → parallel_start_node
  ├→ query_analyzer_node → type_router → analytical/insights_react → branch_router(END)
  └→ web_search_node → branch_router("compiler") → compiler_node → END
```
(The second branch_router call to reach "compiler" is whichever finishes last)
 
### All out-of-scope
```
router_node("insights") → query_analyzer_node → type_router("compiler") → compiler_node → END
```
The compiler receives only the pre-built out-of-scope `AgentResult`s and renders a "Not Supported" response.
 
---
 
## Shared patterns implemented across functions
 
| Pattern | Where | Purpose |
|---|---|---|
| **Common logging envelope** | Every node | Extract `runtime.logger` + conversation IDs, log start/end |
| **State-delta returns** | Every node | Return partial dict updates; LangGraph merges via reducers |
| **Uniform result schema** | `analytical_node`, `insights_react_node`, `web_search_node` | Normalize all outputs to `AgentResult` before compilation |
| **Decorator-based resilience** | All major async nodes | `handle_async_node_exception` provides consistent error capture |
| **Barrier synchronization** | `expected_branches` + `completed_branches` | Coordination protocol between routing and compilation |
| **`operator.add` reducers** | `agent_results`, `execution_path`, `completed_branches` | Enable parallel branches to contribute results without overwriting |
 
---
 
## What the file accomplishes overall
 
`orchestrator_workflow.py` accomplishes three things simultaneously:
 
1. **Control-plane routing** (which branch(es) to execute),
2. **Execution strategy selection** (direct analytical tool vs ReAct agent vs web),
3. **Convergent completion** (only compile when required branches are complete).
 
In short, it is the orchestrator's executable workflow contract that aligns request intent with the right specialized components and guarantees a unified compiled response.
