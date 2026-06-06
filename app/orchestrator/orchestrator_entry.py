from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from app.memory import conversation_store
from app.orchestrator.state import GraphState
from runtime.runtime_config import get_runtime

logger = logging.getLogger(__name__)


async def orchestrate(
    user_query: str,
    conversation_id: Optional[str],
    stream_queue: asyncio.Queue,
) -> str:
    runtime = get_runtime()

    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    conversation_history = []
    conversation_summary = ""

    conv_data = conversation_store.load(conversation_id)
    if conv_data is not None:
        conversation_history, conversation_summary = conversation_store.build_context(conv_data)
        logger.info(
            "Loaded conversation %s: %d history messages, summary length %d",
            conversation_id,
            len(conversation_history),
            len(conversation_summary),
        )

    initial_state: GraphState = {
        "user_query": user_query,
        "conversation_id": conversation_id,
        "conversation_history": conversation_history,
        "conversation_summary": conversation_summary,
        "query_type": "",
        "is_complex": False,
        "sub_queries": [],
        "agent_results": [],
        "final_response": "",
        "stream_queue": stream_queue,
        "runtime": runtime,
        "execution_path": [],
        "expected_branches": [],
        "completed_branches": [],
        "error": {"has_error": False},
        "is_error_state": False,
    }

    final_state: Optional[Dict[str, Any]] = None

    try:
        final_state = await runtime.compiled_graph.ainvoke(initial_state)
        final_response = final_state.get("final_response", "")
        logger.info(
            "Graph completed. Path: %s",
            " -> ".join(final_state.get("execution_path", [])),
        )
        return final_response

    except Exception as e:
        logger.error("Graph execution failed: %s", e, exc_info=True)
        error_msg = f"An error occurred while processing your request: {e}"
        try:
            await stream_queue.put({"type": "error", "message": error_msg})
            await stream_queue.put(None)
        except Exception:
            pass
        return error_msg

    finally:
        response_text = ""
        if final_state:
            response_text = final_state.get("final_response", "")

        if response_text:
            try:
                conv = conversation_store.append_turn(
                    conversation_id, user_query, response_text
                )
                summary_prompt = runtime.prompt_loader.get_system_prompt("conversation_summary")
                await conversation_store.compress_history(
                    conversation_id, runtime.llm_client, summary_prompt
                )
            except Exception as e:
                logger.error("Failed to save conversation: %s", e)
