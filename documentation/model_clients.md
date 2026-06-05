# Model Clients — OpenAI, Gemini & Claude Wrappers
 
## Files
| File | Lines | Class |
|------|-------|-------|
| `utils/model_clients/openai_client.py` | 528 | `AsyncOpenAILLM` |
| `utils/model_clients/gemini_client.py` | 392 | `AsyncGeminiLLM` |
| `utils/model_clients/claude_client.py` | 400 | `AsyncClaudeLLM` |
 
## Purpose
Each client wraps a different LLM provider with a uniform async callable interface (`await client(prompt, **kwargs) → str`), adding retry logic, token management, structured logging, and error normalisation.
 
---
 
## Common Interface
 
All three clients are callable:
```python
response_text: str = await client(prompt, system_prompt=..., temperature=..., max_output_tokens=...)
```
 
They are instantiated once during FastAPI lifespan startup and injected into `LLMFactory`.
 
---
 
## `AsyncOpenAILLM` — Azure OpenAI
 
### Key Design
- Uses the official `openai` SDK (`AsyncAzureOpenAI`).
- Bearer token authentication via `linked_services.llm_token.llm_token_manager`.
- Client instance is **lazily created and reused** (`_get_client()`).
 
### Retry Strategy
- **tenacity** with exponential backoff + jitter.
- Retries on: `RateLimitError`, `APIConnectionError`, `APIError`, `AuthenticationError`.
- Respects `Retry-After` header from Azure when available.
 
### Model-Specific Logic
| Model family | Behaviour |
|-------------|-----------|
| GPT-4 / GPT-4o | Uses `temperature` parameter |
| GPT-5 (o-series) | Uses `reasoning_effort` instead of temperature |
 
### Additional Methods
- `generate_embedding(text) → list[float]` — calls `text-embedding-3-small` with its own retry.
- `get_num_tokens(text, model) → int` — token counting via `tiktoken`.
 
### Error Handling
- Rate limits → `AgentExecutionException(ErrorCode.LLM_RATE_LIMIT)`
- Other API errors → `AgentExecutionException(ErrorCode.LLM_API_ERROR)`
 
---
 
## `AsyncGeminiLLM` — Google Gemini (Vertex AI)
 
### Key Design
- **Raw HTTP** via `aiohttp` (no SDK) — posts JSON to Vertex AI REST endpoint.
- Manages its own `aiohttp.ClientSession` (created lazily, closed on shutdown).
- Safety settings: all categories set to `BLOCK_NONE` (content filtering handled at app level).
 
### Payload Construction
- `_build_payload(prompt, cfg)` → Gemini `generateContent` format.
- Supports **web search** via Gemini's `google_search` tool when `use_web_search=True`.
- When web search is active, returns `(text, raw_response)` tuple; otherwise just `text`.
 
### Retry Strategy
- **tenacity** exponential backoff (same pattern as OpenAI).
- Retries on: HTTP 429 (rate limit) and 5xx (server errors).
- Non-retryable 4xx errors raise immediately.
 
### Response Parsing
`_extract_response` handles multiple response formats:
1. `candidates[0].content.parts[0].text` (standard Gemini)
2. `choices[0].message.content` (OpenAI-compatible wrapper)
3. Plain `text` field (simplified)
 
---
 
## `AsyncClaudeLLM` — Anthropic Claude (Vertex AI)
 
### Key Design
- **Subclasses `AsyncGeminiLLM`** — reuses session management, retry logic, and token estimation.
- Overrides payload format (Anthropic Messages API), response parsing, and endpoint URL.
- Supports **extended thinking** (`budget_tokens ≥ 1024`) — when enabled, forces `temperature=1`.
 
### Payload Format (Anthropic Messages API)
```json
{
  "anthropic_version": "vertex-2023-10-16",
  "messages": [{"role": "user", "content": [{"type": "text", "text": "..."}]}],
  "max_tokens": 4096,
  "temperature": 0,
  "system": "optional system prompt"
}
```
 
### Response Parsing
Handles two formats:
1. **GSK API Gateway** (OpenAI-compatible): `choices[0].message.content`
2. **Native Anthropic**: `content[].type=="text"` blocks (skips `thinking` blocks)
 
### Extended Thinking
When `budget_tokens >= 1024`:
- `temperature` forced to 1 (Anthropic requirement)
- `top_p` omitted
- Response may contain `thinking` blocks (filtered out)
 
---
 
## Shared Patterns
 
| Pattern | All Clients |
|---------|-------------|
| Bearer token auth | Via `llm_token_manager` |
| Structured logging | `await self.logger.info(parent_id, child_id, tag, message, **metrics)` |
| Token estimation | `_estimate_tokens(text)` (chars ÷ 4 heuristic) |
| Graceful close | `await client.close()` called during shutdown |
| Error normalisation | All failures become `AgentExecutionException` with appropriate `ErrorCode` |
