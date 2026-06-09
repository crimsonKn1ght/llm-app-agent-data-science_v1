from __future__ import annotations

from typing import Any, Iterable, Literal, Mapping, Optional

from app.orchestrator.state import AgentResult, Citation

Confidence = Literal["high", "medium", "low", "unknown"]


def make_success_result(
    *,
    sub_query_id: int,
    query: str,
    agent_type: str,
    result: str,
    source: str,
    latency_ms: Optional[int] = None,
    citations: Optional[list[Citation]] = None,
    confidence: Confidence = "unknown",
    tool_metadata: Optional[dict[str, Any]] = None,
    data_sources: Optional[list[str]] = None,
) -> AgentResult:
    agent_result: AgentResult = {
        "sub_query_id": sub_query_id,
        "query": query,
        "agent_type": agent_type,
        "result": result,
        "status": "success",
        "source": source,
        "output_chars": len(result),
        "confidence": confidence,
        "tool_metadata": tool_metadata or {},
    }
    if latency_ms is not None:
        agent_result["latency_ms"] = latency_ms
    if citations is not None:
        agent_result["citations"] = citations
    if data_sources is not None:
        agent_result["data_sources"] = data_sources
    return agent_result


def make_error_result(
    *,
    sub_query_id: int,
    query: str,
    agent_type: str,
    error_message: str,
    source: str,
    result: Optional[str] = None,
    latency_ms: Optional[int] = None,
    tool_metadata: Optional[dict[str, Any]] = None,
) -> AgentResult:
    result_text = result or error_message
    agent_result: AgentResult = {
        "sub_query_id": sub_query_id,
        "query": query,
        "agent_type": agent_type,
        "result": result_text,
        "status": "error",
        "error_message": error_message,
        "source": source,
        "output_chars": len(result_text),
        "confidence": "unknown",
        "tool_metadata": tool_metadata or {},
    }
    if latency_ms is not None:
        agent_result["latency_ms"] = latency_ms
    return agent_result


def make_out_of_scope_result(
    *,
    sub_query_id: int,
    query: str,
    result: str,
    source: str,
    scope_reasoning: str = "",
    tool_metadata: Optional[dict[str, Any]] = None,
) -> AgentResult:
    metadata = dict(tool_metadata or {})
    if scope_reasoning:
        metadata["scope_reasoning"] = scope_reasoning

    return {
        "sub_query_id": sub_query_id,
        "query": query,
        "agent_type": "out_of_scope",
        "result": result,
        "status": "out_of_scope",
        "source": source,
        "output_chars": len(result),
        "confidence": "high",
        "tool_metadata": metadata,
    }


def normalize_citations(
    results: Iterable[Mapping[str, Any]],
    *,
    default_provider: str,
) -> list[Citation]:
    citations: list[Citation] = []
    for rank, item in enumerate(results, start=1):
        url = str(item.get("href") or item.get("url") or "").strip()
        if not url:
            continue

        citations.append({
            "title": str(item.get("title") or "Untitled").strip(),
            "url": url,
            "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
            "rank": rank,
            "provider": str(item.get("provider") or default_provider).strip(),
        })
    return citations
