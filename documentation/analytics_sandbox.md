# Analytics Sandbox — Code Safety & Tool Registry
 
## File
`apps/tools/analytics_tools/__init__.py` (318 lines)
 
## Purpose
Defines the sandbox environment for LLM-generated analytics code: which libraries are allowed, which are blocked, how code is validated for safety, and the base class / registry for all analytics tools.
 
---
 
## Sandbox Configuration
 
### Allowed Libraries (`SANDBOX_LIBRARY_VERSIONS`)
Pinned versions that match the Kubernetes execution pod:
 
| Category | Libraries |
|----------|-----------|
| Core data | `pandas 2.3.2`, `numpy 2.2.6` |
| ML / stats | `scikit-learn 1.7.2`, `scipy 1.15.3` |
| Visualisation | `matplotlib 3.10.6` |
| Text processing | `nltk 3.9.4`, `regex 2025.9.18`, `rapidfuzz 3.14.1` |
| Stdlib | `re`, `json`, `datetime`, `collections`, `math`, `statistics` |
 
### Pre-mapped Imports (`SANDBOX_IMPORTS`)
Aliases available in the sandbox namespace:
```python
pd → pandas, np → numpy, plt → matplotlib.pyplot,
re, regex, json, datetime, collections, math, statistics,
fuzz → rapidfuzz.fuzz, process → rapidfuzz.process, scipy
```
 
### Attribute Imports (`SANDBOX_ATTR_IMPORTS`)
```python
PorterStemmer → nltk.stem.PorterStemmer
word_tokenize → nltk.tokenize.word_tokenize
```
 
### `get_sandbox_versions_context() → str`
Generates a human-readable version list for inclusion in LLM prompts, ensuring generated code targets the correct API surface.
 
---
 
## Code Safety Validation
 
### Blocked Modules (`BLOCKED_MODULES`)
Any import of these modules causes immediate rejection:
 
| Category | Modules |
|----------|---------|
| Filesystem | `os`, `pathlib`, `glob`, `shutil`, `tempfile`, `io` |
| Process/System | `subprocess`, `sys`, `signal`, `ctypes`, `multiprocessing`, `threading` |
| Network | `socket`, `http`, `urllib`, `requests`, `aiohttp`, `httpx` |
| Serialisation | `pickle`, `shelve`, `marshal` |
| Code execution | `importlib`, `runpy`, `code`, `codeop`, `compileall` |
| Other | `webbrowser`, `antigravity` |
 
### `validate_code_safety(code: str) → None`
Uses Python's `ast` module to parse the generated code and walk the AST. Checks:
 
1. **`import X`** — rejects if module is in `BLOCKED_MODULES` or not in `_ALLOWED_MODULES`.
2. **`from X import ...`** — same check on the top-level module.
3. **`__import__("X")`** — catches dynamic import attempts.
4. **`open(...)`** — file access blocked.
5. **`exec(...)` / `eval(...)` / `compile(...)`** — dynamic code execution blocked.
 
Raises `UnsafeCodeError` with line number and specific violation details.
 
---
 
## Base Tool
 
### `BaseTool` (ABC)
All analytics tools inherit from this:
 
```python
class BaseTool(ABC):
    name: str = ""
    description: str = ""
 
    @abstractmethod
    async def execute(self, source_state, runtime, context) -> ToolResult: ...
 
    def _success(self, data, metadata) -> ToolResult: ...
    def _failure(self, error, metadata) -> ToolResult: ...
```
 
- `source_state: SourceExecutionState` — mutable per-source state (DataFrame, schema, etc.)
- `runtime` — `RuntimeContext` with logger, llm_factory, etc.
- `context: dict` — additional info (user query, plan steps)
- Returns `ToolResult(tool_name, success, error, data, metadata)`
 
---
 
## Tool Registry
 
### `ToolRegistry` (class-level singleton)
```python
ToolRegistry.register(tool_instance)     # Register at import time
ToolRegistry.get("tool_name") → BaseTool # Lookup by name
ToolRegistry.names() → list[str]         # All registered names
ToolRegistry.has("name") → bool          # Check existence
ToolRegistry.reset()                     # Clear (for testing)
```
 
The analytics executor looks up tools by name from the plan and calls `.execute()`.
 
---
 
## Security Model
 
```
LLM generates code
       │
       ▼
validate_code_safety(code)  ──blocked──▶ UnsafeCodeError → abort
       │
       ▼ (safe)
Execute in restricted namespace with only SANDBOX_IMPORTS available
```
 
This dual-layer approach (AST validation + restricted namespace) prevents:
- Data exfiltration (no network/filesystem)
- Privilege escalation (no subprocess/os)
- Arbitrary code execution (no eval/exec/pickle)
