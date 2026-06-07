from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.orchestrator.events import emit_progress, emit_text
from app.orchestrator.state import AgentResult, GraphState

logger = logging.getLogger(__name__)

_PROMPT_NAME = "compiler_synthesis"
OUT_OF_SCOPE_MSG = "This question is outside the scope of what I can help with."


async def compiler_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    system_prompt = loader.get_system_prompt(_PROMPT_NAME)
    temperature = loader.get_temperature(_PROMPT_NAME)
    max_output_tokens = loader.get_max_tokens(_PROMPT_NAME)

    user_query = state["user_query"]
    queue: asyncio.Queue = state["stream_queue"]
    agent_results: List[AgentResult] = state.get("agent_results", [])

    success_results = [r for r in agent_results if r["status"] == "success"]
    error_results = [r for r in agent_results if r["status"] == "error"]
    oos_results = [r for r in agent_results if r["status"] == "out_of_scope"]

    accumulated = ""

    try:
        if not success_results and not error_results:
            text = OUT_OF_SCOPE_MSG
            await emit_text(queue, text)
            accumulated = text
        elif not success_results and error_results:
            text = "I encountered errors while processing your request. Please try again."
            for er in error_results:
                text += f"\n\n**Error for:** {er['query']}\n{er['result']}"
            await emit_text(queue, text)
            accumulated = text
        elif len(success_results) == 1 and not oos_results:
            if success_results[0].get("status") == "streamed":
                accumulated = success_results[0]["result"]
            else:
                answer = success_results[0]["result"]
                accumulated = await _stream_text(answer, queue)
        else:
            await emit_progress(queue, f"Synthesizing response from {len(success_results)} source(s)...")
            accumulated = await _synthesize_and_stream(
                user_query, success_results, oos_results,
                system_prompt, temperature, max_output_tokens, runtime.llm_client, queue,
            )
    except Exception as e:
        logger.error("Compiler node failed: %s", e)
        error_text = f"An error occurred while compiling the response: {e}"
        await emit_text(queue, error_text)
        accumulated = error_text
    finally:
        queue.put_nowait(None)

    return {
        "execution_path": ["compiler"],
        "final_response": accumulated,
    }


async def _stream_text(text: str, queue: asyncio.Queue) -> str:
    chunk_size = 80
    for i in range(0, len(text), chunk_size):
        await emit_text(queue, text[i : i + chunk_size])
        await asyncio.sleep(0)
    return text


async def _synthesize_and_stream(
    user_query: str,
    success_results: List[AgentResult],
    oos_results: List[AgentResult],
    system_prompt: str,
    temperature: float,
    max_output_tokens: int,
    llm_client: Any,
    queue: asyncio.Queue,
) -> str:
    sub_answers = [
        f"Sub-answer {i} (for: {r['query']}):\n{r['result']}"
        for i, r in enumerate(success_results, start=1)
    ]
    if oos_results:
        oos_lines = "\n".join(
            f"- \"{r['query']}\": {OUT_OF_SCOPE_MSG}" for r in oos_results
        )
        sub_answers.append(
            f"Out-of-scope queries (mention briefly that these could not be addressed):\n{oos_lines}"
        )

    user_input = (
        f"Original user question: {user_query}\n\n"
        + "\n\n---\n\n".join(sub_answers)
    )

    accumulated = ""
    try:
        async for chunk in llm_client.stream(
            user_input=user_input,
            system_prompt=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        ):
            await emit_text(queue, chunk)
            accumulated += chunk
    except Exception as e:
        logger.error("Streaming synthesis failed: %s", e)
        fallback = "\n\n---\n\n".join(r["result"] for r in success_results)
        if accumulated:
            remaining = fallback[len(accumulated):]
            await emit_text(queue, remaining)
            accumulated += remaining
        else:
            await emit_text(queue, fallback)
            accumulated = fallback

    return accumulated
