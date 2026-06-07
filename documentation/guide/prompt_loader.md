# Prompt Loader — YAML/JSON Prompt Management
 
## File
`prompts/prompt_loader.py` (150 lines)
 
## Purpose
Loads prompt templates from YAML/JSON files and renders them with variable substitution. Provides an optional bridge to LangChain `PromptTemplate` objects.
 
---
 
## Class: `PromptLoader`
 
### Constructor
```python
loader = PromptLoader(local_root="prompts")
```
`local_root` is the base directory containing all prompt YAML/JSON files (relative to project root).
 
---
 
## Methods
 
### `get_prompt_config(relative_path, prompt_name) → dict`
Loads the full configuration block for a named prompt from a YAML/JSON file.
 
**Expected file structure (YAML):**
```yaml
redaction_guardrail_prompt:
  system_prompt: "You are a PII redaction assistant. Given the following text, replace all person names with [REDACTED]. Text: {{text}}"
  input_variables:
    - text
 
query_classifier_prompt:
  system_prompt: "Classify the following query..."
  input_variables:
    - user_query
    - conversation_history
```
 
### `get_prompt_string(relative_path, prompt_name, inputs) → str`
1. Loads the prompt config.
2. Extracts `system_prompt` template.
3. Replaces `{{variable}}` placeholders with values from `inputs` dict.
4. Lists and dicts are JSON-serialised before substitution.
5. Raises `ValueError` if required inputs are missing.
 
### `to_langchain_template(relative_path, prompt_name) → PromptTemplate`
Converts a prompt config into a LangChain `PromptTemplate` for use with LangChain chains/agents.
 
---
 
## Template Syntax
- Uses **double curly braces**: `{{variable_name}}`
- NOT Jinja2 — simple string `.replace()` under the hood.
- Variables listed in `input_variables` array are required; missing ones raise `KeyError`.
 
---
 
## Integration
 
```
PromptLoader ──used by──▶ PromptRegistry ──used by──▶ LLMFactory.run(prompt_key, **inputs)
```
 
- `PromptRegistry` maps logical prompt keys (e.g. `"redaction_guardrail_prompt"`) to file paths + prompt names.
- `LLMFactory.run()` calls the registry to resolve and render the prompt, then sends it to the appropriate LLM client.
 
---
 
## File Discovery
- Supports `.yaml`, `.yml`, and `.json` extensions.
- Path is relative to `local_root` (e.g. `"agents/query_classifier.yaml"`).
- Raises `FileNotFoundError` if file doesn't exist.
- Raises `ValueError` for unsupported extensions.
