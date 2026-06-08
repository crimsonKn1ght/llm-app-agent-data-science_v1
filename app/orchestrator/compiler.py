from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse, urlunparse

from app.orchestrator.state import AgentResult, Citation

SUMMARY_CHAR_THRESHOLD = 1200
MAX_RENDERED_CITATIONS = 5

SOURCE_HEADERS = {
    "insights": "## Insights",
    "analytical": "## Analysis",
    "web_search": "## Web Search",
}

SOURCE_ORDER = ("insights", "analytical", "web_search")

OUT_OF_SCOPE_FALLBACK = "This question is outside the scope of what I can help with."
ERROR_ONLY_RESPONSE = (
    "## Error\n\n"
    "I couldn't complete this request with the available processing paths. "
    "Please try again."
)


@dataclass(frozen=True)
class CompiledSection:
    key: str
    header: str
    content: str


@dataclass(frozen=True)
class CompilationPlan:
    strategy: str
    sections: List[CompiledSection]
    final_response: str
    summary_input: str
    error_notice: str
    citations: List[Citation]
    metadata: Dict[str, Any]


def _ordered_source_items(source_contents: Dict[str, str]) -> Iterable[tuple[str, str]]:
    emitted = set()
    for key in SOURCE_ORDER:
        if key in source_contents:
            emitted.add(key)
            yield key, source_contents[key]

    for key in sorted(source_contents):
        if key not in emitted:
            yield key, source_contents[key]


def build_source_sections(source_contents: Dict[str, str]) -> List[CompiledSection]:
    sections: List[CompiledSection] = []
    for key, content in _ordered_source_items(source_contents):
        if not content or not content.strip():
            continue
        sections.append(
            CompiledSection(
                key=key,
                header=SOURCE_HEADERS.get(key, f"## {key.replace('_', ' ').title()}"),
                content=content.strip(),
            )
        )
    return sections


def categorize_results(agent_results: List[AgentResult]) -> Dict[str, List[AgentResult]]:
    categorized = {
        "success": [],
        "error": [],
        "out_of_scope": [],
    }
    for result in agent_results:
        status = result.get("status")
        if status in categorized:
            categorized[status].append(result)
    return categorized


def render_sections(sections: List[CompiledSection]) -> str:
    return "\n\n".join(
        f"{section.header}\n\n{section.content}"
        for section in sections
    )


def build_summary_input(user_query: str, sections: List[CompiledSection]) -> str:
    all_sources_text = "\n\n".join(
        f"[{section.header}]\n{section.content}"
        for section in sections
    )
    if not all_sources_text.strip():
        all_sources_text = "No information could be retrieved."
    return f"Original question: {user_query}\n\nSource information:\n{all_sources_text}"


def build_error_notice(error_results: List[AgentResult]) -> str:
    if not error_results:
        return ""

    failed = sorted({
        result.get("source") or result.get("agent_type") or "unknown"
        for result in error_results
    })
    label = ", ".join(failed)
    return f"## Notices\n\n- Some parts could not be completed: {label}."


def build_compiler_metadata(
    categorized: Dict[str, List[AgentResult]],
    *,
    citation_count: int,
    rendered_citation_count: int,
    strategy: str,
    rendered_section_count: int,
) -> Dict[str, Any]:
    return {
        "successful_agents": [
            result.get("source") or result.get("agent_type", "")
            for result in categorized["success"]
        ],
        "failed_agents": [
            result.get("source") or result.get("agent_type", "")
            for result in categorized["error"]
        ],
        "skipped_agents": [
            result.get("source") or result.get("agent_type", "")
            for result in categorized["out_of_scope"]
        ],
        "citation_count": citation_count,
        "rendered_citation_count": rendered_citation_count,
        "summary_strategy": strategy,
        "rendered_section_count": rendered_section_count,
    }


def count_citations(agent_results: List[AgentResult]) -> int:
    return sum(len(result.get("citations", [])) for result in agent_results)


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def collect_web_citations(agent_results: List[AgentResult]) -> List[Citation]:
    citations: List[Citation] = []
    seen_urls: set[str] = set()

    for result in agent_results:
        if result.get("status") != "success":
            continue
        if result.get("agent_type") != "web_search" and result.get("source") != "web_search":
            continue

        for citation in result.get("citations", []):
            url = str(citation.get("url", "")).strip()
            if not url:
                continue

            normalized = _normalize_url(url)
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            citations.append(citation)

    return citations


def render_citation_section(citations: List[Citation]) -> str:
    if not citations:
        return ""

    lines = ["## Sources"]
    for idx, citation in enumerate(citations[:MAX_RENDERED_CITATIONS], start=1):
        title = str(citation.get("title") or citation.get("url") or "Source").strip()
        url = str(citation.get("url") or "").strip()
        snippet = str(citation.get("snippet") or "").strip()
        if not url:
            continue

        line = f"{idx}. [{title}]({url})"
        if snippet:
            line += f" - {snippet}"
        lines.append(line)

    return "\n".join(lines) if len(lines) > 1 else ""


def append_citations(final_response: str, citations: List[Citation]) -> str:
    citation_section = render_citation_section(citations)
    if not citation_section:
        return final_response
    return "\n\n".join(part for part in (final_response, citation_section) if part and part.strip())


def plan_compilation(
    *,
    user_query: str,
    source_contents: Dict[str, str],
    agent_results: List[AgentResult],
    summary_char_threshold: int = SUMMARY_CHAR_THRESHOLD,
) -> CompilationPlan:
    categorized = categorize_results(agent_results)
    sections = build_source_sections(source_contents)
    citation_count = count_citations(agent_results)
    has_web_section = any(section.key == "web_search" for section in sections)
    citations = collect_web_citations(agent_results) if has_web_section else []
    rendered_citation_count = min(len(citations), MAX_RENDERED_CITATIONS)

    if not sections and categorized["out_of_scope"]:
        final_response = categorized["out_of_scope"][0].get("result", OUT_OF_SCOPE_FALLBACK)
        strategy = "out_of_scope"
        metadata = build_compiler_metadata(
            categorized,
            citation_count=citation_count,
            rendered_citation_count=0,
            strategy=strategy,
            rendered_section_count=0,
        )
        return CompilationPlan(
            strategy=strategy,
            sections=[],
            final_response=final_response,
            summary_input="",
            error_notice="",
            citations=[],
            metadata=metadata,
        )

    if not sections and categorized["error"]:
        strategy = "error_only"
        metadata = build_compiler_metadata(
            categorized,
            citation_count=citation_count,
            rendered_citation_count=0,
            strategy=strategy,
            rendered_section_count=0,
        )
        return CompilationPlan(
            strategy=strategy,
            sections=[],
            final_response=ERROR_ONLY_RESPONSE,
            summary_input="",
            error_notice="",
            citations=[],
            metadata=metadata,
        )

    error_notice = build_error_notice(categorized["error"]) if sections else ""
    section_text = render_sections(sections)
    total_chars = sum(len(section.content) for section in sections)
    should_summarize = len(sections) > 1 or total_chars > summary_char_threshold
    strategy = "summarize" if should_summarize else "passthrough"

    final_response = section_text
    if error_notice:
        final_response = "\n\n".join(part for part in (final_response, error_notice) if part)
    final_response = append_citations(final_response, citations)

    metadata = build_compiler_metadata(
        categorized,
        citation_count=citation_count,
        rendered_citation_count=rendered_citation_count,
        strategy=strategy,
        rendered_section_count=len(sections),
    )

    return CompilationPlan(
        strategy=strategy,
        sections=sections,
        final_response=final_response,
        summary_input=build_summary_input(user_query, sections) if should_summarize else "",
        error_notice=error_notice,
        citations=citations,
        metadata=metadata,
    )


def assemble_with_summary(
    *,
    summary_text: str,
    sections: List[CompiledSection],
    error_notice: str = "",
    citations: List[Citation] | None = None,
) -> str:
    parts = ["## Summary\n\n" + summary_text.strip(), render_sections(sections)]
    if error_notice:
        parts.append(error_notice)
    response = "\n\n".join(part for part in parts if part and part.strip())
    return append_citations(response, citations or [])
