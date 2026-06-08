from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.orchestrator.events import emit_progress
from app.orchestrator.results import make_out_of_scope_result
from app.orchestrator.state import AgentResult, DecomposedQuery, GraphState

logger = logging.getLogger(__name__)

MAX_SUB_QUERIES = 3
VALID_QUERY_TYPES = {"analytical", "insights", "out_of_scope", "web_search"}
VALID_SCOPES = {"in_scope", "out_of_scope"}

INTENT_TO_TOOL = {
    "insights": "insights",
    "analytical": "analytical",
    "web_search": "web_search",
}

QueryType = Literal["analytical", "insights", "out_of_scope", "web_search"]
Scope = Literal["in_scope", "out_of_scope"]
ToolHint = Literal["insights", "analytical", "web_search"]


class AnalyzerSubQueryModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1)
    intent: ToolHint = "insights"
    scope: Scope = "in_scope"
    scope_reasoning: str = ""
    tool_hint: ToolHint = "insights"

    @field_validator("query", "scope_reasoning", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @model_validator(mode="before")
    @classmethod
    def _normalize_enums(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        intent = str(normalized.get("intent") or "").strip()
        if intent not in INTENT_TO_TOOL:
            intent = "insights"
        normalized["intent"] = intent

        tool_hint = str(normalized.get("tool_hint") or "").strip()
        if tool_hint not in INTENT_TO_TOOL:
            tool_hint = INTENT_TO_TOOL[intent]
        normalized["tool_hint"] = tool_hint

        scope = str(normalized.get("scope") or "").strip()
        if scope not in VALID_SCOPES:
            scope = "in_scope"
        normalized["scope"] = scope

        return normalized


class AnalyzerResultModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query_type: QueryType = "insights"
    is_complex: bool = False
    sub_queries: List[AnalyzerSubQueryModel] = Field(min_length=1)
    reasoning: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_query_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        query_type = str(normalized.get("query_type") or "").strip()
        if query_type not in VALID_QUERY_TYPES:
            query_type = "insights"
        normalized["query_type"] = query_type

        return normalized


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


def _strip_markdown_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def _extract_first_json_object(raw: str) -> str:
    cleaned = _strip_markdown_fences(raw)
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in analyzer output")

    depth = 0
    in_string = False
    escape = False
    for idx, char in enumerate(cleaned[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start:idx + 1]

    raise ValueError("Unterminated JSON object in analyzer output")


def _parse_llm_output(raw: str) -> Dict[str, Any]:
    return json.loads(_extract_first_json_object(raw))


def _model_to_result(model: AnalyzerResultModel, original_query: str) -> Dict[str, Any]:
    query_type = model.query_type

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

    if query_type == "out_of_scope":
        return {
            "query_type": "out_of_scope",
            "is_complex": False,
            "sub_queries": [{
                "query": original_query,
                "intent": "insights",
                "scope": "out_of_scope",
                "scope_reasoning": "Top-level analyzer classification was out_of_scope",
                "tool_hint": "insights",
            }],
        }

    sub_queries = model.sub_queries[:MAX_SUB_QUERIES]
    normalized = [
        {
            "query": sq.query,
            "intent": sq.intent,
            "scope": sq.scope,
            "scope_reasoning": sq.scope_reasoning,
            "tool_hint": sq.tool_hint,
        }
        for sq in sub_queries
    ]

    return {
        "query_type": query_type,
        "is_complex": bool(model.is_complex and len(normalized) > 1),
        "sub_queries": normalized,
    }


def _validate_result(parsed: Dict[str, Any], original_query: str) -> Dict[str, Any]:
    model = AnalyzerResultModel.model_validate(parsed)
    return _model_to_result(model, original_query)


def _parse_validate_repair_result(raw: str, original_query: str) -> Dict[str, Any]:
    parsed = _parse_llm_output(raw)
    return _validate_result(parsed, original_query)


def _classify_fallback_query(original_query: str) -> QueryType:
    query = original_query.lower()
    web_patterns = (
        r"\b(current|recent|latest|live|today|now|right now|news|weather|sports|score|stock|price|prices)\b",
    )
    analytical_patterns = (
        r"\b(count|counts|statistic|statistics|average|mean|median|percentage|percent|trend|trends)\b",
        r"\bhow many\b",
        r"\bcompare\b.*\b(number|numeric|percentage|count|average|trend)\b",
    )

    if any(re.search(pattern, query) for pattern in web_patterns):
        return "web_search"
    if any(re.search(pattern, query) for pattern in analytical_patterns):
        return "analytical"
    return "insights"


def _fallback_result(original_query: str, reason: str) -> Dict[str, Any]:
    query_type = _classify_fallback_query(original_query)
    intent = query_type if query_type != "out_of_scope" else "insights"
    return {
        "query_type": query_type,
        "is_complex": False,
        "sub_queries": [{
            "query": original_query,
            "intent": intent,
            "scope": "in_scope",
            "scope_reasoning": f"Fallback {query_type} routing - {reason}",
            "tool_hint": INTENT_TO_TOOL[intent],
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
        result = _parse_validate_repair_result(raw, user_query)
    except Exception as e:
        raw_preview = locals().get("raw", "")[:300]
        fallback_strategy = _classify_fallback_query(user_query)
        logger.warning(
            "Query analyzer validation failed, using deterministic fallback | strategy=%s | error=%s | raw_preview=%r",
            fallback_strategy,
            e,
            raw_preview,
        )
        result = _fallback_result(user_query, str(e))

    query_type = result["query_type"]
    is_complex = result["is_complex"]
    sub_queries_raw = result["sub_queries"]

    in_scope_queries: List[DecomposedQuery] = []
    out_of_scope_results: List[AgentResult] = []

    for idx, sq in enumerate(sub_queries_raw, start=1):
        if sq["scope"] == "out_of_scope":
            out_of_scope_results.append(make_out_of_scope_result(
                sub_query_id=idx,
                query=sq["query"],
                result="This question is outside the scope of what I can help with.",
                source="query_analyzer",
                scope_reasoning=sq.get("scope_reasoning", ""),
                tool_metadata={
                    "query_type": query_type,
                    "intent": sq.get("intent", ""),
                    "tool_hint": sq.get("tool_hint", ""),
                },
            ))
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

    tool_hints = set(sq["tool_hint"] for sq in in_scope_queries)
    expected_branches = sorted(tool_hints) if tool_hints else []

    logger.info(
        "Query classified | type=%s | is_complex=%s | in_scope=%d | out_of_scope=%d | branches=%s",
        query_type, is_complex, len(in_scope_queries), len(out_of_scope_results), expected_branches,
    )
    for sq in in_scope_queries:
        logger.debug(
            "Sub-query %d | tool=%s | query=%r",
            sq["sub_query_id"], sq["tool_hint"], sq["query"],
        )

    return {
        "execution_path": ["query_analyzer"],
        "query_type": query_type,
        "is_complex": is_complex,
        "sub_queries": in_scope_queries,
        "agent_results": out_of_scope_results,
        "expected_branches": expected_branches,
    }
