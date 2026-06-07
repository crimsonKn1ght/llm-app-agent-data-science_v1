from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

from app.logging_config import set_conversation_id
from app.memory import conversation_store
from app.orchestrator.state import GraphState
from runtime.runtime_config import get_runtime

logger = logging.getLogger(__name__)


async def orchestrate(
    user_query: str,
    conversation_id: Optional[str],
    stream_queue: asyncio.Queue,
) -> str:
    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    set_conversation_id(conversation_id)

    t_start = time.perf_counter()
    query_preview = user_query[:100] + "..." if len(user_query) > 100 else user_query
    logger.info("Request received | query=%r | query_len=%d", query_preview, len(user_query))

    runtime = get_runtime()
    conversation_history: list = []
    conversation_summary = ""

    conv_data = conversation_store.load(conversation_id)
    if conv_data is not None:
        conversation_history, conversation_summary = conversation_store.build_context(conv_data)
        logger.info(
            "Conversation context loaded | history_turns=%d | has_summary=%s",
            len(conversation_history),
            bool(conversation_summary),
        )
    else:
        logger.info("New conversation started")

    initial_state: GraphState = {
        "user_query": user_query,
        "conversation_id": conversation_id,
        "conversation_history": conversation_history,
        "conversation_summary": conversation_summary,
        "query_type": "",
        "is_complex": False,
        "sub_queries": [],
        "agent_results": [],
        "source_contents": {},
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
        elapsed = time.perf_counter() - t_start
        logger.info(
            "Pipeline completed | path=%s | response_chars=%d | elapsed=%.2fs",
            " -> ".join(final_state.get("execution_path", [])),
            len(final_response),
            elapsed,
        )
        return final_response

    except Exception as e:
        elapsed = time.perf_counter() - t_start
        logger.error(
            "Pipeline failed | elapsed=%.2fs | error=%s",
            elapsed, e, exc_info=True,
        )
        error_msg = f"An error occurred while processing your request: {e}"
        try:
            stream_queue.put_nowait({"type": "error", "message": error_msg})
            stream_queue.put_nowait(None)
        except Exception:
            pass
        return error_msg

    finally:
        response_text = ""
        if final_state:
            response_text = final_state.get("final_response", "")

        if response_text:
            try:
                conversation_store.append_turn(conversation_id, user_query, response_text)
                summary_prompt = runtime.prompt_loader.get_system_prompt("conversation_summary")
                await conversation_store.compress_history(
                    conversation_id, runtime.llm_client, summary_prompt
                )
                logger.info("Conversation turn saved | conv_id=%s", conversation_id)
            except Exception as e:
                logger.error("Failed to save conversation | error=%s", e)
