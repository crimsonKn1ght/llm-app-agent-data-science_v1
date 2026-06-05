# Post-Compilation Pipeline — In-Depth Guide
 
This document covers everything that happens **after** the compiler's unified synthesis in `compiler_agent.py`: response formatting, PII redaction, citation formatting, HTML-to-markdown link conversion, error section assembly, and follow-up question ranking.
 
These functions are all methods on the `ResponseCompiler` class in `apps/agents/compiler_agent.py`.
 
---
 
## 1) Where these functions sit in the compilation flow
 
```
compile()
  ├── Categorize agent_results (success/error/oos/web)
  ├── Build content_map (source → text)
  ├── _needs_synthesis() → decision
  │   ├── True → _unified_synthesis() + _generate_executive_summary()
  │   └── False → pass-through
  ├── _split_blocks() → internal/external sections
  ├── _add_error_sections() → error fallback sections
  ├── _rank_followups() → dedupe + LLM rank
  │
  └── format_response(summary, sections)      ←── THIS DOC STARTS HERE
        ├── Prepend executive summary
        ├── Per-section processing:
        │   ├── External → _format_web_citations()
        │   ├── Internal → collect for batch redaction
        │   └── Not Supported → inline note
        ├── Batch redaction via redact_persons()
        └── _html_links_to_markdown()
```
 
---
 
## 2) `_split_blocks(content_map, source_insights_map) -> (internal, external)`
 
### What it does
Separates compiled per-source content into internal and external blocks based on `DataSource.external_sources()`.
 
### Classification
- **External sources**: `Publication Abstracts`, `Social Listening` → `external` list
- **Internal sources**: `Patient MVOC`, `HCP MVOC`, `PAG MVOC`, `Patient MIQS` → `internal` list
 
### Side effect
Populates `source_insights_map[source_name] = content` for each source. This map is later saved to DB as `jsonResponse` by the memory saver.
 
### Output format
Each block is formatted as:
```markdown
### Patient MVOC
Based on analysis of 500 records, fatigue was mentioned by 14.6%...
```
 
---
 
## 3) `_add_error_sections(sections, error_results)`
 
### What it does
When agent results have `status="error"`, this method creates appropriate error sections in the response.
 
### Logic
1. Split errors into internal vs external based on `tool_metadata.source`.
2. For each group, append a section with the appropriate error message.
3. If errors don't cleanly split (no source metadata), create one generic internal error section.
 
### `_get_error_message(error_results) -> str`
 
Priority-based error message selection:
 
| Priority | Condition | Message |
|---|---|---|
| 1 (highest) | `tool_metadata.error == "infeasible"` | The planner's raw feasibility reasoning (user-facing) |
| 2 | `error_type == "data_fetch_error"` | `constants.DATA_ERROR` |
| 3 | `error_type == "timeout"` | `constants.TIMEOUT_ERROR` |
| 4 | `error_type == "llm_error"` | `constants.LLM_ERROR` |
| 5 | `error_type == "analytics_error"` | `constants.PROCESSING_ERROR` |
| 6 | `error_type == "insights_error"` | `constants.INSIGHTS_AGENT_ERROR` |
| 7 | `error_type == "no_data_error"` | `constants.DATA_ERROR` |
| 8 (default) | Anything else | `constants.PROCESSING_ERROR` |
 
---
 
## 4) `_rank_followups(state, insights_results, web_results) -> List[Dict]`
 
### What it does
Deduplicates, optionally LLM-ranks, and caps follow-up questions to at most 4.
 
### Algorithm
 
1. **Web-only shortcut**: If no insights results but web results exist → return web's follow-ups directly.
2. **Collect**: Gather all `suggested_questions` from all insights results.
3. **Dedupe**: `_dedupe_questions_by_text()` — exact text matching, set-based.
4. **≤4 shortcut**: If 4 or fewer unique questions → skip LLM, just assign IDs.
5. **LLM ranking** (>4 questions): Call `llm_factory.run(prompt_name="compiler_followup_selector_prompt", ...)` with:
   - User query
   - Merged conversation history
   - Sub-query context (which sub-queries were asked)
   - All candidate follow-up questions
6. **Parse response**: Extract `selected_followups` from JSON, match back to original question objects.
7. **Fallback**: On any LLM/parse error → take first 4 questions.
 
### `_dedupe_questions_by_text(questions) -> List[Dict]`
Simple exact-text deduplication using a `set`. Preserves order (first occurrence wins).
 
### `_assign_ids(questions) -> List[Dict]`
Assigns sequential `"id"` from `"1"` to `"4"`. Handles both dict and string inputs.
 
---
 
## 5) `format_response(summary, sections) -> str`
 
### What it does
The final formatting pass that converts structured compiler output into a single markdown string ready for the UI.
 
### Step-by-step
 
**1. Executive summary:**
```markdown
## Executive Summary
{summary text}
```
Only added if `summary` is non-empty (i.e., synthesis produced one).
 
**2. Per-section processing:**
 
For each section `{"heading": "...", "content": "..."}`:
 
| Section type | Detection | Processing |
|---|---|---|
| **External source** | `"external"` in heading (case-insensitive) | Run through `_format_web_citations()` |
| **Not Supported** | `"not supported"` in heading | Inline without `##` heading prefix |
| **Internal source (needs redaction)** | NOT web/dimensions/external | Collected for batch redaction |
| **Web/Dimensions** | `"web"` or `"dimensions"` in heading | Inline as-is (no redaction needed) |
 
**3. Batch PII redaction:**
 
All internal sections are concatenated with `===SECTION_BREAK===` delimiter, sent to `redact_persons()` in **one LLM call**, then split back and mapped to their original positions.
 
Why batch? One LLM call is ~2-3 seconds. With N internal sources, sequential calls would take N × 2-3s. Batching sends all text in one call.
 
**Fallback:** If the LLM consumes/corrupts the delimiter during redaction, the original unredacted text is used for that section.
 
**4. HTML → Markdown link conversion:**
`_html_links_to_markdown()` converts any remaining `<a>` tags to markdown `[text](url)` format.
 
---
 
## 6) `redact_persons(text) -> str` (from `apps/guardrails/guardrails.py`)
 
### What it does
Removes real human person names from medical/clinical text using an LLM guardrail.
 
### What it preserves
- Disease names
- Medical procedures
- Drug names
- Anatomical terms
- Technical medical language
 
### Implementation
Single LLM call: `llm_factory.run(prompt_name="redaction_guardrail_prompt", user_input=text)`.
 
The LLM is instructed to return the text unchanged except with person names removed/replaced. This is a specialized medical NER task — the model must distinguish between:
- Person names to redact: "Dr. Smith mentioned...", "Patient John reported..."
- Medical terms to preserve: "Addison's disease", "Parkinson's", "Cushing syndrome"
 
### Error handling
- `AgentException` → re-raised (already logged upstream)
- Generic `Exception` → wrapped in `AgentExecutionException`
 
---
 
## 7) `_format_web_citations(text) -> str`
 
### What it does
Processes `(Source: ...)` blocks in external source content (Publication Abstracts, Social Listening). Converts raw citations into clickable HTML links.
 
### Pre-processing — garbage cleanup
Before processing, strips known garbage patterns:
- `(Source: 42)` — hallucinated numeric citations
- `(Source: CHUNK SUMMARIES)` — sentinel text leaks
- `(Source: )` — empty source blocks
 
### Citation type handling
 
| Citation type | Detection | Processing |
|---|---|---|
| **Pub ID** | Matches `pub.\d+` | Converts to Dimensions URL: `https://app.dimensions.ai/details/publication/{pub_id}` |
| **Dimensions URL** | Contains `dimensions.ai` | Extracts `pub.NNNN` label from URL, creates link |
| **Full URL** | Starts with `http` | Extracts domain as label, appends 6-char UUID suffix |
| **Bare domain** | Matches `word.tld/path` pattern | Prepends `https://`, uses domain as label |
 
### Deduplication
Within each citation block, duplicate URLs (case-insensitive) are removed.
 
### URL cap for non-publication sources
- **Publication IDs/Dimensions URLs**: No cap (all citations preserved).
- **Other URLs (Social Listening)**: Capped at 2 links per citation block to avoid clutter.
 
### Concrete example
```
Input:  "Fatigue is common (Source: pub.123456789, https://example.com/study)"
Output: "Fatigue is common (Source: <a href='https://app.dimensions.ai/...' target='_blank'>pub.123456789</a>, <a href='https://example.com/study' target='_blank'>example.com-a1b2c3</a>)"
```
 
---
 
## 8) `_html_links_to_markdown(text) -> str`
 
### What it does
Converts HTML `<a>` tags to markdown link format using regex.
 
```
Input:  <a href='https://example.com' target='_blank'>Example-abc123</a>
Output: [Example-abc123](https://example.com)
```
 
### Why this step is needed
`_format_web_citations` creates HTML `<a>` tags (for `target='_blank'` support). The final response needs to be pure markdown for the frontend renderer. This conversion step ensures consistency.
 
### Regex pattern
```python
r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
→ r"[\2](\1)"
```
Handles both single and double quotes around `href`.
 
---
 
## 9) End-to-end formatting example
 
### Input to `format_response`
```python
summary = "Fatigue is the most commonly reported side effect across all sources."
sections = [
    {"heading": "Insights from Internal Sources",
     "content": "### Patient MVOC\nDr. Smith noted 14.6% of patients... (pub.123)"},
    {"heading": "Insights from External Sources",
     "content": "### Publication Abstracts\nStudies show fatigue rates (Source: pub.456, https://example.com/study)"},
    {"heading": "Not Supported",
     "content": "Disease stage filtering is not available for the selected sources."},
]
```
 
### Processing steps
 
**1. Executive summary added:**
```markdown
## Executive Summary
Fatigue is the most commonly reported side effect across all sources.
```
 
**2. Internal section → collected for redaction:**
```
"### Patient MVOC\nDr. Smith noted 14.6% of patients... (pub.123)"
```
 
**3. External section → `_format_web_citations()`:**
```
(Source: pub.456, https://example.com/study)
→ (Source: <a href='https://app.dimensions.ai/.../pub.456' target='_blank'>pub.456</a>, <a href='https://example.com/study' target='_blank'>example.com-f3e2d1</a>)
```
 
**4. Batch redaction of internal sections:**
```
"Dr. Smith noted 14.6% of patients..."
→ "[Name redacted] noted 14.6% of patients..."
```
 
**5. `_html_links_to_markdown()` on final output:**
```
<a href='...' target='_blank'>pub.456</a>
→ [pub.456](https://app.dimensions.ai/.../pub.456)
```
 
### Final output
```markdown
## Executive Summary
Fatigue is the most commonly reported side effect across all sources.
 
## Insights from Internal Sources
### Patient MVOC
[Name redacted] noted 14.6% of patients...
 
## Insights from External Sources
### Publication Abstracts
Studies show fatigue rates (Source: [pub.456](https://app.dimensions.ai/.../pub.456), [example.com-f3e2d1](https://example.com/study))
 
 Not Supported Disease stage filtering is not available for the selected sources.
```
 
---
 
## 10) Cross-cutting patterns
 
| Pattern | Detail |
|---|---|
| **Batch LLM calls** | Redaction batches all internal sections in one call (saves latency) |
| **Selective redaction** | Only internal sources are redacted; web/external/dimensions are exempt |
| **Citation normalization** | Pub IDs → full Dimensions URLs; bare domains → `https://` prefixed |
| **UUID link dedup prevention** | Each URL link gets a unique suffix so the browser doesn't collapse duplicates |
| **Graceful degradation** | Delimiter corruption in redaction → falls back to original text |
| **Priority-based error messages** | Most specific error type wins (infeasible > data_fetch > timeout > generic) |
| **LLM-ranked follow-ups** | Only invoked when >4 candidates; ≤4 skips LLM entirely |
