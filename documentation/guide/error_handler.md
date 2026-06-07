# Error Handler — Exception Hierarchy & Decorators
 
## File
`utils/error_handling/error_handler.py` (436 lines)
 
## Purpose
Defines a structured exception hierarchy for the entire application and provides two decorators that standardise error handling in LangGraph nodes and agent functions.
 
---
 
## Exception Hierarchy
 
```
AgentException (base)
├── AgentExecutionException      — generic LLM/tool failure (carries ErrorCode)
├── DataFetchException           — database or external data retrieval failure
├── QueryProcessingException     — query parsing / classification failure
├── MemoryException              — conversation memory load/save failure
├── AnalyticsAgentException      — analytics pipeline base
│   ├── AnalyticsPlanningException   — plan generation failure
│   └── AnalyticsExecutionException  — code execution failure
├── UnsafeCodeError              — sandbox code safety violation
└── AsyncTimeoutException        — async operation exceeded deadline
```
 
All exceptions carry:
- `message: str` — human-readable description
- `error_code: ErrorCode` — enum value for programmatic branching (e.g. `LLM_RATE_LIMIT`, `LLM_API_ERROR`, `LLM_TIMEOUT`, `GUARDRAIL_ERROR`, `DATA_FETCH_ERROR`)
 
---
 
## Decorators
 
### `@handle_async_node_exception(node_name: str)`
**For**: LangGraph graph node functions.
 
Behaviour:
1. Catches any exception raised inside the node.
2. Logs the error with `node_name` context.
3. Takes a `_safe_state_snapshot` of the current state for diagnostics.
4. **Re-raises** the exception (graph execution halts).
 
Use when a node failure should abort the entire graph run.
 
### `@wrap_async_agent(agent_type: str)`
**For**: Agent entry-point functions (insights agent, web agent, compiler, etc.).
 
Behaviour:
1. Catches any exception.
2. Logs with `agent_type` context.
3. **Returns an error state dict** instead of raising — allows the orchestrator to continue with partial results from other agents.
 
Use when agent failure should be gracefully handled (e.g. parallel fan-out where one agent failing shouldn't kill the whole run).
 
---
 
## Helper: `_safe_state_snapshot(state)`
Creates a shallow copy of the LangGraph state dict for logging, truncating large values (response texts, embeddings) to avoid log bloat. Used internally by both decorators.
 
---
 
## Usage Pattern
 
```python
from utils.error_handling.error_handler import (
    handle_async_node_exception,
    wrap_async_agent,
    AgentExecutionException,
)
from config.constants import ErrorCode
 
# In a graph node:
@handle_async_node_exception("query_analysis")
async def analyze_query(state: dict) -> dict:
    ...
 
# In an agent:
@wrap_async_agent("insights_agent")
async def run_insights_agent(state: dict) -> dict:
    ...
 
# Raising typed exceptions:
raise AgentExecutionException(
    message="OpenAI rate limited",
    error_code=ErrorCode.LLM_RATE_LIMIT,
)
```
 
## Design Notes
- `ErrorCode` enum lives in `config/constants.py`, not in this file.
- The separation of "raise" vs "return error state" maps directly to LangGraph's execution model: nodes that are sequential must raise; nodes in parallel fan-out should return gracefully.
