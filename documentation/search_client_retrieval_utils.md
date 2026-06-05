# AI Search Client & Retrieval Utils — In-Depth Guide
 
This document covers the Azure AI Search connection management and index schema configuration:
 
- **`linked_services/retrieval/ai_search_client.py`** — Singleton connection pool of async SearchClients
- **`linked_services/retrieval/retrieval_utils.py`** — Index schemas, data source mappings, and accessor helpers
 
---
 
## 1) High-level role
 
These two files form the **infrastructure layer** beneath the retrieval pipeline (`apps/retrieval/ai_search_retrieval.py`). They answer two fundamental questions:
 
- **`ai_search_client.py`**: "How do I get a connected client for an Azure Search index?"
- **`retrieval_utils.py`**: "What fields/config does this index have?"
 
The execution layer (`ai_search_retrieval.py`) builds queries, runs searches, and post-processes results — but delegates connection management and schema knowledge to these files.
 
---
 
## 2) `AISearchClientManager` — connection pool
 
### Design
 
A **singleton class** (class-level state, no instances) that maintains one `SearchClient` per Azure Search index. The lifecycle is tied to the FastAPI app:
 
```
App startup  →  AISearchClientManager.init()    → creates 4 clients
Per request  →  AISearchClientManager.get("MVOC") → returns shared client (zero overhead)
App shutdown →  AISearchClientManager.close()   → closes all clients + credential
```
 
### Class-level state
 
```python
_clients: Dict[str, SearchClient] = {}          # index_key → shared client
_credential: Optional[DefaultAzureCredential] = None  # shared credential
_initialized: bool = False                       # guard flag
```
 
### Registered indexes
 
```python
_INDEX_GETTERS = {
    "DIMENSIONS": lambda: constants.DIMENSIONS_INDEX(),
    "SL":         lambda: constants.SL_INDEX(),
    "MVOC":       lambda: constants.MVOC_INDEX(),
    "MIQ":        lambda: constants.MIQ_INDEX(),
}
```
 
The actual index names (e.g., `"patientplus-dimensions-v2"`) are resolved from constants/KeyVault at init time.
 
---
 
### `init()` — startup
 
```python
@classmethod
async def init(cls) -> None:
```
 
**Called once** during FastAPI lifespan startup.
 
Steps:
1. **Idempotency check**: If already initialized → no-op (safe to call multiple times).
2. **Create shared credential**: `DefaultAzureCredential` with browser/environment credential excluded (uses managed identity in Azure, service principal locally).
3. **Create one `SearchClient` per index** with:
   - `endpoint`: Azure AI Search service URL (from constants/KeyVault)
   - `index_name`: Resolved from constants
   - `credential`: Shared `DefaultAzureCredential`
   - `connection_timeout=10`: Fail fast if Azure Search unreachable
   - `read_timeout=30`: Allow semantic reranker processing time
4. **Mark initialized**.
 
**Why shared credential?** Azure SDK's `DefaultAzureCredential` caches tokens internally and auto-refreshes before expiry. One credential handles all 4 clients, avoiding token fetch per request.
 
**Why connection_timeout=10?** If the Azure Search endpoint is unreachable, waiting longer is wasted time — better to fail fast and let the pipeline report a data fetch error.
 
**Why read_timeout=30?** Semantic reranker queries can take 10-20s for large result sets. 30s provides headroom without allowing infinite waits.
 
---
 
### `get(source_name)` — per-request access
 
```python
@classmethod
def get(cls, source_name: str) -> SearchClient:
```
 
Returns the pre-created `SearchClient` for a canonical index key.
 
- **Input**: `"MVOC"`, `"SL"`, `"DIMENSIONS"`, or `"MIQ"` (case-insensitive, normalized internally)
- **Returns**: Shared `SearchClient` — caller must NOT close it
- **Raises**:
  - `RuntimeError` if `init()` was never called
  - `ValueError` if `source_name` doesn't match any registered index
 
**Why this is fast:** No TCP/TLS handshake, no token fetch, no object creation. Just a dictionary lookup. This makes per-request retrieval overhead negligible.
 
---
 
### `close()` — shutdown
 
```python
@classmethod
async def close(cls) -> None:
```
 
Closes all `SearchClient` instances and the shared credential. Called during FastAPI shutdown.
 
- **Safe to call without init**: If the pool was never initialized (e.g., `SKIP_KEYVAULT=true` local mode), returns silently.
- **Per-client error isolation**: If one client fails to close, others are still attempted.
- **Clears state**: Resets `_clients`, `_credential`, `_initialized` so the manager could theoretically be re-initialized.
 
---
 
## 3) `retrieval_utils.py` — schema and configuration
 
### `get_ai_search_client_async(source_name) -> SearchClient`
 
Thin wrapper over `AISearchClientManager.get()`. This is the **single entry point** used by `ai_search_retrieval.py` for client access.
 
```python
async def get_ai_search_client_async(source_name: str) -> SearchClient:
    return AISearchClientManager.get(source_name)
```
 
---
 
### `DATA_SOURCE_SEARCH_CONFIG`
 
Maps each logical data source to its index and search configuration:
 
```python
{
    "Publication Abstracts": {
        "index": "DIMENSIONS",
        "scoring_profile": "sp_dimensions",
        "use_semantic_reranker": True,
        "semantic_configuration_name": "semantic_confi_patientplus",
    },
    "Patient MVOC":  {"index": "MVOC", "scoring_profile": "sp_mvoc", ...},
    "PAG MVOC":      {"index": "MVOC", ...},
    "HCP MVOC":      {"index": "MVOC", ...},
    "Social Listening": {"index": "SL", "scoring_profile": "sp_social_listening", ...},
    "Patient MIQs":  {"index": "MIQ", "scoring_profile": "sp_miq", ...},
}
```
 
**Key insight:** Patient MVOC, HCP MVOC, and PAG MVOC all share the `MVOC` index. They're differentiated by source-scoping filters defined in `DATA_SOURCE_FILTERS`.
 
---
 
### `DataSource_Index_Mapping`
 
Derived lookup: logical source → index key.
 
```python
{
    "Publication Abstracts": "DIMENSIONS",
    "Patient MVOC": "MVOC",
    "PAG MVOC": "MVOC",
    "HCP MVOC": "MVOC",
    "Social Listening": "SL",
    "Patient MIQs": "MIQ",
}
```
 
Used by `get_aisearch_data()` to determine which index(es) to query for requested data sources.
 
---
 
### `DATA_SOURCE_FILTERS`
 
Source-scoping definitions for shared indexes:
 
```python
{
    "Patient MVOC":  {"index": "MVOC", "field": "Datasource", "values": ["Patient MVOC"]},
    "HCP MVOC":      {"index": "MVOC", "field": "Datasource", "values": ["HCP MVOC"]},
    "PAG MVOC":      {"index": "MVOC", "field": "Datasource", "values": ["PAG MVOC"]},
}
```
 
When querying the shared MVOC index for "Patient MVOC" only, the pipeline adds an OData filter: `Datasource eq 'Patient MVOC'`. This prevents cross-source bleed.
 
---
 
### `INDEX_FIELD_MAPPINGS`
 
The **central per-index schema registry**. For each index, defines what fields to select, search, filter on, and how to handle NER.
 
#### MVOC index
 
```python
{
    "select_fields": ["Name", "Text", "Date", "Datasource", "Product", "Indication",
                      "TherapyArea", "HCPName", "HCPSpecialty", "CountryName", "Region",
                      "overallSentiment", "ner_processed", "age_entities_processed", "raw_ner_entities"],
    "search_fields": ["Text", "ner_processed", "Indication", "TherapyArea", "Product", "HCPSpecialty", "CountryName"],
    "vector_field": "vector",
    "ner_field": "ner_processed",
    "filter_fields": {"diseaseArea": None, "startDate": "Date", "endDate": "Date",
                      "ageGroup": "age_entities_processed", "country": "CountryName", "region": "Region", "diseaseStage": None},
}
```
 
#### SL (Social Listening) index
 
```python
{
    "select_fields": ["Id", "Text", "Title", "Date", "Indication", "Product",
                      "CountryName", "Region", "Domain", "overallSentiment", "Insightshashtag", "Url", ...],
    "search_fields": ["Text", "ner_processed", "Title", "Indication", "CountryName"],
    "vector_field": "vector",
    "ner_field": None,  # No NER → falls back to collection filter for disease area
    "filter_fields": {"diseaseArea": "Indication", ...},
}
```
 
#### MIQ (Patient Medical Information Queries) index
 
```python
{
    "select_fields": ["Name", "Text", "Date", "Product", "Indication", "TherapyArea",
                      "HCPSpecialty", "CountryName", "Region", "overallSentiment", ...],
    "search_fields": ["Text", "ner_processed", "Indication", "TherapyArea", "Product", "HCPSpecialty", "CountryName"],
    "vector_field": "vector",
    "ner_field": "ner_processed",  # Has NER → uses search.ismatch for disease area
    "filter_fields": {"diseaseArea": None, "country": "CountryName", "region": "Region", ...},
}
```
 
#### DIMENSIONS (Publication Abstracts) index
 
```python
{
    "select_fields": ["Id", "DOI", "Text", "Date", "Authors", "Journal", "Mesh_Terms",
                      "Funders", "Altmetric", "Times_Cited", "Indication", "Product",
                      "TherapyArea", "Dimensions_Url", "Linkout", ...],
    "search_fields": ["Text", "ner_processed", "Indication", "TherapyArea", "Mesh_Terms", "Product"],
    "vector_field": "vector",
    "ner_field": None,  # No NER → simple collection filter
    "filter_fields": {"diseaseArea": "Indication", ...},
}
```
 
---
 
### Accessor functions
 
| Function | Returns | Used by |
|---|---|---|
| `get_schema(index_name)` | Full schema dict for index | All other accessors |
| `get_select_fields(index_name)` | `$select` projection list | `_execute_job` |
| `get_search_fields(index_name)` | Keyword search target fields | `_execute_job` |
| `get_vector_field(index_name)` | Embedding column name (`"vector"`) | Vector query construction |
| `get_filter_field(index_name, key)` | OData field name for a canonical filter key, or `None` | `_build_metadata_filter` |
| `get_ner_field(index_name)` | NER field name or `None` | Disease area filter strategy decision |
 
---
 
## 4) How they work together
 
```
ai_search_retrieval.py                    retrieval_utils.py              ai_search_client.py
─────────────────────                    ──────────────────              ─────────────────────
get_aisearch_data(query, sources, ...)
  │
  ├─ DataSource_Index_Mapping           ← "Patient MVOC" → "MVOC"
  │
  ├─ _execute_job(job):
  │    │
  │    ├─ get_ai_search_client_async("MVOC")  ──────────────────────→ AISearchClientManager.get("MVOC")
  │    │                                                                     │
  │    │                                                              returns shared SearchClient
  │    │
  │    ├─ get_select_fields("MVOC")      ← ["Name", "Text", ...]
  │    ├─ get_search_fields("MVOC")      ← ["Text", "ner_processed", ...]
  │    ├─ get_vector_field("MVOC")       ← "vector"
  │    ├─ get_filter_field("MVOC", "country") ← "CountryName"
  │    ├─ get_ner_field("MVOC")          ← "ner_processed"
  │    │
  │    ├─ DATA_SOURCE_SEARCH_CONFIG["Patient MVOC"]
  │    │   ← scoring_profile, semantic config
  │    │
  │    └─ DATA_SOURCE_FILTERS["Patient MVOC"]
  │        ← {field: "Datasource", values: ["Patient MVOC"]}
  │
  └─ Returns per-source DataFrames + __meta__
```
 
---
 
## 5) Key design decisions
 
| Decision | Rationale |
|---|---|
| **Singleton pool** | Zero per-request overhead; connection reuse avoids TCP/TLS handshake costs |
| **One credential for all** | Azure SDK caches/refreshes tokens internally; sharing avoids redundant token fetches |
| **Class-level state** | Simple, no instance management needed; lifecycle is explicit (init/close) |
| **Timeout configuration** | `connection_timeout=10` fast-fails; `read_timeout=30` handles semantic reranker |
| **Idempotent init/close** | Safe to call multiple times — prevents double-initialization bugs |
| **Schema registry** | Centralizes field knowledge so query building stays generic |
| **`filter_fields` with `None`** | Explicit "not supported" signal — filter builder skips unsupported filters cleanly |
| **NER field presence** | Drives the disease area filtering strategy (search.ismatch vs simple collection filter) |
 
---
 
## 6) Lifecycle in FastAPI
 
```python
# fastapi_v1.py lifespan handler:
 
@asynccontextmanager
async def lifespan(app):
    # Startup
    await AISearchClientManager.init()  # Creates 4 SearchClients
    ...
    yield
    # Shutdown
    await AISearchClientManager.close()  # Closes all clients + credential
```
 
---
 
## 7) Local development without Azure
 
When running with `SKIP_KEYVAULT=true`:
- `AISearchClientManager.init()` may not be called (or may fail gracefully)
- `AISearchClientManager.close()` is a safe no-op
- Retrieval calls will fail at `get()` with `RuntimeError` — caught and surfaced as `DataFetchException` upstream
