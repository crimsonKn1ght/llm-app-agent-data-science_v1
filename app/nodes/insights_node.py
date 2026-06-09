from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List

from app.orchestrator.events import emit_progress, emit_text
from app.orchestrator.results import make_success_result
from app.orchestrator.state import DecomposedQuery, GraphState

logger = logging.getLogger(__name__)

SOURCE_KEY = "insights"
HEADER = "## Insights\n\n"
NO_RELEVANT_ANSWER = "No relevant answer retrieved based on this query"


def _get_my_queries(state: GraphState) -> List[DecomposedQuery]:
    return [sq for sq in state["sub_queries"] if sq["tool_hint"] == SOURCE_KEY]


def _build_user_input(
    query: str, summary: str, history: List[Dict[str, str]],
) -> str:
    parts = [f"Question: {query}"]
    if summary:
        parts.append(f"\nConversation summary:\n{summary}")
    if history:
        recent = history[-12:]
        lines = [f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in recent]
        parts.append("\nRecent conversation:\n" + "\n".join(lines))
    return "\n".join(parts)


async def _stream_single(state: GraphState, query: str) -> str:
    runtime = state["runtime"]
    queue = state["stream_queue"]
    loader = runtime.prompt_loader

    user_input = _build_user_input(
        query,
        state.get("conversation_summary", ""),
        state.get("conversation_history", []),
    )

    collected: List[str] = []
    async for chunk in runtime.llm_client.stream(
        user_input=user_input,
        system_prompt=loader.get_system_prompt("insights"),
        temperature=loader.get_temperature("insights"),
        max_output_tokens=loader.get_max_tokens("insights"),
    ):
        collected.append(chunk)
        await emit_text(queue, chunk, origin=SOURCE_KEY)

    return "".join(collected)


async def _generate_one(state: GraphState, query: str) -> str:
    runtime = state["runtime"]
    loader = runtime.prompt_loader

    user_input = _build_user_input(
        query,
        state.get("conversation_summary", ""),
        state.get("conversation_history", []),
    )

    return await runtime.llm_client.generate(
        user_input=user_input,
        system_prompt=loader.get_system_prompt("insights"),
        temperature=loader.get_temperature("insights"),
        max_output_tokens=loader.get_max_tokens("insights"),
    )


async def _compile_and_stream(state: GraphState, sub_answers: List[str]) -> str:
    runtime = state["runtime"]
    queue = state["stream_queue"]
    loader = runtime.prompt_loader

    combined_input = "\n\n---\n\n".join(
        f"Sub-answer {i+1}:\n{ans}" for i, ans in enumerate(sub_answers)
    )
    user_input = f"Original question: {state['user_query']}\n\nSub-answers:\n{combined_input}"

    collected: List[str] = []
    async for chunk in runtime.llm_client.stream(
        user_input=user_input,
        system_prompt=loader.get_system_prompt("source_synthesis"),
        temperature=loader.get_temperature("source_synthesis"),
        max_output_tokens=loader.get_max_tokens("source_synthesis"),
    ):
        collected.append(chunk)
        await emit_text(queue, chunk, origin=SOURCE_KEY)

    return "".join(collected)


async def insights_node(state: GraphState) -> Dict[str, Any]:
    started = time.perf_counter()
    my_queries = _get_my_queries(state)

    if not my_queries:
        logger.debug("Insights node: no queries to process, skipping")
        return {
            "execution_path": ["insights"],
            "completed_branches": ["insights"],
            "agent_results": [],
            "source_contents": {},
        }

    queue = state["stream_queue"]
    await emit_progress(queue, "Generating insights...", origin=SOURCE_KEY)
    await emit_text(queue, HEADER, origin=SOURCE_KEY)

    synthesis_used = len(my_queries) > 1

    if len(my_queries) == 1:
        result_text = await _stream_single(state, my_queries[0]["query"])
    else:
        logger.info("Insights node: processing %d sub-queries", len(my_queries))
        tasks = [_generate_one(state, sq["query"]) for sq in my_queries]
        sub_answers = await asyncio.gather(*tasks)
        sub_answers = [a for a in sub_answers if a and a.strip()]

        if not sub_answers:
            result_text = NO_RELEVANT_ANSWER
            await emit_text(queue, result_text, origin=SOURCE_KEY)
        else:
            result_text = await _compile_and_stream(state, sub_answers)

    await emit_text(queue, "\n\n", origin=SOURCE_KEY)

    if not result_text or not result_text.strip():
        result_text = NO_RELEVANT_ANSWER

    logger.info("Insights node completed | result_chars=%d", len(result_text))

    latency_ms = int((time.perf_counter() - started) * 1000)
    agent_results = [
        make_success_result(
            sub_query_id=sq["sub_query_id"],
            query=sq["query"],
            agent_type=SOURCE_KEY,
            result=result_text,
            source=SOURCE_KEY,
            latency_ms=latency_ms,
            confidence="unknown",
            tool_metadata={
                "intent": sq["intent"],
                "prompt_name": "source_synthesis" if synthesis_used else "insights",
                "sub_query_count": len(my_queries),
                "synthesis_used": synthesis_used,
            },
        )
        for sq in my_queries
    ]

    return {
        "execution_path": ["insights"],
        "completed_branches": ["insights"],
        "agent_results": agent_results,
        "source_contents": {SOURCE_KEY: result_text},
    }
