# `state.py` — In-Depth Component Understanding
 
This document explains the internal design and behavior of:
 
- **File:** `apps/state/state.py`
- **Primary role:** Define the shared state types (`GraphState`, `AgentResult`, `DecomposedQuery`, `ErrorInfo`) used throughout the LangGraph orchestrator. This file is the **schema backbone** — every node reads from and writes to fields defined here.
 
---
 
## 1) Why this component exists
 
LangGraph operates on a shared state dictionary that flows through every node in the graph. `state.py` defines:
 
- The **shape** of that dictionary (`GraphState`)
- The **reducer semantics** for parallel branches (via `Annotated[List, operator.add]`)
- The **intermediate types** that nodes produce and consume (`DecomposedQuery`, `AgentResult`)
- The **error schema** (`ErrorInfo`)
 
Without this file, nodes would have no contract about what fields exist or how parallel writes merge. It's the single source of truth for the orchestrator's data model.
 
---
 
## 2) Type definitions
 
### `DecomposedQuery` (TypedDict)
 
Represents one sub-query produced by the query analyzer. This is the input format for downstream agents.
 
```python
class DecomposedQuery(TypedDict):
    query_id: int              # Parent query identifier
    sub_query_id: int          # Unique sub-query identifier (1-based)
    query: str                 # The sub-query text
    intent: str                # "qa" | "summary" | "analytical" | "trend_analysis" | "comparative"
    topic: Optional[str]       # Topic label (e.g., "side effects")
    scope: Optional[str]       # "in_scope" | "out_of_scope"
    scope_reasoning: Optional[str]  # Why this scope was assigned
    tool_hint: Optional[str]   # "insights_tool" | "analytical_tool"
```
 
**Produced by:** `query_analyzer_node` (in `orchestrator_workflow.py`)  
**Consumed by:** `insights_react_agent`, `analytical_node`
 
**Key design notes:**
- `tool_hint` guides the ReAct agent's execution path selection — it's a recommendation, not a hard constraint.
- `sub_query_id` is the primary tracking key used by the `Scratchpad` in the insights ReAct agent for completion tracking.
- Only **in-scope** sub-queries are placed in `state["sub_queries"]`; out-of-scope ones become immediate `AgentResult`s.
 
---
 
### `AgentResult` (TypedDict)
 
The universal output format from all agent nodes. The compiler receives a list of these and renders the final response.
 
```python
class AgentResult(TypedDict):
    query_id: int                          # Parent query identifier
    sub_query_id: int                      # Which sub-query this answers
    query: Optional[str]                   # The query text that was answered
    agent_type: str                        # "internal_insights" | "web_search" | "orchestrator"
    result: str                            # The actual answer text (or error message)
    data_sources: List[str]                # Sources used (e.g., ["Patient MVOC"])
    tool_metadata: Dict[str, Any]          # Arbitrary metadata for compiler routing
    suggested_questions: List[Dict[str, str]]  # Follow-up suggestions
    status: Literal["error", "success", "out_of_scope"]
```
 
**Produced by:** `analytical_node`, `insights_react_agent`, `web_search_node`, `query_analyzer_node` (for out-of-scope)  
**Consumed by:** `compiler_agent`
 
**Key design notes:**
- `agent_type` controls which compiler bucket the result falls into:
  - `"internal_insights"` → internal success/error buckets (both insights and analytics use this)
  - `"web_search"` → web section
  - `"orchestrator"` → out-of-scope section
- `tool_metadata.intent` distinguishes analytics from insights within `"internal_insights"` (value `"analytical"` triggers analytics-specific compiler behavior)
- `tool_metadata` also carries error typing: `error_type`, `error`, `source`, etc.
- `suggested_questions` is populated by the last result in a batch (to avoid duplicates in the compiler)
 
---
 
### `ErrorInfo` (TypedDict, total=False)
 
Structured error metadata stored in `state["error"]` when a node fails.
 
```python
class ErrorInfo(TypedDict, total=False):
    has_error: bool              # Whether an error occurred
    node: Optional[str]          # Which node failed (e.g., "insights_react_node")
    error_code: Optional[str]    # ErrorCode constant (e.g., "LLM_API_ERROR")
    error_message: Optional[str] # Human-readable error description
    traceback: Optional[str]     # Full traceback string for debugging
```
 
**Produced by:** `@handle_async_node_exception` decorator  
**Consumed by:** `orchestrator_entry.py` (for error response construction) and `memory_saver` (for error metadata persistence)
 
`total=False` means all fields are optional — the dict can be `{"has_error": False}` in the success case.
 
---
 
### `GraphState` (TypedDict)
 
The main state dictionary threaded through every LangGraph node. This is the core contract of the system.
 
```python
class GraphState(TypedDict):
    # ─── Request & Runtime ───
    request: ChatRequest              # Normalized API request
    runtime: RuntimeContext           # Logger, LLM factory, DB, storage
    query_id: int                     # Unique identifier for this query
    timestamp: str                    # ISO timestamp of request
 
    # ─── Query Analysis Output ───
    query_type: str                   # "analytical" | "insights" | "web" | "out_of_scope"
    is_complex_query: bool            # Whether query needs complex handling
    sub_queries: List[DecomposedQuery]  # In-scope decomposed sub-queries
 
    # ─── Agent Execution ───
    agents_to_invoke: List[str]       # (Legacy/unused) planned agent list
    agent_results: Annotated[List[AgentResult], operator.add]  # ← REDUCER
 
    # ─── Compilation Output ───
    compiled_response: ResponseContent  # Structured {summary, sections}
    final_response: ChatResponse        # Final API response payload
 
    # ─── Error Tracking ───
    error: ErrorInfo                  # Error details if any node fails
    is_error_state: bool              # Quick check for error state
 
    # ─── Execution Tracking ───
    execution_path: Annotated[List[str], operator.add]  # ← REDUCER
 
    # ─── Routing & Synchronization ───
    only_web: bool                    # True when webSearch=True and no dataSources
    expected_branches: List[str]      # ["insights"], ["web"], or ["insights", "web"]
    completed_branches: Annotated[List[str], operator.add]  # ← REDUCER
 
    # ─── Memory & Context ───
    source_memory: Dict[str, Dict[str, Any]]       # Per-source conversation history
    source_insights_map: Dict[str, str]            # Per-source compiled text (for DB)
    context_list: Dict[str, Dict[str, List[str]]]  # Retrieved doc IDs per sub-query per source
```
 
---
 
## 3) Reducer fields (`operator.add`)
 
Three fields use LangGraph's **reducer pattern** via `Annotated[List, operator.add]`:
 
| Field | Reducer | Effect |
|---|---|---|
| `agent_results` | `operator.add` | When a node returns `{"agent_results": [...]}`, the list is **appended** to the existing list (not replaced) |
| `execution_path` | `operator.add` | Each node appends its name; the final path shows the complete execution trace |
| `completed_branches` | `operator.add` | Each branch appends its name when complete; `branch_router` checks if all expected are done |
 
**Why this matters:**  
In parallel mode (insights + web), both branches independently produce `agent_results`. Without `operator.add`, the second branch to finish would overwrite the first's results. With the reducer, both lists are concatenated.
 
**Example:**
```python
# insights_react_node returns:
{"agent_results": [result_1, result_2]}
 
# web_search_node returns:
{"agent_results": [result_3]}
 
# Final state after both complete:
state["agent_results"] = [result_1, result_2, result_3]
```
 
---
 
## 4) Related data contracts
 
### `ChatRequest` (Pydantic BaseModel, from `utils/data_contracts/request_contracts.py`)
 
```python
class ChatRequest(BaseModel):
    parentConversationId: Optional[str]     # Conversation thread ID
    conversationID: Optional[str]           # This turn's ID
    sequence_number: int = 1                # Turn number in conversation
    userQuery: str                          # Original user question (1-5000 chars)
    rephrasedQuery: Optional[str]           # Frontend pre-rephrased version
    persona: Optional[str] = "medical"      # User persona
    mudid: Optional[str]                    # User identifier
    filters: Optional[Filters]             # Disease area, geography, time, etc.
    dataSources: List[str]                  # Selected data sources
    webSearch: bool = False                 # Whether web search requested
    is_refinement: bool = False             # Whether this is a follow-up refinement
```
 
### `ResponseContent` (TypedDict, from `utils/data_contracts/response_contracts.py`)
 
```python
class ResponseContent(TypedDict):
    summary: str                            # Executive summary text
    sections: List[ResponseSection]         # [{heading, content}, ...]
```
 
### `ChatResponse` (TypedDict, from `utils/data_contracts/response_contracts.py`)
 
```python
class ChatResponse(TypedDict):
    parentConversationId: Optional[str]
    conversationID: str
    sequence_number: int
    responseContent: str                    # Final rendered markdown
    exploreFurther: List[ExploreFurther]    # Follow-up question suggestions
    timestamp: str
```
 
---
 
## 5) State lifecycle
 
### Initial state (set by `orchestrator_entry.py`)
```python
{
    "request": ChatRequest(...),
    "runtime": RuntimeContext(...),
    "query_id": 1,
    "timestamp": "2025-05-30T...",
    "query_type": "",
    "is_complex_query": False,
    "sub_queries": [],
    "agent_results": [],
    "compiled_response": {"summary": "", "sections": []},
    "final_response": {...},
    "error": {"has_error": False},
    "is_error_state": False,
    "source_memory": {},  # Populated by memory_loader
    "context_list": {},
    "execution_path": [],
    "only_web": False,
}
```
 
### After memory loading
```python
state["source_memory"] = {
    "Patient MVOC": {"summary": "...", "history": [...]},
    "Social Listening": {"summary": "...", "history": [...]},
}
```
 
### After query analyzer
```python
state["query_type"] = "insights"
state["is_complex_query"] = True
state["sub_queries"] = [DecomposedQuery(...), ...]
state["agent_results"] = [AgentResult(...)]  # out-of-scope results pre-built
state["execution_path"] = ["router_node", "query_analyzer"]
```
 
### After agent execution
```python
state["agent_results"] = [...]  # Accumulated from all branches via reducer
state["completed_branches"] = ["insights", "web"]
state["execution_path"] = ["router_node", "query_analyzer", "insights_react", "web_search"]
```
 
### After compilation
```python
state["compiled_response"] = {"summary": "...", "sections": [...]}
state["final_response"]["responseContent"] = "## Executive Summary\n..."
state["final_response"]["exploreFurther"] = [{"id": "1", "question": "..."}]
state["source_insights_map"] = {"Patient MVOC": "compiled text...", ...}
state["execution_path"] = [..., "compiler"]
```
 
---
 
## 6) Key design decisions
 
| Decision | Rationale |
|---|---|
| **TypedDict (not Pydantic)** for GraphState | LangGraph requires dict-based state; TypedDict gives type checking without runtime overhead |
| **`operator.add` reducers** | Enable parallel branches to contribute results without overwriting |
| **`ErrorInfo` with `total=False`** | Most fields are only present during errors; keeps success-path state clean |
| **`source_memory` as Dict[str, Dict]`** | Per-source isolation prevents cross-source context contamination |
| **`context_list` nested Dict** | Enables per-sub-query, per-source document traceability |
| **`only_web` as explicit field** | Avoids re-computing `webSearch and no dataSources` in multiple nodes |
 
---
 
## 7) How nodes interact with state
 
| Node | Reads | Writes |
|---|---|---|
| `router_node` | `request.webSearch`, `request.dataSources` | `expected_branches`, `execution_path` |
| `query_analyzer_node` | `request.userQuery`, `source_memory` | `query_type`, `is_complex_query`, `sub_queries`, `agent_results` |
| `analytical_node` | `sub_queries`, `request.filters`, `request.dataSources` | `agent_results`, `completed_branches`, `execution_path` |
| `insights_react_node` | `sub_queries`, `source_memory`, `is_complex_query` | `agent_results`, `completed_branches`, `execution_path`, `context_list` |
| `web_search_node` | `request`, `source_memory`, `only_web` | `agent_results`, `completed_branches`, `execution_path` |
| `compiler_node` | `agent_results`, `request` | `compiled_response`, `final_response`, `source_insights_map` |
