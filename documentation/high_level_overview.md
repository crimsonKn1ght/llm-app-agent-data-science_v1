# Patient+ Orchestrator — High-Level Codebase Overview
 
This document provides a bird's-eye view of the entire codebase, explaining how all components connect and the request lifecycle from API entry to final response.
 
> For deep dives into individual components, see the files in `understand_component_docs/`.
 
---
 
## 1) What This System Does
 
**Patient+** is a GenAI-powered conversational system for pharmaceutical data science. Users ask natural-language questions about patient feedback, HCP opinions, clinical publications, and social listening data. The system:
 
- Retrieves relevant records from Azure AI Search indexes
- Answers qualitative questions (insights/QA/summarization) via LLM
- Answers quantitative questions (counts, trends, sentiment) via LLM-generated pandas code
- Optionally searches the web via Google Gemini with grounding
- Compiles all results into a coherent markdown response with citations, PII redaction, and follow-up questions
- Maintains multi-turn conversation memory per data source
 
---
 
## 2) Architecture at a Glance
 
```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Entry Point                            │
│                    main.py / fastapi_v1.py / local_app_fastapi.py       │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     Orchestrator Entry                                   │
│                 apps/orchestrator/orchestrator_entry.py                  │
│         (builds LangGraph, runs it, wraps with memory load/save)        │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     ┌──────────────┐  ┌─────────────┐  ┌─────────────┐
     │ Memory Loader │  │ Orchestrator│  │ Memory Saver│
     │   (before)    │  │  Workflow   │  │  (after)    │
     └──────────────┘  │  (LangGraph)│  └─────────────┘
                       └──────┬──────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   ┌─────────┐         ┌───────────┐         ┌──────────┐
   │ Internal │         │    Web    │         │ Compiler │
   │  Path    │         │   Agent   │         │  Agent   │
   │          │         │ (Gemini)  │         │          │
   └────┬─────┘         └───────────┘         └──────────┘
        │
   ┌────┴─────────────────┐
   ▼                      ▼
┌──────────────┐   ┌──────────────────┐
│ Query        │   │ Execution Agents │
│ Analyzer     │   │                  │
│ (classify +  │   │ • Insights ReAct │
│  decompose)  │   │ • Analytics      │
└──────────────┘   └────────┬─────────┘
                            │
                   ┌────────┴────────┐
                   ▼                 ▼
            ┌────────────┐    ┌────────────┐
            │ Insights   │    │ Analytics  │
            │ Tool       │    │ Pipeline   │
            │ (QA/summ.) │    │ (Plan→Exec │
            └─────┬──────┘    │ →Narrate   │
                  │           │ →Reflect)  │
                  ▼           └─────┬──────┘
            ┌────────────┐          │
            │ Retrieval  │◄─────────┘
            │ Layer      │
            │ (Azure AI  │
            │  Search)   │
            └────────────┘
```
 
---
 
## 3) Key Directories and Their Roles
 
| Directory | Role |
|---|---|
| **`apps/orchestrator/`** | LangGraph workflow definition, query analysis, routing logic |
| **`apps/agents/`** | Specialized agents: insights ReAct agent, compiler, web agent, analytics agent |
| **`apps/tools/`** | Tool implementations called by agents (insights_tool, analytical_tool) |
| **`apps/tools/analytics_tools/`** | Individual analytics tools (fetch_records, run_analysis_code, generate_chart, etc.) |
| **`apps/retrieval/`** | Azure AI Search query execution and post-processing |
| **`apps/memory_management/`** | Conversation persistence, per-source history, LLM-based summarization |
| **`apps/guardrails/`** | PII redaction (person name removal via LLM) |
| **`apps/state/`** | Shared state types (`GraphState`, `AgentResult`, etc.) |
| **`config/`** | Constants, data source definitions, error message templates |
| **`linked_services/`** | Infrastructure connectors (database, Key Vault, LLM token management, retrieval config, storage) |
| **`prompts/`** | LLM prompt templates loaded by `prompt_loader.py` |
| **`utils/`** | Cross-cutting utilities: data contracts, error handling, logging, model clients |
| **`analytics-executor/`** | Separate microservice for sandboxed code execution |
 
---
 
## 4) Request Lifecycle (End-to-End)
 
### Phase 1: Entry & Memory Loading
1. **FastAPI** receives a `ChatRequest` (user query, selected data sources, filters, conversation IDs).
2. **`orchestrator_entry.py`** builds the LangGraph and initial state.
3. **Memory Loader** (`memory.py`) loads prior conversation context from SQL Server — per-source summaries and message history.
 
### Phase 2: Routing
4. **Router Node** inspects the request to decide execution paths:
   - `insights` only (internal data sources selected)
   - `web` only (web search requested, no data sources)
   - `both` (parallel: internal + web)
 
### Phase 3: Query Analysis (Internal Path)
5. **Query Analyzer** (`query_analyzer.py`) makes one LLM call to:
   - Classify intent (`analytical` vs `insights`)
   - Decompose complex queries into sub-queries
   - Scope-check each sub-query (in-scope vs out-of-scope)
6. **Type Router** sends analytical queries directly to the analytics node, insights queries to the ReAct agent, and all-out-of-scope queries straight to the compiler.
 
### Phase 4: Agent Execution
 
#### Insights Path (qualitative questions)
7. **Insights ReAct Agent** (`insights_react_agent.py`) selects an execution strategy:
   - **Fast path**: Single simple query → one tool call, no planner
   - **Uniform tool path**: All sub-queries use same tool → parallel execution, no planner
   - **Full ReAct loop**: Mixed tools or dependencies → LLM planner coordinates sequential/parallel tool calls
8. **Insights Tool** (`insights_tool.py`) retrieves data from Azure AI Search and answers via LLM.
 
#### Analytics Path (quantitative questions)
9. **Analytics Agent** (`apps/agents/analytics/`) runs a 4-phase pipeline:
   - **Plan**: LLM generates execution plan (which tools, which columns, what code to write)
   - **Execute**: Fetch data, generate & run pandas code in a sandbox, optionally generate charts
   - **Narrate**: LLM converts structured results to English prose
   - **Reflect**: LLM validates narrative accuracy against data (with retry loops)
 
#### Web Path
10. **Web Agent** (`web_agent.py`) calls Google Gemini with search grounding, formats citations, and optionally generates follow-up questions.
 
### Phase 5: Compilation
11. **Compiler Agent** (`compiler_agent.py`) receives all `AgentResult`s and:
    - Decides synthesis strategy (pass-through for simple cases, LLM synthesis for complex/mixed)
    - Generates executive summary (skipped for analytics-only)
    - Splits content into internal vs external sections
    - Batch-redacts person names from internal sections (one LLM call)
    - Formats citations for external sources
    - Ranks and selects follow-up questions
 
### Phase 6: Memory Saving
12. **Memory Saver** updates the conversation record in SQL Server and persists compressed per-source summaries.
 
---
 
## 5) Data Sources
 
The system queries several Azure AI Search indexes:
 
| Logical Source | Index | Description |
|---|---|---|
| **Patient MVOC** | MVOC (shared) | Patient voice-of-customer feedback |
| **HCP MVOC** | MVOC (shared) | Healthcare professional feedback |
| **PAG MVOC** | MVOC (shared) | Patient advocacy group feedback |
| **Patient MIQs** | MIQ | Medical information queries |
| **Social Listening** | SL | Social media monitoring data |
| **Publication Abstracts** | DIMENSIONS | Scientific publication abstracts |
 
Patient MVOC, HCP MVOC, and PAG MVOC share the same Azure index (`MVOC`) and are distinguished by a `Datasource` filter field. The retrieval layer handles this split transparently.
 
**Internal vs External**: Patient MVOC, HCP MVOC, PAG MVOC, and Patient MIQs are "internal" (subject to PII redaction). Social Listening and Publication Abstracts are "external" (get citation formatting instead).
 
---
 
## 6) Key Design Patterns
 
### Graceful Degradation
Every component prefers partial results over total failure. Failed sources produce error sections; successful sources still render. The compiler always returns *something*.
 
### Per-Source Isolation
Conversation memory, retrieval, and agent results are all keyed by data source. This prevents cross-source context contamination and enables source-specific rendering.
 
### LLM Call Minimization
- Analytics skip summary generation (already narrated upstream)
- Follow-up ranking skips LLM when ≤4 candidates
- PII redaction batches all sections into one LLM call
- Code-sharing groups let multiple sources reuse one LLM-generated code snippet
- Fast paths bypass the ReAct planner for simple queries
 
### Parallel Execution
- Multiple Azure Search indexes are queried concurrently
- Analytics sources execute in parallel (with semaphore cap of 6)
- Insights and web branches run concurrently in mixed mode
- Synthesis and follow-up ranking run concurrently
 
### Sandbox Safety
LLM-generated pandas/matplotlib code runs in a controlled sandbox with AST validation — blocked modules include `os`, `subprocess`, `socket`, `pickle`, `exec`, `eval`, and filesystem access.
 
---
 
## 7) Technology Stack
 
| Layer | Technology |
|---|---|
| API Framework | FastAPI (async) |
| Orchestration | LangGraph (StateGraph with reducer-based parallel state merging) |
| Internal LLM | Azure OpenAI (GPT-4 family) |
| Web Search LLM | Google Gemini (with search grounding) |
| Data Retrieval | Azure AI Search (vector/hybrid/semantic reranker) |
| Embeddings | OpenAI embedding models |
| Database | SQL Server (conversation persistence) |
| Code Execution | Sandboxed Python (pandas, numpy, scikit-learn, matplotlib) |
| Deployment | Docker, Azure DevOps pipelines |
 
---
 
## 8) Component Documentation Index
 
For detailed function-by-function documentation, see:
 
| Document | Covers |
|---|---|
| [`analytics_agent_detailed.md`](./analytics_agent_detailed.md) | Analytics pipeline: planner, router, executor, narrator, reflector, sandbox |
| [`compiler_agent_detailed.md`](./compiler_agent_detailed.md) | Compilation: synthesis strategy, source grouping, error handling, unified synthesis |
| [`insights_react_agent_detailed.md`](./insights_react_agent_detailed.md) | ReAct agent: execution paths, scratchpad, planner, tool dispatch |
| [`insights_tool_detailed.md`](./insights_tool_detailed.md) | Insights tool: retrieval, direct/map-reduce answer paths, per-source processing |
| [`memory_management_detailed.md`](./memory_management_detailed.md) | Conversation persistence: DB schema, load/save lifecycle, LLM compression |
| [`orchestrator_entry_detailed.md`](./orchestrator_entry_detailed.md) | Entry point: state init, graph execution, error handling, memory lifecycle |
| [`orchestrator_workflow_detailed.md`](./orchestrator_workflow_detailed.md) | LangGraph workflow: routing, branch synchronization, node wiring |
| [`post_compilation_detailed.md`](./post_compilation_detailed.md) | Post-compilation: PII redaction, citation formatting, markdown rendering |
| [`query_analyzer_detailed.md`](./query_analyzer_detailed.md) | Query analyzer: classification, decomposition, scope-checking, tool hints |
| [`retrieval_detailed.md`](./retrieval_detailed.md) | Retrieval layer: Azure AI Search, OData filters, index schemas, post-processing |
| [`state_detailed.md`](./state_detailed.md) | State types: GraphState, AgentResult, DecomposedQuery, reducers |
| [`llm_factory_runtime_detailed.md`](./llm_factory_runtime_detailed.md) | LLM Factory: multi-provider dispatch, prompt registry, runtime context |
| [`ai_search_client_retrieval_utils_detailed.md`](./ai_search_client_retrieval_utils_detailed.md) | Search client pool: connection lifecycle, index schemas, accessor helpers |
| [`web_agent_detailed.md`](./web_agent_detailed.md) | Web agent: Gemini integration, grounding citations, query rewriting |
| [`guardrails_detailed.md`](./guardrails_detailed.md) | PII redaction guardrail: LLM-based entity removal |
| [`error_handler_detailed.md`](./error_handler_detailed.md) | Exception hierarchy, node/agent decorators, error codes |
| [`sql_module_detailed.md`](./sql_module_detailed.md) | Async SQL executor: connection pooling, retry, transactions |
| [`model_clients_detailed.md`](./model_clients_detailed.md) | LLM clients: AsyncOpenAILLM, AsyncGeminiLLM, AsyncClaudeLLM |
| [`prompt_loader_detailed.md`](./prompt_loader_detailed.md) | YAML/JSON prompt loading, template rendering, LangChain bridge |
| [`api_entry_points_detailed.md`](./api_entry_points_detailed.md) | FastAPI app: lifespan, routes, request/response models, mock mode |
| [`analytics_sandbox_detailed.md`](./analytics_sandbox_detailed.md) | Code safety: blocked/allowed modules, AST validation, tool registry |
