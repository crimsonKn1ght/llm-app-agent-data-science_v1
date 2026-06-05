from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.orchestrator.state import AgentResult, GraphState

logger = logging.getLogger(__name__)


def _build_prompt_input(
    query: str,
    conversation_summary: str,
    conversation_history: List[Dict[str, str]],
) -> str:
    parts = [f"Question: {query}"]

    if conversation_summary:
        parts.append(f"\nConversation summary:\n{conversation_summary}")

    if conversation_history:
        recent = conversation_history[-12:]
        lines = []
        for msg in recent:
            role = msg.get("role", "user").upper()
            lines.append(f"{role}: {msg.get('content', '')}")
        parts.append(f"\nRecent conversation:\n" + "\n".join(lines))

    return "\n".join(parts)


async def _process_sub_query(
    sub_query: Dict[str, Any],
    system_prompt: str,
    conversation_summary: str,
    conversation_history: List[Dict[str, str]],
    gemini_client: Any,
) -> AgentResult:
    query = sub_query["query"]
    sub_id = sub_query["sub_query_id"]

    user_input = _build_prompt_input(query, conversation_summary, conversation_history)

    try:
        answer = await gemini_client.generate(
            user_input=user_input,
            system_prompt=system_prompt,
            temperature=0.7,
            max_output_tokens=4096,
        )
        return {
            "sub_query_id": sub_id,
            "query": query,
            "agent_type": "insights",
            "result": answer,
            "status": "success",
        }
    except Exception as e:
        logger.error("Insights node failed for sub_query %d: %s", sub_id, e)
        return {
            "sub_query_id": sub_id,
            "query": query,
            "agent_type": "insights",
            "result": f"Failed to generate an answer: {e}",
            "status": "error",
        }


async def insights_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    gemini = runtime.gemini_client
    system_prompt = runtime.prompts.get("insights", "")
    sub_queries = state["sub_queries"]
    summary = state.get("conversation_summary", "")
    history = state.get("conversation_history", [])

    tasks = [
        _process_sub_query(sq, system_prompt, summary, history, gemini)
        for sq in sub_queries
    ]
    results: List[AgentResult] = await asyncio.gather(*tasks)

    return {
        "execution_path": ["insights"],
        "completed_branches": ["internal"],
        "agent_results": list(results),
    }
