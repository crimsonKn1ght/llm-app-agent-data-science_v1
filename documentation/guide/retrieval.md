# Retrieval Layer Detailed Guide (`ai_search_retrieval.py` + `retrieval_utils.py`)
 
## 1) High-level role in the system
 
The retrieval layer is the **data access and relevance engine** for internal sources backed by Azure AI Search.
 
At runtime, tools like `insights_tool` and analytics fetchers call `get_aisearch_data(...)` to:
 
1. Convert logical data sources (Patient MVOC, SL, etc.) into concrete Azure index jobs.
2. Build vector/hybrid search requests with metadata filters.
3. Query indexes in parallel.
4. Post-process raw results (score filtering, reranker filtering, dedup, caps).
5. Return per-source `DataFrame`s plus structured retrieval diagnostics (`__meta__`).
 
In short: **LLM reasoning happens after retrieval; retrieval ensures the model reasons on grounded internal evidence.**
 
---
 
## 2) How the two files divide responsibilities
 
- **`linked_services/retrieval/retrieval_utils.py`** — Declarative/config side.
  - Defines source→index mapping, per-source search defaults, filter field mappings, and schema accessors.
  - Provides shared async Azure Search client accessor.
 
- **`apps/retrieval/ai_search_retrieval.py`** — Execution/pipeline side.
  - Builds OData filters from user metadata.
  - Runs search jobs (parallel), handles retries/error paths, post-processes output, and returns final payload.
 
---
 
## 3) End-to-end retrieval flow (practical view)
 
1. Caller invokes `get_aisearch_data(query, data_sources, metadata_filters, ...)`.
2. Data sources are normalized and mapped to indexes via `DataSource_Index_Mapping`.
3. Query embedding is generated (`runtime.llm_factory.openai.generate_embedding`).
4. Jobs are created per index; MVOC is split per logical source to enforce source-level scoping.
5. Jobs execute in parallel using `_execute_job(...)` + `asyncio.gather(..., return_exceptions=True)`.
6. Each job:
   - gets async search client,
   - builds OData filter,
   - builds vector query (for vector/hybrid),
   - runs Azure Search,
   - post-processes rows,
   - maps results to logical source key(s).
7. Job outputs are merged and augmented with `__meta__` diagnostics.
8. If every requested source is empty, `DataFetchException` is raised.
 
---
 
## 4) `retrieval_utils.py` — constants, structures, and functions
 
### `_MVOC_SEMANTIC_CFG`
 
Reusable semantic search config bundle for MVOC family sources.
 
### `DATA_SOURCE_SEARCH_CONFIG`
 
Main configuration table keyed by logical source (`DataSource.*`).
 
For each source, defines:
 
- `index` (canonical index key: `MVOC`, `SL`, `MIQ`, `DIMENSIONS`)
- optional `scoring_profile`
- semantic reranker defaults (`use_semantic_reranker`, `semantic_configuration_name`)
 
### `DataSource_Index_Mapping`
 
Derived dictionary from `DATA_SOURCE_SEARCH_CONFIG`.
Maps logical source → index key. Used by `get_aisearch_data(...)` to determine which indexes to query.
 
### `DATA_SOURCE_FILTERS`
 
Source-scoping definitions (especially for shared index `MVOC`).
Example pattern: for logical source `Patient MVOC`, enforce `Datasource eq 'Patient MVOC'`.
 
### `_COMMON_ANALYTICS_FIELDS`
 
Fields included across index select projections to support analytics and downstream transforms (`["ner_processed", "age_entities_processed", "raw_ner_entities"]`).
 
These columns are not user-facing search fields — they exist so that downstream analytics and NER-based filtering logic always has the raw data available in the returned DataFrame.
 
### `_BASE_FILTER_FIELDS`
 
Canonical filter-key template used across indexes, then overridden per index where needed.
 
A shared template dictionary that maps **canonical filter key names** (the names the application uses) to **actual Azure index field names** (or `None` if the index doesn't support that filter).
 
```
{
    "diseaseArea": None,       # overridden per-index (some use "Indication", some use NER)
    "startDate":   "Date",     # all indexes use "Date"
    "endDate":     "Date",
    "ageGroup":    "age_entities_processed",
    "region":      None,       # overridden per-index
    "country":     None,       # overridden per-index
    "diseaseStage": None,
}
```
 
Each index entry in `INDEX_FIELD_MAPPINGS` starts from this base and overrides specific keys (e.g., MVOC sets `"country": "CountryName"`, `"region": "Region"`).
 
### `INDEX_FIELD_MAPPINGS`
 
Central per-index schema registry. For each index key (`"MVOC"`, `"SL"`, `"MIQ"`, `"DIMENSIONS"`), the schema defines:
 
| Schema key | What it controls | Example (MVOC) |
|---|---|---|
| `select_fields` | Columns returned in search results (the `$select` projection sent to Azure). Controls what data the downstream tools/agents receive. | `["Name", "Text", "Date", "Datasource", "Product", "Indication", ...]` |
| `search_fields` | Text columns Azure searches against for keyword/hybrid queries. These are the fields where your query text is matched. | `["Text", "ner_processed", "Indication", "TherapyArea", "Product", ...]` |
| `vector_field` | The column holding the pre-computed embedding vector for vector/hybrid search. Always `"vector"` in this codebase. | `"vector"` |
| `ner_field` | The column with NER-extracted entities, or `None` if the index doesn't have NER data. Used for smarter disease area filtering (see NER section below). | `"ner_processed"` (MVOC, MIQ) or `None` (SL, DIMENSIONS) |
| `filter_fields` | Maps canonical filter names → actual OData field names for that index. This is how the same user filter (e.g. "country") resolves to different physical fields across indexes. | `{"country": "CountryName", "region": "Region", "diseaseArea": None, ...}` |
 
**Why this matters:** Different Azure indexes have different column names for the same concept. `INDEX_FIELD_MAPPINGS` centralizes these differences so the filter builder and search logic can stay generic.
 
### What is NER and its relation to disease area?
 
**NER = Named Entity Recognition** — an NLP technique that extracts structured entities (disease names, drug names, symptoms, etc.) from free text.
 
Some indexes (MVOC, MIQ) have a pre-computed field called `ner_processed` where NER has already been run on the document `Text`. The extracted entities are stored in this searchable field.
 
**How it connects to disease area filtering:**
 
When a user selects a disease area filter (e.g. `"Asthma"`), the code uses two different strategies depending on whether the index has an `ner_field`:
 
1. **Index HAS `ner_field`** (MVOC, MIQ → `"ner_processed"`):
   - Uses `search.ismatch(...)` against BOTH `Indication` (match if ANY word hits) AND `ner_processed` (match if ALL words hit).
   - Combined with OR: document matches if either field matches.
   - This is more accurate because NER-extracted entities are cleaner/more precise than raw text.
 
2. **Index has NO `ner_field`** (SL, DIMENSIONS → `None`):
   - Falls back to a simple collection filter on the `Indication` field directly.
   - Less precise but still functional.
 
This dual-strategy is implemented in `_build_indication_ismatch_filter()` and the disease area section of `_build_metadata_filter()` in `ai_search_retrieval.py`.
 
### Functions
 
#### `get_ai_search_client_async(source_name: str) -> SearchClient`
 
Returns the shared async `SearchClient` from the `AISearchClientManager` connection pool.
 
- `source_name` is a canonical index key like `"MVOC"`, `"SL"`, `"DIMENSIONS"`, `"MIQ"`.
- It calls `AISearchClientManager.get(source_name)` which returns a pre-initialized, shared client.
- The client is **not closed** after use — lifecycle is managed at app startup/shutdown in `fastapi_v1.py`.
 
Why it matters:
- Single access path for all search clients across the app.
- Avoids creating/destroying connections per request (performance).
 
#### `get_schema(index_name: str) -> IndexSchema`
 
Looks up `index_name` in `INDEX_FIELD_MAPPINGS` and returns the full schema dictionary for that index.
- If the index key doesn't exist, raises `KeyError` with a message listing registered indexes.
- All other accessor functions below call this internally.
 
Think of it as: "give me the complete field configuration for this Azure index."
 
#### `get_select_fields(index_name: str) -> List[str]`
 
Returns the `select_fields` list from the schema — i.e., which columns to include in Azure Search results.
This controls the `$select` parameter sent to Azure, determining what data columns appear in the returned DataFrame.
 
#### `get_search_fields(index_name: str) -> List[str]`
 
Returns the `search_fields` list — i.e., which text columns Azure should run keyword matching against.
For example, for MVOC this returns `["Text", "ner_processed", "Indication", "TherapyArea", "Product", "HCPSpecialty", "CountryName"]`.
 
#### `get_vector_field(index_name: str) -> str`
 
Returns the name of the embedding vector column (always `"vector"` in this codebase).
Used when constructing `VectorizedQuery` objects for vector/hybrid search.
 
#### `get_filter_field(index_name: str, canonical_key: str) -> Optional[str]`
 
Translates a **canonical filter name** (used by the application) to the **actual Azure field name** for that specific index.
 
Examples:
- `get_filter_field("MVOC", "country")` → `"CountryName"`
- `get_filter_field("MVOC", "diseaseArea")` → `None` (MVOC uses NER-based matching instead)
- `get_filter_field("SL", "diseaseArea")` → `"Indication"`
 
Returns `None` when the filter isn't supported for that index, which tells `_build_metadata_filter` to skip that filter clause.
 
#### `get_ner_field(index_name: str) -> Optional[str]`
 
Returns the NER field name if the index has pre-computed NER entities, otherwise `None`.
 
- `get_ner_field("MVOC")` → `"ner_processed"` (NER available, use for disease area matching)
- `get_ner_field("SL")` → `None` (no NER, fall back to Indication field)
 
This is checked in `_build_metadata_filter()` to decide the disease area filtering strategy (see NER section above).
 
---
 
## 5) `ai_search_retrieval.py` — function-by-function
 
### Runtime and retry setup
 
#### `_is_transient_search_error(exc) -> bool`
 
Classifies retryable Azure failures (HTTP 429/503 and request/response transport errors).
 
#### `_search_retry`
 
A tenacity retry policy object (up to 3 attempts, exponential backoff 1s→8s).
 
> Note: In this current file version, `_search_retry` exists as infrastructure but is not directly applied around the search call.
 
### Dedup and OData helpers
 
#### `deduplicate_by_vector(df, embeddings, threshold=0.9) -> DataFrame`
 
Computes cosine similarity matrix and drops near-duplicate rows where similarity exceeds threshold.
Used to reduce semantic duplicates in retrieved results.
 
#### `_escape_odata_value(value) -> str`
 
Escapes single quotes for safe OData string literals.
 
#### `_odata_in_list(values) -> str`
 
Builds comma-separated escaped value list for `search.in(...)` expressions.
 
#### `_normalize_datetime_offset(value) -> Optional[str]`
 
Normalizes date/datetime text to OData-compatible `DateTimeOffset` style.
 
`T` is the ISO 8601 separator between date and time portions (e.g. `2024-05-01T10:30:00`).
`Z` is the UTC timezone designator (Zulu time, offset `+00:00`).
 
Behavior:
- `"2024-05-01"` → `"2024-05-01T00:00:00Z"` (date-only → midnight UTC)
- `"2024-05-01T10:30:00"` → `"2024-05-01T10:30:00Z"` (no timezone → append Z)
- `"2024-05-01T10:30:00Z"` → unchanged (already valid)
- `"   "` → `None`
 
#### `_build_indication_ismatch_filter(indications, ner_field, indication_field='Indication') -> Optional[str]`
 
Builds compound `search.ismatch(...)` clause:
- checks `Indication` with mode `'any'` (match if ANY word in the phrase matches),
- checks NER field with mode `'all'` (ALL words in the phrase must match),
- combines both with OR: document matches if EITHER field matches.
 
Goal: robust disease area matching when NER fields are available.
 
#### `_collection_filter(field, values) -> Optional[str]`
 
Builds OData for collection-type fields (fields that are arrays in Azure):
- single value: `field/any(x: x eq 'v')`
- multiple values: `field/any(x: search.in(x, 'v1,v2', ','))`
 
#### `_equality_filter(field, values) -> Optional[str]`
 
Builds equality filter for scalar fields:
- single value: `field eq 'v'`
- multiple values: `(field eq 'v1' or field eq 'v2')`
 
### Metadata filter assembly
 
#### `_build_metadata_filter(index_name, metadata_filters, data_sources=None, requested_source_key=None) -> Optional[str]`
 
Core OData composer. This is the big function that translates user-selected filters into Azure OData filter strings.
 
It combines (when present):
- **Source scope** from `DATA_SOURCE_FILTERS` (important for MVOC splits — ensures Patient MVOC queries only return Patient MVOC docs from the shared MVOC index),
- **Disease area** (NER-based `search.ismatch` preferred when `ner_field` exists, fallback to collection filter on `Indication`),
- **Time period** (`startDate ge`, `endDate le` — uses `_normalize_datetime_offset` to standardize dates),
- **Age group** normalization (maps UI labels like `"Adults (18-50 years)"` → `"Adults"`) + collection filtering,
- **Geography** (supports both legacy `{"type": "region", "value": "..."}` and pydantic `{"global": [...], "region": [...], "country": [...]}` payload shapes, with global/region/country hierarchy — global means no geo filter),
- **Disease stage** equality.
 
Returns all clauses joined with ` and `, or `None` if no filters apply.
 
### Job result helpers
 
#### `_JOB_KEYS`
 
Defines normalized structure for per-job outputs:
`result_map`, `errors`, `failed_sources`, `failed_indexes`, `filter_by_logical_source`.
 
#### `_empty_job_result() -> Dict[str, Any]`
 
Creates empty container with expected keys so merge logic is predictable.
 
#### `_sources_for_index(index_name, normalized_sources, per_source_cfg) -> List[str]`
 
Finds logical source keys that map to a given index.
 
#### `_mark_failure(result, meta, index_name, requested_source_key, normalized_sources, per_source_cfg)`
 
Stores index/source failure metadata and injects empty DataFrames for affected logical sources.
 
#### `_backfill_empty(result, index_name, requested_source_key, normalized_sources, per_source_cfg)`
 
Ensures expected source keys exist in result map even when no docs were found.
Prevents key errors and stabilizes downstream merge/consumption.
 
#### `_scope_df_to_filter(df, scope) -> DataFrame`
 
Applies source scope filter (field + allowed values) to a DataFrame.
Used mainly for shared indexes where one index serves multiple logical sources (e.g., filtering MVOC DataFrame rows to only Patient MVOC entries).
 
### Post-processing
 
#### `_post_process_results(df, index_name, vector_field, *, score_threshold, reranker_score_threshold, use_semantic_reranker, has_search_text, enable_deduplication, result_threshold, intent) -> DataFrame`
 
Pipeline that transforms raw Azure results to curated outputs:
1. Optional BM25/hybrid score floor (`@search.score`) — drops rows below threshold.
2. Optional semantic reranker cutoff (`@search.reranker_score`) — sorts by reranker score, truncates at first row below threshold. **Skipped when `intent` is set** (e.g., analytical queries need all records).
3. Optional vector deduplication — uses `deduplicate_by_vector` with cosine similarity at 0.9 threshold.
4. Sorting by best available score column (prefers reranker score, falls back to BM25).
5. Hard row cap at `result_threshold`.
6. Drop vector column for non-analytical intent to reduce payload size.
 
### Per-index job execution
 
#### `_execute_job(job, **kwargs) -> Dict[str, Any]`
 
Executes one retrieval job for one index (or one MVOC logical-source split).
 
Key behavior:
- Create/get async search client from pool,
- Build OData filter via `_build_metadata_filter`,
- Build `VectorizedQuery` object for vector/hybrid search,
- Compute effective defaults from per-source config (scoring profile, semantic reranker config, semantic configuration name),
- For analytical intent: include vector column in select fields for downstream cosine-sim filtering,
- Execute async Azure Search and collect all result docs,
- Post-process DataFrame through `_post_process_results`,
- Map results to logical source key(s) — for MVOC splits, applies `_scope_df_to_filter` to ensure correct source scoping,
- On failure at any stage: capture structured metadata in `failed_indexes`/`failed_sources` and return safe empty outputs.
 
### Main entry point
 
#### `get_aisearch_data(query, data_sources, metadata_filters, search_type, k_nearest_neighbors, top, result_threshold, score_threshold, reranker_score_threshold, enable_deduplication, scoring_profile, scoring_parameters, use_semantic_reranker, semantic_configuration_name, intent) -> Dict[str, Any]`
 
This is the **public retrieval API** used by `insights_tool`, `fetch_records`, and `analytical_tool`.
 
Responsibilities:
- Normalize source names,
- Resolve target indexes via `DataSource_Index_Mapping`,
- Generate query embedding via OpenAI,
- Build jobs (including MVOC split strategy — one job per MVOC logical source),
- Run all jobs concurrently via `asyncio.gather(..., return_exceptions=True)`,
- Merge outputs while protecting non-empty data from accidental overwrite by empty backfills,
- Attach `__meta__` diagnostics,
- Raise `DataFetchException` if all requested sources are empty.
 
Return shape:
```python
{
    "Patient MVOC":          pd.DataFrame,   # rows from Azure AI Search
    "Social Listening":      pd.DataFrame,
    "Publication Abstracts": pd.DataFrame,
    "__meta__": {
        "failed_sources":           {...},
        "failed_indexes":           {...},
        "errors":                   [...],
        "last_error":               str | None,
        "filters_by_logical_source": {...},
    }
}
```
 
---
 
## 6) Why this design is strong
 
1. **Separation of concerns**: config/schema in `retrieval_utils`, execution in `ai_search_retrieval`.
2. **Cross-source consistency**: canonical filters mapped per index via `_BASE_FILTER_FIELDS` + overrides.
3. **Shared-index safety**: MVOC split + explicit scoping avoids source bleed.
4. **Performance**: async parallel fan-out per index job.
5. **Resilience**: partial failures are surfaced in metadata, not silently dropped.
6. **Downstream friendliness**: always returns predictable keys and DataFrame containers.
 
---
 
## 7) Quick mental model
 
- `retrieval_utils.py` = **what to query and how fields are named**.
- `ai_search_retrieval.py` = **how queries are executed, filtered, and returned**.
 
Together, they form the retrieval backbone used by both qualitative insights and quantitative analytics.
