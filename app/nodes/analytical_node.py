from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.orchestrator.events import emit_progress
from app.orchestrator.state import AgentResult, GraphState

logger = logging.getLogger(__name__)

_PROMPT_NAME = "analytical"


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
        parts.append("\nRecent conversation:\n" + "\n".join(lines))

    return "\n".join(parts)


async def _process_sub_query(
    sub_query: Dict[str, Any],
    system_prompt: str,
    temperature: float,
    max_output_tokens: int,
    conversation_summary: str,
    conversation_history: List[Dict[str, str]],
    llm_client: Any,
) -> AgentResult:
    query = sub_query["query"]
    sub_id = sub_query["sub_query_id"]

    user_input = _build_prompt_input(query, conversation_summary, conversation_history)

    try:
        answer = await llm_client.generate(
            user_input=user_input,
            system_prompt=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return {
            "sub_query_id": sub_id,
            "query": query,
            "agent_type": "analytical",
            "result": answer,
            "status": "success",
        }
    except Exception as e:
        logger.error("Analytical node failed for sub_query %d: %s", sub_id, e)
        return {
            "sub_query_id": sub_id,
            "query": query,
            "agent_type": "analytical",
            "result": f"Failed to generate an answer: {e}",
            "status": "error",
        }


async def analytical_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    system_prompt = loader.get_system_prompt(_PROMPT_NAME)
    temperature = loader.get_temperature(_PROMPT_NAME)
    max_output_tokens = loader.get_max_tokens(_PROMPT_NAME)

    await emit_progress(state["stream_queue"], "Running analytical reasoning...")

    sub_queries = state["sub_queries"]
    summary = state.get("conversation_summary", "")
    history = state.get("conversation_history", [])

    tasks = [
        _process_sub_query(
            sq, system_prompt, temperature, max_output_tokens,
            summary, history, runtime.llm_client,
        )
        for sq in sub_queries
    ]
    results: List[AgentResult] = await asyncio.gather(*tasks)

    return {
        "execution_path": ["analytical"],
        "completed_branches": ["internal"],
        "agent_results": list(results),
    }
