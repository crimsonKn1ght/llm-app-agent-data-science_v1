# API Entry Points — FastAPI Application
 
## Files
| File | Purpose |
|------|---------|
| `fastapi_v1.py` (629 lines) | Main FastAPI app: lifespan, models, routes |
| `local_app_fastapi.py` | Local dev launcher (loads `.env.local`, runs uvicorn) |
| `main.py` | Production entry point |
 
---
 
## Application Lifespan (`fastapi_v1.py`)
 
The `@asynccontextmanager lifespan(app)` function orchestrates startup and shutdown:
 
### Startup Sequence
1. **Key Vault** — `load_keyvault_secrets_async()` fetches secrets into `os.environ`. Supports `SKIP_KEYVAULT=true` for local mock mode.
2. **Validate secrets** — checks `DB_HOST`, `DB_NAME`, `DB_UID`, `DB_PWD`, `OPENAI_ENDPOINT` are present.
3. **Database** — `AsyncSQLExecutor.init(DBConfig())` creates the connection pool.
4. **Load DB constants** — reads a config table and sets values as env vars.
5. **AI Search** — `AISearchClientManager.init()` creates search client pool.
6. **Logger** — `get_logger().start_worker()` starts the async logging worker.
7. **Runtime Context** — assembles `PromptLoader`, `PromptRegistry`, LLM clients (OpenAI, Gemini, Claude), `LLMFactory`, and `RuntimeContext`. Stores via `runtime_holder.set_runtime()`.
 
### Shutdown Sequence
1. Close all LLM client sessions (OpenAI, Gemini, Claude).
2. Shutdown logger worker.
3. Close database engine (`AsyncSQLExecutor.close()`).
4. Close AI Search clients.
 
---
 
## Endpoints
 
### Health & Readiness
| Path | Method | Purpose |
|------|--------|---------|
| `GET /health` | Liveness probe | Returns `{"status": "ok"}` |
| `GET /ready` | Readiness probe | Returns `{"status": "ready"}` |
| `GET /health/db` | DB health | Pings SQL Server via `db.ping()` |
| `GET /api/health` | Kong gateway alias | Same as `/health` |
| `GET /api/ready` | Kong gateway alias | Same as `/ready` |
| `GET /api/health/db` | Kong gateway alias | Same as `/health/db` |
 
### Main Generation Endpoint
```
POST /api/patient-plus/genai-backend/generate
```
 
**Request** (`PredictRequest`):
| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `parentConversationId` | UUID string | ✅ | Valid UUID |
| `conversationID` | UUID string | ✅ | Valid UUID |
| `sequence_number` | int | ✅ | ≥ 1 |
| `userQuery` | string | ✅ | Non-empty |
| `rephrasedQuery` | string | ❌ | — |
| `persona` | string | ❌ | — |
| `mud_id` | string | ❌ | — |
| `filters` | `Filters` object | ❌ | — |
| `webSearch` | bool | ❌ | Default `false` |
 
**Response** (`PredictResponse`):
| Field | Description |
|-------|-------------|
| `http_status_code` | Logical outcome (200, 422, 500) |
| `parentConversationId` | Echo |
| `conversationID` | Echo |
| `sequence_number` | Echo |
| `responseText` | LLM answer (success) or error message (failure) |
| `suggestedQuestions` | Follow-up questions |
| `generation_timeStamp` | ISO timestamp |
 
**Flow**: Validates request → checks runtime → calls `orchestrator_entry.orchestrate_chat(payload)` → returns result as `JSONResponse` with matching HTTP status code.
 
---
 
## Error Handling
- `RequestValidationError` → 422 with field-level detail.
- `ValidationError` during processing → 422.
- Unhandled exceptions → 500 with generic message (no internal details leaked).
 
All error responses use the same `PredictResponse` shape with `responseText` carrying the error message.
 
---
 
## Mock Mode (`SKIP_KEYVAULT=true`)
For local testing without Azure infrastructure:
- Skips Key Vault, DB, AI Search, and LLM client initialization.
- Sets mock env vars for required secrets.
- Returns a hardcoded mock response from the generation endpoint.
 
---
 
## Local Development
```powershell
python local_app_fastapi.py
```
- Searches for `.env.local` in common locations.
- Sets `LOCAL_DEVELOPMENT=true`.
- Runs uvicorn with reload enabled on port 8000.
