from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.orchestrator.state import AgentResult, DecomposedQuery, GraphState

logger = logging.getLogger(__name__)

MAX_SUB_QUERIES = 3

INTENT_TO_TOOL = {
    "insights": "insights",
    "analytical": "analytical",
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
    valid_types = {"analytical", "insights", "out_of_scope"}
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
    gemini = runtime.gemini_client
    system_prompt = runtime.prompts.get("query_analyzer", "")

    history_context = _build_history_context(state)
    user_input = f"Query to analyze: {user_query}"
    if history_context:
        user_input += f"\n\nConversation context:\n{history_context}"

    try:
        raw = await gemini.generate(
            user_input=user_input,
            system_prompt=system_prompt,
            temperature=0.1,
            max_output_tokens=2048,
        )
        parsed = _parse_llm_output(raw)
        result = _normalize_result(parsed, user_query)
    except Exception as e:
        logger.warning("Query analyzer failed, using fallback: %s", e)
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

    return {
        "execution_path": ["query_analyzer"],
        "query_type": query_type,
        "is_complex": is_complex,
        "sub_queries": in_scope_queries,
        "agent_results": out_of_scope_results,
    }
