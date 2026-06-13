# Architecture Overview

This document describes the current DS-side architecture implemented in this repository. It is based on the live code under `app/`, `runtime/`, `prompts/`, and `run.py`.

The project is named **LLM Chat Agent**. It is not currently a classic RAG system: there is no vector database, embedding index, document retrieval layer, or document chunk augmentation pipeline. The workflow is LLM-orchestrated, with optional web search when requested.

## High-Level Architecture

The application is an async FastAPI service backed by a LangGraph workflow. Requests enter through a streaming API route, are normalized into shared graph state, pass through classification and routing, then produce a streamed final answer.

```mermaid
flowchart LR
    Client["External client or frontend"] --> API["FastAPI API Layer"]
    API --> Stream["Streaming Layer<br/>asyncio.Queue + NDJSON"]
    API --> Entry["Orchestrator Entry<br/>orchestrate()"]

    Entry --> MemoryLoad["Memory Load<br/>conversation_store.load()"]
    Entry --> State["GraphState Init"]
    State --> Graph["LangGraph Workflow"]

    Graph --> Analyzer["Query Analyzer"]
    Analyzer --> Insights["Insights Node"]
    Analyzer --> Analytical["Analytical Node"]
    Analyzer --> Web["Web Search Node<br/>optional"]
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
| Application startup | `app/main.py`, `run.py` | Load env, configure logging, initialize runtime, configure CORS, start FastAPI |
| Health API | `app/api/health.py` | Liveness and runtime readiness endpoints |
| Chat API | `app/api/routes.py` | Streaming chat generation plus conversation history/search/detail/status |
| Orchestrator entry | `app/orchestrator/orchestrator_entry.py` | Create/normalize conversation ID, load memory, build `GraphState`, invoke LangGraph, save memory |
| Workflow graph | `app/orchestrator/orchestrator_workflow.py` | Define LangGraph nodes, conditional routing, parallel fan-out, final convergence |
| State model | `app/orchestrator/state.py` | Typed shared state and reducer behavior for graph updates |
| Nodes | `app/nodes/*.py` | Query analysis, answer generation, optional web search, final summary |
| Runtime | `runtime/*.py` | Anthropic client, prompt loading, graph compilation |
| Prompts | `prompts/prompts.yaml` | System prompts, temperatures, and token limits |
| Memory | `app/memory/conversation_store.py` | File-backed conversation history, compression storage, history listing, search |

## Request Lifecycle

1. A client sends `POST /api/chat/generate` with `user_query`, optional `conversation_id`, and optional `web_search`.
2. The route validates the request and creates an `asyncio.Queue` for stream events.
3. The route starts `orchestrate()` in a background task.
4. The route immediately returns a `StreamingResponse` that yields queue events as NDJSON.
5. The orchestrator uses the provided conversation UUID or generates a new one.
6. The orchestrator loads runtime dependencies and any existing conversation memory.
7. The orchestrator builds the initial `GraphState`.
8. The compiled LangGraph runs the request through router, analyzer, one or more answer nodes, and summary.
9. Web search is included only when `web_search=true`.
10. Nodes emit `progress` and `text` events while they work.
11. `summary_node` emits a terminal `final_response` event and closes the stream.
12. The orchestrator persists the user query and final response.
13. If conversation history is long enough, older turns are compressed into a summary.

## End-To-End Workflow

```mermaid
flowchart TD
    A["Client sends chat request"] --> B["app/api/routes.py<br/>chat_endpoint()"]
    B --> C["Create stream_queue"]
    C --> D["asyncio.create_task(run_pipeline())"]
    C --> E["StreamingResponse(event_generator())"]

    D --> F["orchestrate()"]
    F --> G{"conversation_id provided and valid?"}
    G -- "No" --> H["Generate UUID"]
    G -- "Yes" --> I["Use provided UUID"]
    H --> J["Set logging context"]
    I --> J

    J --> K["get_runtime()"]
    K --> L["conversation_store.load()"]
    L --> M["Build initial GraphState"]
    M --> N["runtime.compiled_graph.ainvoke(state)"]

    N --> O["router_node"]
    O --> P["query_analyzer_node"]
    P --> Q{"route_after_analysis()"}

    Q -- "insights" --> R["insights_node"]
    Q -- "analytical" --> S["analytical_node"]
    Q -- "web_search=true + insights" --> U1["parallel_insights_web"]
    Q -- "web_search=true + analytical" --> U2["parallel_analytical_web"]

    U1 --> R
    U1 --> T["web_search_node"]
    U2 --> S
    U2 --> T

    R --> V["summary_node"]
    S --> V
    T --> V

    V --> W["Emit final_response"]
    W --> X["Queue sentinel None"]
    X --> Y["Save conversation turn"]
    Y --> Z["Compress history when needed"]

    E --> AA["event_generator reads queue"]
    AA --> AB["Yield NDJSON events to client"]
```

## API Surface

### Generation

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/chat/generate` | Stream a new answer as NDJSON |

Request body:

```json
{
  "user_query": "Explain AI",
  "conversation_id": "optional-existing-uuid",
  "web_search": false
}
```

### Conversation Store

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/chat/history` | List recent saved conversations |
| `GET` | `/api/chat/search?q=term` | Search saved conversation messages |
| `GET` | `/api/chat/status?conversation_id=<uuid>` | Return whether a conversation exists and basic counts |
| `GET` | `/api/chat/{conversation_id}` | Return conversation history and compressed summary |

These endpoints are lightweight helpers over local JSON files. They are intended for frontend convenience, not as a multi-user authorization boundary.

### Health

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Basic liveness |
| `GET` | `/ready` | Runtime readiness |
| `GET` | `/api/health` | API-prefixed liveness alias |
| `GET` | `/api/ready` | API-prefixed readiness alias |

## LangGraph Workflow

The graph is built in `app/orchestrator/orchestrator_workflow.py`.

```mermaid
flowchart TD
    Start(["START"]) --> Router["router_node"]
    Router --> Analyzer["query_analyzer"]

    Analyzer --> Decision{"route_after_analysis"}

    Decision -- "insights" --> Insights["insights"]
    Decision -- "analytical" --> Analytical["analytical"]
    Decision -- "web_search=true + insights" --> ParallelIW["parallel_insights_web"]
    Decision -- "web_search=true + analytical" --> ParallelAW["parallel_analytical_web"]

    ParallelIW --> Insights
    ParallelIW --> Web["web_search"]
    ParallelAW --> Analytical
    ParallelAW --> Web

    Insights --> Summary
    Analytical --> Summary
    Web --> Summary

    Summary --> End(["END"])
```

## Routing Rules

`query_analyzer_node` asks the LLM to return structured JSON with:

- `query_type`: `insights` or `analytical`
- `is_complex`: whether the query has multiple parts
- `sub_queries`: up to 3 normalized sub-queries
- `tool_hint`: the node that should handle each sub-query

`route_after_analysis()` routes to the matching node or parallel branch:

| Condition | Route |
| --- | --- |
| All sub-queries use `insights` | `insights` |
| All sub-queries use `analytical` | `analytical` |
| `web_search=true` with insights | `parallel_insights_web`, then `insights` and `web_search` |
| `web_search=true` with analytical | `parallel_analytical_web`, then `analytical` and `web_search` |
| Mixed internal tool hints | Parallel internal branches when configured by the workflow |

Web search is request-owned, not analyzer-owned. The analyzer does not decide to use web search on its own.

## GraphState

`GraphState` is the shared state object passed between LangGraph nodes.

| Field | Purpose |
| --- | --- |
| `user_query` | Original user request |
| `conversation_id` | Current conversation identifier |
| `web_search` | Request flag controlling web search |
| `conversation_history` | Recent message history |
| `conversation_summary` | Compressed older history |
| `query_type` | LLM-classified query category |
| `is_complex` | Whether the analyzer decomposed the query |
| `sub_queries` | Normalized in-scope sub-queries |
| `agent_results` | Results from answer nodes |
| `source_contents` | Rendered content used by summary |
| `final_response` | Complete final answer |
| `stream_queue` | Queue used to stream events to the API layer |
| `runtime` | LLM client, prompt loader, compiled graph |
| `execution_path` | Audit trail of executed graph nodes |

## Node Responsibilities

### `router_node`

Starts the graph, emits initial progress, and records execution path.

### `query_analyzer_node`

Builds a prompt using the current query plus conversation summary/history. It calls the LLM with the `query_analyzer` prompt, parses JSON output, normalizes the result, and sets expected branches.

If analyzer parsing or generation fails, it falls back to a single internal sub-query: `analytical` for quantitative patterns, otherwise `insights`.

### `insights_node`

Handles qualitative or explanatory questions. It streams a `## Insights` section. For multiple matching sub-queries, it generates answers concurrently and synthesizes them with the `source_synthesis` prompt.

### `analytical_node`

Handles quantitative or structured reasoning questions with LLM-based analytical reasoning. It does not execute database-backed analytics or code.

### `web_search_node`

Runs only when the request sets `web_search=true`. It searches with DuckDuckGo first, optionally falls back to Brave Search when `BRAVE_SEARCH_API_KEY` is configured, formats results for the LLM, and streams a `## Web Search` answer.

### `summary_node`

Creates the final response. It synthesizes source sections with the `final_summary` prompt, emits `## Summary`, assembles the final response, sends `final_response`, and closes the stream.

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

| Event | Produced by | Meaning |
| --- | --- | --- |
| `progress` | Nodes via `emit_progress()` | Status update |
| `text` | Nodes via `emit_text()` | Streamed markdown content |
| `error` | Orchestrator error handler | User-facing failure |
| `final_response` | `summary_node` | Complete response text |

## Runtime And Prompts

Runtime is initialized during FastAPI startup in `app/main.py`.

`runtime/runtime_config.py` creates:

- `LLMClient`, an async Anthropic wrapper.
- `PromptLoader`, which loads `prompts/prompts.yaml`.
- `compiled_graph`, created by `build_orchestrator_graph()`.

Prompt names currently used by nodes:

| Prompt | Used by |
| --- | --- |
| `query_analyzer` | `query_analyzer_node` |
| `insights` | `insights_node` |
| `analytical` | `analytical_node` |
| `web_search` | `web_search_node` |
| `source_synthesis` | Multi-sub-query synthesis |
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
| `LLM_TIMEOUT_SECONDS` | No | `60` | Timeout for each LLM call attempt |
| `LLM_MAX_RETRIES` | No | `2` | Retry count after LLM failures |
| `CONVERSATIONS_DIR` | No | `conversations` | Conversation JSON storage directory |
| `MAX_HISTORY_TURNS` | No | `20` | Compress once history exceeds this many messages |
| `KEEP_RECENT_TURNS` | No | `10` | Recent messages retained after compression |
| `BRAVE_SEARCH_API_KEY` | No | Empty | Optional fallback for web search |
| `WEB_SEARCH_TIMEOUT_SECONDS` | No | `15` | Timeout for each web search attempt |
| `WEB_SEARCH_MAX_RETRIES` | No | `1` | Retry count after web search failures |
| `URL_ALLOWED_ORIGINS` | No | Local Vite origins | Comma-separated CORS origins for separately hosted frontends |

## Implementation Notes

- The route streams results while the graph is still running; clients should process NDJSON incrementally.
- The graph always goes through `query_analyzer_node`; internal routing is based on analyzer output.
- Web search is controlled only by the `web_search` request field.
- `summary_node` is the final convergence point for single-node and parallel paths.
- Conversation saving happens after response generation and only when a final response exists.
- Conversation history endpoints are local-file helpers and do not provide user-level access control.
