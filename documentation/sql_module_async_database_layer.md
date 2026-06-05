# SQL Module — Async Database Layer
 
## File
`linked_services/database/sql_module.py` (604 lines)
 
## Purpose
Provides a production-grade async SQL executor for Azure SQL / MSSQL with connection pooling, automatic retries, transaction support, and structured result objects.
 
---
 
## Core Classes
 
### `DBConfig` (dataclass)
Configuration pulled from environment variables at construction time:
 
| Field | Default | Description |
|-------|---------|-------------|
| `host` | env `DB_HOST` | SQL Server hostname |
| `database` | env `DB_NAME` | Database name |
| `uid` / `pwd` | env vars | Credentials |
| `driver` | `ODBC Driver 18 for SQL Server` | ODBC driver |
| `pool_size` | 10 | SQLAlchemy pool size |
| `max_overflow` | 20 | Extra connections beyond pool_size |
| `pool_recycle` | 3600 | Seconds before recycling idle connections |
| `pool_timeout` | 30 | Seconds to wait for a free connection |
| `max_retries` | 3 | Retry attempts for transient failures |
| `retry_base_delay` | 1.0 | Base delay (seconds) for exponential backoff |
 
### `QueryResult` (dataclass)
Returned by all query methods:
 
| Field | Type | Description |
|-------|------|-------------|
| `rows` | `list[dict]` | Result rows (empty for mutations) |
| `row_count` | `int` | Number of rows returned/affected |
| `duration_ms` | `float` | Execution time |
| `query_id` | `str` | Tracing label |
| `error` | `Optional[str]` | Error message if failed |
 
Properties:
- `.success` → `True` if `error is None`
- `.first` → first row dict or `None`
 
### `IsolationLevel` (enum)
- `READ_COMMITTED` (default)
- `REPEATABLE_READ`
- `SERIALIZABLE`
 
---
 
## `AsyncSQLExecutor` — Singleton
 
### Lifecycle
```python
await AsyncSQLExecutor.init(config: DBConfig)   # Create engine + session factory
db = AsyncSQLExecutor.get()                      # Retrieve singleton
await AsyncSQLExecutor.close()                   # Dispose engine on shutdown
```
 
### Query Methods
 
#### `await db.select(query, params, *, query_id, session) → QueryResult`
- Executes SELECT, returns rows as list of dicts.
- Never raises — check `.success`.
- Supports `:name` parameterised placeholders.
 
#### `await db.update(query, params, *, query_id, session) → QueryResult`
- Executes INSERT / UPDATE / DELETE.
- Auto-commits if no session provided.
- `.row_count` = rows affected.
 
#### `async with db.transaction(isolation) as sess:`
- Context manager for atomic multi-statement transactions.
- Pass `sess` to `select()` / `update()` via `session=sess`.
- Clean exit → COMMIT; exception → ROLLBACK.
 
#### `await db.ping() → bool`
- Health check (`SELECT 1`). Used by `/health/db` endpoint.
 
---
 
## Retry Logic
 
The module-level `_with_retry(fn, config, query_id)` helper:
1. Catches `RETRYABLE_ERRORS = (OperationalError, TimeoutError, OSError, ConnectionResetError)`.
2. Retries up to `config.max_retries` times.
3. Exponential backoff: `retry_base_delay * 2^attempt` seconds.
4. Logs each retry attempt with `query_id`.
 
---
 
## FastAPI Integration
 
```python
from linked_services.database.sql_module import get_db
 
@app.get("/api/data")
async def get_data(db: AsyncSQLExecutor = Depends(get_db)):
    result = await db.select("SELECT ...", {...})
```
 
`get_db()` simply returns `AsyncSQLExecutor.get()` — bridges the singleton with FastAPI's `Depends()`.
 
---
 
## Connection String
Built internally as:
```
mssql+aioodbc://uid:pwd@host/database?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
```
Uses `aioodbc` async ODBC adapter under SQLAlchemy's async engine.
