from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.orchestrator.events import emit_progress
from app.orchestrator.state import AgentResult, DecomposedQuery, GraphState

logger = logging.getLogger(__name__)

MAX_SUB_QUERIES = 3

INTENT_TO_TOOL = {
    "insights": "insights",
    "analytical": "analytical",
    "web_search": "web_search",
}


def _build_history_context(state: GraphState) -> str:
    parts: List[str] = []
    summary = state.get("conversation_summary", "")
    if summary:
        parts.append(f"Conversation summary:\n{summary}")

    history = state.get("conversation_history", [])
    recent = history[-12:]
    if recent:
        lines = []
        for msg in recent:
            role = msg.get("role", "user").upper()
            lines.append(f"{role}: {msg.get('content', '')}")
        parts.append("Recent conversation:\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _parse_llm_output(raw: str) -> Dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    return json.loads(cleaned)


def _normalize_result(parsed: Dict[str, Any], original_query: str) -> Dict[str, Any]:
    valid_types = {"analytical", "insights", "out_of_scope", "web_search"}
    query_type = parsed.get("query_type", "insights")
    if query_type not in valid_types:
        query_type = "insights"

    is_complex = parsed.get("is_complex", False)
    sub_queries = parsed.get("sub_queries", [])

    if query_type == "analytical":
        return {
            "query_type": "analytical",
            "is_complex": False,
            "sub_queries": [{
                "query": original_query,
                "intent": "analytical",
                "scope": "in_scope",
                "scope_reasoning": "Analytical queries passed through without decomposition",
                "tool_hint": "analytical",
            }],
        }

    if query_type == "web_search":
        return {
            "query_type": "web_search",
            "is_complex": False,
            "sub_queries": [{
                "query": original_query,
                "intent": "web_search",
                "scope": "in_scope",
                "scope_reasoning": "Web search queries passed through without decomposition",
                "tool_hint": "web_search",
            }],
        }

    if len(sub_queries) > MAX_SUB_QUERIES:
        sub_queries = sub_queries[:MAX_SUB_QUERIES]

    if not sub_queries:
        sub_queries = [{
            "query": original_query,
            "intent": "insights",
            "scope": "in_scope",
            "scope_reasoning": "Default — no sub-queries returned",
            "tool_hint": "insights",
        }]

    normalized = []
    for sq in sub_queries:
        intent = sq.get("intent", "insights")
        normalized.append({
            "query": sq.get("query", original_query),
            "intent": intent,
            "scope": sq.get("scope", "in_scope"),
            "scope_reasoning": sq.get("scope_reasoning", ""),
            "tool_hint": sq.get("tool_hint", INTENT_TO_TOOL.get(intent, "insights")),
        })

    return {
        "query_type": query_type,
        "is_complex": is_complex,
        "sub_queries": normalized,
    }


def _fallback_result(original_query: str, reason: str) -> Dict[str, Any]:
    return {
        "query_type": "insights",
        "is_complex": False,
        "sub_queries": [{
            "query": original_query,
            "intent": "insights",
            "scope": "in_scope",
            "scope_reasoning": f"Fallback — {reason}",
            "tool_hint": "insights",
        }],
    }


async def query_analyzer_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    user_query = state["user_query"]
    queue = state["stream_queue"]
    loader = runtime.prompt_loader
    system_prompt = loader.get_system_prompt("query_analyzer")

    await emit_progress(queue, "Analyzing your query...")

    history_context = _build_history_context(state)
    user_input = f"Query to analyze: {user_query}"
    if history_context:
        user_input += f"\n\nConversation context:\n{history_context}"

    try:
        raw = await runtime.llm_client.generate(
            user_input=user_input,
            system_prompt=system_prompt,
            temperature=loader.get_temperature("query_analyzer"),
            max_output_tokens=loader.get_max_tokens("query_analyzer"),
        )
        parsed = _parse_llm_output(raw)
        result = _normalize_result(parsed, user_query)
    except Exception as e:
        logger.warning("Query analyzer LLM failed, using insights fallback | error=%s", e)
        result = _fallback_result(user_query, str(e))

    query_type = result["query_type"]
    is_complex = result["is_complex"]
    sub_queries_raw = result["sub_queries"]

    in_scope_queries: List[DecomposedQuery] = []
    out_of_scope_results: List[AgentResult] = []

    for idx, sq in enumerate(sub_queries_raw, start=1):
        if sq["scope"] == "out_of_scope":
            out_of_scope_results.append({
                "sub_query_id": idx,
                "query": sq["query"],
                "agent_type": "out_of_scope",
                "result": "This question is outside the scope of what I can help with.",
                "status": "out_of_scope",
            })
        else:
            in_scope_queries.append({
                "sub_query_id": idx,
                "query": sq["query"],
                "intent": sq["intent"],
                "scope": "in_scope",
                "scope_reasoning": sq.get("scope_reasoning", ""),
                "tool_hint": sq.get("tool_hint", "insights"),
            })

    if is_complex and len(in_scope_queries) > 1:
        parts = "\n".join(
            f"  {sq['sub_query_id']}. {sq['query']}" for sq in in_scope_queries
        )
        await emit_progress(
            queue,
            f"Your query was decomposed into {len(in_scope_queries)} parts:\n{parts}",
        )

    logger.info(
        "Query classified | type=%s | is_complex=%s | in_scope=%d | out_of_scope=%d",
        query_type, is_complex, len(in_scope_queries), len(out_of_scope_results),
    )
    for sq in in_scope_queries:
        logger.debug(
            "Sub-query %d | tool=%s | query=%r",
            sq["sub_query_id"], sq["tool_hint"], sq["query"],
        )
    if out_of_scope_results:
        logger.info(
            "Out-of-scope sub-queries: %s",
            [r["query"] for r in out_of_scope_results],
        )

    return {
        "execution_path": ["query_analyzer"],
        "query_type": query_type,
        "is_complex": is_complex,
        "sub_queries": in_scope_queries,
        "agent_results": out_of_scope_results,
    }
