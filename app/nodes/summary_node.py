from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.orchestrator.events import emit_final_response, emit_progress, emit_text
from app.orchestrator.state import GraphState

logger = logging.getLogger(__name__)

SOURCE_HEADERS = {
    "insights": "## Insights",
    "analytical": "## Analysis",
    "web_search": "## Web Search",
}


async def summary_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    queue = state["stream_queue"]
    loader = runtime.prompt_loader
    source_contents: Dict[str, str] = state.get("source_contents", {})

    # Handle out-of-scope (no sources processed)
    if not source_contents:
        oos_results = [r for r in state.get("agent_results", []) if r["status"] == "out_of_scope"]
        if oos_results:
            msg = oos_results[0]["result"]
            await emit_text(queue, msg, origin="system")
            await emit_final_response(queue, msg)
            queue.put_nowait(None)
            return {
                "execution_path": ["summary"],
                "final_response": msg,
            }

    # Always generate summary from source contents
    await emit_progress(queue, "Generating summary...", origin="summary")
    await emit_text(queue, "## Summary\n\n", origin="summary")

    all_sources_text = "\n\n".join(
        f"[{SOURCE_HEADERS.get(key, key)}]\n{content}"
        for key, content in source_contents.items()
        if content.strip()
    )

    if not all_sources_text.strip():
        all_sources_text = "No information could be retrieved."

    user_input = f"Original question: {state['user_query']}\n\nSource information:\n{all_sources_text}"

    summary_parts: List[str] = []
    async for chunk in runtime.llm_client.stream(
        user_input=user_input,
        system_prompt=loader.get_system_prompt("final_summary"),
        temperature=loader.get_temperature("final_summary"),
        max_output_tokens=loader.get_max_tokens("final_summary"),
    ):
        summary_parts.append(chunk)
        await emit_text(queue, chunk, origin="summary")

    summary_text = "".join(summary_parts)
    await emit_text(queue, "\n\n", origin="summary")

    # Build final response: summary first, then individual sources
    final_sections: List[str] = ["## Summary\n\n" + summary_text]
    for key, content in source_contents.items():
        header = SOURCE_HEADERS.get(key, f"## {key.title()}")
        final_sections.append(f"{header}\n\n{content}")

    final_response = "\n\n".join(final_sections)

    await emit_final_response(queue, final_response)
    queue.put_nowait(None)
    await asyncio.sleep(0)

    logger.info(
        "Summary node completed | summary_chars=%d | sources=%s",
        len(summary_text), list(source_contents.keys()),
    )

    return {
        "execution_path": ["summary"],
        "final_response": final_response,
    }
