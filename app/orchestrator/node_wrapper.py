from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, List

from app.orchestrator.events import emit_text
from app.orchestrator.results import make_error_result
from app.orchestrator.state import DecomposedQuery, GraphState

logger = logging.getLogger(__name__)

NodeFunc = Callable[[GraphState], Awaitable[Dict[str, Any]]]
NODE_FAILURE_NOTICE = "{source} could not be completed. Continuing with available results."


def _matching_queries(state: GraphState, tool_hint: str) -> List[DecomposedQuery]:
    return [sq for sq in state.get("sub_queries", []) if sq.get("tool_hint") == tool_hint]


def _fallback_queries(state: GraphState, tool_hint: str) -> List[DecomposedQuery]:
    matches = _matching_queries(state, tool_hint)
    if matches:
        return matches

    return [{
        "sub_query_id": 0,
        "query": state.get("user_query", ""),
        "intent": tool_hint,
        "scope": "in_scope",
        "scope_reasoning": "Node failed before matching queries could be processed",
        "tool_hint": tool_hint,
    }]


def with_node_error_handling(
    node_func: NodeFunc,
    *,
    node_name: str,
    branch_name: str,
) -> NodeFunc:
    async def wrapped(state: GraphState) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            return await node_func(state)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.error("%s failed | error=%s", node_name, exc, exc_info=True)
            queue = state.get("stream_queue")
            if queue is not None:
                try:
                    label = branch_name.replace("_", " ").title()
                    await emit_text(
                        queue,
                        f"\n\n> {NODE_FAILURE_NOTICE.format(source=label)}\n\n",
                        origin=branch_name,
                    )
                except Exception:
                    logger.debug("Failed to emit node failure notice", exc_info=True)

            error_results = [
                make_error_result(
                    sub_query_id=sq["sub_query_id"],
                    query=sq["query"],
                    agent_type=branch_name,
                    error_message=f"{node_name} failed while processing this request.",
                    source=branch_name,
                    latency_ms=latency_ms,
                    tool_metadata={
                        "node": node_name,
                        "error_type": type(exc).__name__,
                    },
                )
                for sq in _fallback_queries(state, branch_name)
            ]

            return {
                "execution_path": [node_name],
                "completed_branches": [branch_name],
                "agent_results": error_results,
                "source_contents": {},
                "error": {
                    "has_error": True,
                    "node": node_name,
                    "error_message": str(exc),
                },
                "is_error_state": True,
            }

    return wrapped
