# LLM Factory & Runtime Context — In-Depth Guide
 
This document covers the runtime dependency layer:
 
- **`linked_services/runtime/runtime_context.py`** — Plain container holding all runtime services
- **`linked_services/runtime/llm_factory.py`** — Dynamic LLM dispatch with multi-provider fallback
- **`linked_services/runtime/prompt_registry.py`** — Prompt resolution from YAML files
- **`linked_services/runtime/runtime_holder.py`** — Singleton lifecycle for the RuntimeContext
 
---
 
## 1) High-level role
 
Every agent, tool, and node in the system needs access to:
- An LLM client (to generate text)
- A logger (to record operations)
- A database connection (to persist conversations)
- A prompt loader (to resolve prompt templates)
 
Instead of passing these individually everywhere, they're bundled into a single `RuntimeContext` object that flows through `state["runtime"]`. The `LLMFactory` within it provides a unified `run(prompt_name, user_input, variables)` API that abstracts away which LLM provider (OpenAI, Gemini, Claude) actually handles the call.
 
---
 
## 2) `RuntimeContext` — the service container
 
```python
class RuntimeContext:
    def __init__(self, logger=None, db=None, prompt_loader=None,
                 prompt_mirror=None, llm_factory=None, storage=None):
        self.logger = logger
        self.db = db
        self.prompt_loader = prompt_loader
        self.prompt_mirror = prompt_mirror
        self.llm_factory = llm_factory
        self.storage = storage
```
 
### Fields
 
| Field | Type | Purpose |
|---|---|---|
| `logger` | Async logger | Structured logging with `parent_id`/`child_id`/`module` correlation |
| `db` | `AsyncSQLExecutor` | SQL Server connection for conversation persistence |
| `prompt_loader` | `PromptLoader` | Loads YAML prompt config files from disk |
| `prompt_mirror` | Optional | (Reserved) prompt versioning/mirroring service |
| `llm_factory` | `LLMFactory` | Unified LLM dispatch with multi-provider fallback |
| `storage` | Storage client | Azure Blob/file storage for artifacts |
 
### Design note
`RuntimeContext` is deliberately a **plain container** — no lifecycle logic, no initialization side effects. It simply holds references. Construction and lifecycle are handled by `runtime_holder.py`.
 
---
 
## 3) `runtime_holder.py` — singleton lifecycle
 
```python
_runtime: Optional[RuntimeContext] = None
 
def get_runtime() -> RuntimeContext:
    global _runtime
    if _runtime is None:
        _runtime = _build_runtime()
    return _runtime
 
def set_runtime(runtime: RuntimeContext):
    global _runtime
    _runtime = runtime
```
 
### `_build_runtime() -> RuntimeContext`
 
Lazy construction of the full runtime stack:
 
1. Create logger via `get_logger()`
2. Create `PromptLoader(local_root="prompts")`
3. Create `PromptRegistry(prompt_loader)` — scans and indexes all YAML prompt files
4. Create LLM clients: `AsyncOpenAILLM()`, `AsyncGeminiLLM()`, `AsyncClaudeLLM()`
5. Create `LLMFactory(registry, openai_client, gemini_client, claude_client, logger)`
6. Bundle into `RuntimeContext(logger, db=None, ..., llm_factory, storage=None)`
 
**Note:** `db` and `storage` are `None` in the default build — they're injected by the FastAPI startup handler (`fastapi_v1.py`) which calls `set_runtime()` with the fully-configured context.
 
### Usage patterns
- **`orchestrator_entry.py`**: calls `runtime_holder.get_runtime()` per request
- **`fastapi_v1.py` startup**: calls `set_runtime(fully_configured_runtime)` during app init
- **Tests**: call `set_runtime(mock_runtime)` to inject test doubles
 
---
 
## 4) `LLMFactory` — dynamic multi-provider dispatch
 
### Constructor
 
```python
class LLMFactory:
    def __init__(self, registry, openai_client, gemini_client, logger, claude_client=None):
        self.registry = registry      # PromptRegistry
        self.openai = openai_client   # AsyncOpenAILLM instance
        self.gemini = gemini_client   # AsyncGeminiLLM callable
        self.claude = claude_client   # AsyncClaudeLLM callable (optional)
        self.logger = logger
```
 
### `run(prompt_name, user_input, variables) -> str`
 
The **single public API** used by every agent and tool to call an LLM.
 
#### Parameters
 
| Parameter | Type | Purpose |
|---|---|---|
| `prompt_name` | `str` | Logical prompt identifier (e.g., `"query_analyzer_prompt"`) |
| `user_input` | `str` | The user/dynamic content to pass to the model |
| `variables` | `Optional[Dict]` | Template variables injected into the system prompt via `.format()` |
 
#### How it works
 
1. **Resolve prompt configs:**
   ```python
   configs = self.registry.resolve_all(prompt_name, variables=variables)
   ```
   Returns a priority-sorted list of provider configurations (YAML-defined). Lower `priority` number = tried first.
 
2. **Iterate through configs (fallback chain):**
   For each config:
   - Extract `model_params.model` (e.g., `"gpt-4o"`, `"gemini"`, `"claude-sonnet-4-20250514"`)
   - Build a provider-specific runner via `_build_runner(cfg)`
   - Call the runner with `user_input`
   - If successful → return the response
   - If retryable error (`RateLimitError`, `aiohttp.ClientError`) → log warning, try next config
 
3. **Exhaustion:**
   If all configs fail → raise `AgentExecutionException` with `LLM_API_ERROR` code.
 
#### Concrete example
 
For `prompt_name="insights_direct_answer_prompt"`, the registry might resolve:
```yaml
# Config 1 (priority: 0) — primary
model_params:
  model: gpt-4o
  temperature: 0.1
  max_tokens: 4096
system_prompt: "You are a pharmaceutical insights analyst..."
 
# Config 2 (priority: 1) — fallback
model_params:
  model: gpt-4o-mini
  temperature: 0.1
  max_tokens: 4096
system_prompt: "You are a pharmaceutical insights analyst..."
```
 
The factory tries `gpt-4o` first. If it gets a rate limit error, it falls back to `gpt-4o-mini`.
 
---
 
### `_build_runner(cfg) -> Callable`
 
Creates a provider-specific async function based on `model_params.model`:
 
| Model name | Provider | Client method called |
|---|---|---|
| `"gemini"` | Google Gemini | `self.gemini(user_input, system_prompt=..., **kwargs)` |
| `"claude*"` | Anthropic Claude | `self.claude(user_input, system_prompt=..., **kwargs)` |
| Anything else | Azure OpenAI | `self.openai.call_openai_chat(model_name, user_input, system_prompt, **kwargs)` |
 
Each runner:
1. Logs which provider is being used
2. Passes the system prompt from the resolved YAML config
3. Forwards any extra `model_kwargs` (temperature, max_tokens, etc.)
 
---
 
### Retryable errors
 
```python
RETRYABLE_ERRORS = (RateLimitError, aiohttp.ClientError)
```
 
Only these trigger fallback to the next config. Other errors (e.g., `ValueError`, `JSONDecodeError`) are **not retryable** and will propagate immediately.
 
---
 
## 5) `PromptRegistry` — prompt resolution engine
 
### Constructor
 
```python
class PromptRegistry:
    def __init__(self, loader: PromptLoader, root: str = "prompts"):
        self.loader = loader
        self.root = Path(root)
        self.index: Dict[str, List[str]] = {}
        self._build_index()
```
 
### `_build_index()`
 
Scans the entire `prompts/` directory tree for `*.yaml` files. For each file, extracts prompt names (top-level keys) and maps them to their file paths.
 
After indexing:
```python
self.index = {
    "query_analyzer_prompt": ["agents/openai/query_analyzer.yaml"],
    "insights_direct_answer_prompt": [
        "tools/openai/insights_direct.yaml",
        "tools/openai/insights_direct_fallback.yaml"
    ],
    "web_agent_prompt": ["agents/gemini/web_agent.yaml"],
    ...
}
```
 
### `resolve_all(prompt_name, variables) -> List[Dict]`
 
1. Look up all file paths for `prompt_name` in the index.
2. For each path:
   - Load the YAML config via `loader.get_prompt_config(path, prompt_name)`
   - Attach metadata: `_prompt_path`, `_provider` (inferred from folder structure)
   - Resolve system prompt template with `variables` via `loader.get_prompt_string(path, prompt_name, inputs=variables)`
3. Sort by `priority` field (lower = tried first).
4. Return sorted list.
 
### `_extract_provider(path) -> str`
 
Infers the LLM provider from the folder structure:
- `prompts/agents/openai/...` → `"openai"`
- `prompts/agents/gemini/...` → `"gemini"`
- `prompts/tools/claude/...` → `"claude"`
 
---
 
## 6) End-to-end LLM call trace
 
```
Agent code:
    response = await runtime.llm_factory.run(
        prompt_name="analytics_narrator",
        user_input="USER QUERY: How many patients...\nDATA: {...}",
        variables={"intent": "count", "source": "Patient MVOC"}
    )
 
LLMFactory.run():
    1. registry.resolve_all("analytics_narrator", variables={...})
       → loads YAML, injects variables into system_prompt template
       → returns [{priority:0, model:"gpt-4o", system_prompt:"..."}, ...]
 
    2. _build_runner(config[0])
       → model="gpt-4o" → creates run_openai closure
 
    3. await run_openai(user_input)
       → self.openai.call_openai_chat("gpt-4o", user_input, system_prompt, temp=0.1)
       → returns "Based on analysis of 500 records..."
 
    4. Return response string to agent
```
 
---
 
## 7) Key design patterns
 
| Pattern | Detail |
|---|---|
| **Single API surface** | All agents call `llm_factory.run(prompt_name, ...)` regardless of provider |
| **YAML-driven routing** | Which model handles which prompt is config, not code |
| **Priority-based fallback** | Multiple configs per prompt enable graceful degradation on rate limits |
| **Lazy singleton** | Runtime is built once (lazily) and shared across all requests |
| **Variable injection** | System prompts are templates (`{intent}`, `{source}`) filled at call time |
| **Provider abstraction** | Adding a new provider means adding a client + a folder in `prompts/` |
 
---
 
## 8) Dependencies
 
| Component | Role |
|---|---|
| `utils/model_clients/openai_client.py` | Azure OpenAI API wrapper |
| `utils/model_clients/gemini_client.py` | Google Gemini API wrapper |
| `utils/model_clients/claude_client.py` | Anthropic Claude API wrapper |
| `prompts/prompt_loader.py` | YAML file loading and template rendering |
| `config/constants.py` | `ErrorCode.LLM_API_ERROR` |
| `utils/error_handling/error_handler.py` | `AgentExecutionException` |
