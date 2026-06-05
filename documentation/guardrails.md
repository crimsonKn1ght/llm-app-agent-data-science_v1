# Guardrails — PII Redaction
 
## File
`apps/guardrails/guardrails.py` (82 lines)
 
## Purpose
Provides a single guardrail function that redacts personally identifiable information (PII) from text before it is stored or returned to users.
 
## How It Works
 
```
User text → redact_persons(text) → LLM call with redaction prompt → cleaned text
```
 
The function `redact_persons(text: str) -> str` delegates to `llm_factory.run()` using the `"redaction_guardrail_prompt"` prompt key. The LLM itself performs the redaction (entity recognition + replacement) rather than a regex or NER model.
 
## Key Behaviour
 
| Scenario | Outcome |
|----------|---------|
| LLM succeeds | Returns redacted text |
| `AgentException` raised by LLM layer | Re-raised as-is (already typed) |
| Any other exception | Wrapped in `AgentExecutionException` with `ErrorCode.GUARDRAIL_ERROR` |
 
## Integration Points
- Called by the orchestrator after generating final responses and before persisting to DB.
- Uses `RuntimeContext.llm_factory` (accessed via `runtime_holder`).
- Prompt defined in `prompts/tools/` or `prompts/agents/` YAML (key: `redaction_guardrail_prompt`).
 
## Design Notes
- Intentionally minimal — a single function, not a class.
- LLM-based approach handles nuanced PII (contextual names, rare formats) better than regex, at the cost of latency and token spend.
- Future extensibility: additional guardrail functions (toxicity, off-topic detection) would be added to the same module.
