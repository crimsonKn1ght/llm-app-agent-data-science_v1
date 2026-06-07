# Architecture Overview

This document describes the current architecture implemented in this repository. It is based on the live code under `app/`, `runtime/`, `prompts/`, and `run.py`.

Some older documentation files in this folder describe a larger architecture with components that are not present in this codebase. Use this file as the source of truth for the current request flow.

## High-Level Architecture

The application is an async FastAPI service backed by a LangGraph workflow. Requests enter through a streaming API route, are normalized into a shared graph state, pass through classification and routing, then produce a final streamed response.

```mermaid
flowchart LR
    Client["Client / UI"] --> API["FastAPI API Layer"]
    API --> Stream["Streaming Layer<br/>asyncio.Queue + NDJSON"]
    API --> Entry["Orchestrator Entry<br/>orchestrate()"]

    Entry --> MemoryLoad["Memory Load<br/>conversation_store.load()"]
    Entry --> State["GraphState Init"]
    State --> Graph["LangGraph Workflow"]

    Graph --> Analyzer["Query Analyzer"]
    Analyzer --> Insights["Insights Node"]
    Analyzer --> Analytical["Analytical Node"]
    Analyzer --> Web["Web Search Node"]
    Analyzer --> Summary["Summary Node"]

    Insights --> Summary
    Analytical --> Summary
    Web --> Summary

    Summary --> Stream
    Summary --> MemorySave["Memory Save + Compression"]
    Stream --> Client
```

## Main Layers

| Layer | Files | Responsibility |
| --- | --- | --- |
| Application startup | `app/main.py`, `run.py` | Load environment, configure logging, initialize runtime, start FastAPI |
| API layer | `app/api/routes.py` | Accept chat requests, create streaming queue, return NDJSON stream |
| Orchestrator entry | `app/orchestrator/orchestrator_entry.py` | Create conversation ID, load memory, build `GraphState`, invoke LangGraph, save memory |
| Workflow graph | `app/orchestrator/orchestrator_workflow.py` | Define LangGraph nodes, conditional routing, parallel fan-out, final convergence |
| State model | `app/orchestrator/state.py` | Typed shared state and reducer behavior for graph updates |
| Nodes | `app/nodes/*.py` | Query analysis, answer generation, web search, final summary |
| Runtime | `runtime/*.py` | Anthropic client, prompt loading, graph compilation |
| Prompts | `prompts/prompts.yaml` | System prompts, temperatures, and token limits |
| Memory | `app/memory/conversation_store.py` | File-backed conversation history and summarization |

## Request Lifecycle

1. A client sends `POST /api/chat/generate` with `user_query` and optional `conversation_id`.
2. The route creates an `asyncio.Queue` for stream events.
3. The route starts `orchestrate()` in a background task.
4. The route immediately returns a `StreamingResponse` that yields queue events as NDJSON.
5. The orchestrator creates a conversation ID if one was not supplied.
6. The orchestrator loads the runtime and any existing conversation memory.
7. The orchestrator builds the initial `GraphState`.
8. The compiled LangGraph runs the request through router, analyzer, one or more answer nodes, and summary.
9. Nodes emit `progress` and `text` events while they work.
10. `summary_node` emits a `final_response` event and closes the stream.
11. The orchestrator persists the user query and final response.
12. If the conversation is long enough, older turns are compressed into a summary.

## End-to-End Workflow

```mermaid
flowchart TD
    A["Client sends chat request"] --> B["app/api/routes.py<br/>chat_endpoint()"]
    B --> C["Create stream_queue"]
    C --> D["asyncio.create_task(run_pipeline())"]
    C --> E["StreamingResponse(event_generator())"]

    D --> F["orchestrate()"]
    F --> G{"conversation_id provided?"}
    G -- "No" --> H["Generate UUID"]
    G -- "Yes" --> I["Use provided ID"]
    H --> J["Set logging context"]
    I --> J

    J --> K["get_runtime()"]
    K --> L["conversation_store.load()"]
    L --> M["Build initial GraphState"]
    M --> N["runtime.compiled_graph.ainvoke(state)"]

    N --> O["router_node<br/>emit Started pipeline"]
    O --> P["query_analyzer_node<br/>LLM classify/decompose/scope-check"]
    P --> Q{"route_after_analysis()"}

    Q -- "tool_hint = insights" --> R["insights_node"]
    Q -- "tool_hint = analytical" --> S["analytical_node"]
    Q -- "tool_hint = web_search" --> T["web_search_node"]
    Q -- "multiple tool_hints" --> U["parallel_start"]
    Q -- "only out-of-scope" --> V["summary_node"]

    U --> R
    U --> S
    U --> T

    R --> V
    S --> V
    T --> V

    V --> W["Emit final_response"]
    W --> X["Queue sentinel None"]
    X --> Y["Save conversation turn"]
    Y --> Z["Compress history when needed"]

    E --> AA["event_generator reads queue"]
    AA --> AB["Yield NDJSON events to client"]
```

## LangGraph Workflow

The graph is built in `app/orchestrator/orchestrator_workflow.py`.

```mermaid
flowchart TD
    Start(["START"]) --> Router["router_node"]
    Router --> Analyzer["query_analyzer"]

    Analyzer --> Decision{"route_after_analysis"}

    Decision -- "insights" --> Insights["insights"]
    Decision -- "analytical" --> Analytical["analytical"]
    Decision -- "web_search" --> Web["web_search"]
    Decision -- "parallel" --> Parallel["parallel_start"]
    Decision -- "summary" --> Summary["summary"]

    Parallel --> Insights
    Parallel --> Analytical
    Parallel --> Web

    Insights --> Summary
    Analytical --> Summary
    Web --> Summary

    Summary --> End(["END"])
```

### Routing Rules

`query_analyzer_node` asks the LLM to return structured JSON with:

- `query_type`: `insights`, `analytical`, `web_search`, or `out_of_scope`
- `is_complex`: whether the query has multiple parts
- `sub_queries`: up to 3 normalized sub-queries
- `tool_hint`: the node that should handle each sub-query

`route_after_analysis()` then routes as follows:

| Condition | Route |
| --- | --- |
| No in-scope sub-queries | `summary` |
| All sub-queries use `insights` | `insights` |
| All sub-queries use `analytical` | `analytical` |
| All sub-queries use `web_search` | `web_search` |
| Mixed tool hints | `parallel_start`, then all three answer nodes run |

In the parallel route, each answer node filters `state["sub_queries"]` for its own `tool_hint`. Nodes with no matching sub-queries return empty results and complete quickly.

## GraphState

`GraphState` is the shared state object passed between LangGraph nodes.

Important fields:

| Field | Purpose |
| --- | --- |
| `user_query` | Original user request |
| `conversation_id` | Current conversation identifier |
| `conversation_history` | Recent message history |
| `conversation_summary` | Compressed older history |
| `query_type` | LLM-classified query category |
| `is_complex` | Whether the analyzer decomposed the query |
| `sub_queries` | Normalized in-scope sub-queries |
| `agent_results` | Results from answer nodes and out-of-scope handling |
| `source_contents` | Per-source rendered content used by summary |
| `final_response` | Complete final answer |
| `stream_queue` | Queue used to stream events to the API layer |
| `runtime` | LLM client, prompt loader, compiled graph |
| `execution_path` | Audit trail of executed graph nodes |

Reducer behavior:

- `agent_results` uses `operator.add`, so branch results are appended.
- `execution_path` uses `operator.add`, so node names accumulate.
- `completed_branches` uses `operator.add`.
- `source_contents` uses dictionary merge, so each source contributes its text.

## Node Responsibilities

### `router_node`

Starts the graph, emits a `"Started pipeline"` progress event, and records execution path.

### `query_analyzer_node`

Builds a prompt using the current query plus conversation summary/history. It calls the LLM with the `query_analyzer` prompt, parses JSON output, normalizes the result, filters out-of-scope sub-queries, and sets `expected_branches`.

If analyzer parsing or generation fails, it falls back to a single in-scope `insights` sub-query.

### `insights_node`

Handles qualitative or explanatory questions. It streams a `## Insights` section. For one matching sub-query it streams the LLM answer directly. For multiple matching sub-queries it generates answers concurrently and synthesizes them with the `source_synthesis` prompt.

### `analytical_node`

Handles quantitative or structured reasoning questions. Its mechanics mirror `insights_node`, but it uses the `analytical` prompt and streams a `## Analysis` section.

### `web_search_node`

Handles current or web-dependent questions. It searches with DuckDuckGo first, optionally falls back to Brave Search if `BRAVE_SEARCH_API_KEY` is configured, formats results for the LLM, and streams a `## Web Search` answer.

### `summary_node`

Creates the final response. If all useful work was out-of-scope, it returns the out-of-scope message directly. Otherwise, it synthesizes all `source_contents` with the `final_summary` prompt, emits `## Summary`, then assembles the final response as summary plus source sections.

It also emits the terminal `final_response` event and pushes `None` into the queue so the API stream can close.

## Streaming Contract

The API returns `application/x-ndjson`. Each line is one JSON object.

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI Route
    participant Q as stream_queue
    participant G as LangGraph

    C->>API: POST /api/chat/generate
    API->>G: start orchestrate() task
    API-->>C: open NDJSON stream
    G->>Q: progress event
    Q-->>API: event
    API-->>C: {"type":"progress",...}
    G->>Q: text chunks
    Q-->>API: event
    API-->>C: {"type":"text",...}
    G->>Q: final_response
    G->>Q: None
    Q-->>API: terminal event
    API-->>C: {"type":"final_response",...}
```

Event types:

| Event | Produced by | Meaning |
| --- | --- | --- |
| `progress` | Nodes via `emit_progress()` | Status update |
| `text` | Nodes via `emit_text()` | Streamed markdown content |
| `error` | Orchestrator error handler | User-facing failure message |
| `final_response` | `summary_node` | Complete response text |

## Runtime and Prompts

Runtime is initialized during FastAPI startup in `app/main.py`.

`runtime/runtime_config.py` creates:

- `LLMClient`, an async Anthropic wrapper.
- `PromptLoader`, which loads `prompts/prompts.yaml`.
- `compiled_graph`, created by `build_orchestrator_graph()`.

The active model defaults to `claude-haiku-4-5-20251001` unless `CLAUDE_MODEL` is set.

Prompt names currently used by nodes:

| Prompt | Used by |
| --- | --- |
| `query_analyzer` | `query_analyzer_node` |
| `insights` | `insights_node` |
| `analytical` | `analytical_node` |
| `web_search` | `web_search_node` |
| `source_synthesis` | Multi-sub-query node synthesis |
| `final_summary` | `summary_node` |
| `conversation_summary` | Memory compression |

## Conversation Memory

Memory is file-backed and implemented in `app/memory/conversation_store.py`.

By default, conversation files are written under `conversations/`:

```text
conversations/
  <conversation_id>.json
```

Each file contains:

```json
{
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "summary": "..."
}
```

At the beginning of a request, memory is loaded and attached to `GraphState`.

At the end of a successful request, the new user/assistant turn is appended. If history length exceeds `MAX_HISTORY_TURNS`, older turns are summarized and the latest `KEEP_RECENT_TURNS` are retained.

## Environment Variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | None | Anthropic API key used by `LLMClient` |
| `CLAUDE_MODEL` | No | `claude-haiku-4-5-20251001` | Model name |
| `CONVERSATIONS_DIR` | No | `conversations` | Conversation JSON storage directory |
| `MAX_HISTORY_TURNS` | No | `20` | Compress once history exceeds this many messages |
| `KEEP_RECENT_TURNS` | No | `10` | Recent messages retained after compression |
| `BRAVE_SEARCH_API_KEY` | No | Empty | Optional fallback for web search |

## Important Implementation Notes

- The route streams results while the graph is still running; clients should process NDJSON incrementally.
- The graph always goes through `query_analyzer_node`; routing is based on the analyzer output.
- Mixed requests fan out to all three answer nodes, but each node processes only matching sub-queries.
- `summary_node` is the final convergence point for both single-node and parallel paths.
- Conversation saving happens in `finally`, but only if a final response was produced.
- Web search uses network-backed providers and can return limited results if those providers fail.
