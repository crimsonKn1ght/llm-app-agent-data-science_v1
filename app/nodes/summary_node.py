from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from app.orchestrator.events import emit_final_response, emit_progress, emit_text
from app.orchestrator.compiler import (
    assemble_with_summary,
    append_citations,
    plan_compilation,
    render_sections,
)
from app.orchestrator.state import GraphState

logger = logging.getLogger(__name__)

SUMMARY_FALLBACK_NOTICE = (
    "## Notices\n\n"
    "- Summary generation failed; showing available source output instead."
)


async def summary_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    queue = state["stream_queue"]
    loader = runtime.prompt_loader
    source_contents: Dict[str, str] = state.get("source_contents", {})
    agent_results = state.get("agent_results", [])

    compilation = plan_compilation(
        user_query=state["user_query"],
        source_contents=source_contents,
        agent_results=agent_results,
    )

    if compilation.strategy in {"out_of_scope", "error_only"}:
        await emit_text(queue, compilation.final_response, origin="system")
        await emit_final_response(queue, compilation.final_response)
        queue.put_nowait(None)
        return {
            "execution_path": ["summary"],
            "final_response": compilation.final_response,
            "compiler_metadata": compilation.metadata,
        }

    if compilation.strategy == "passthrough":
        if compilation.error_notice:
            await emit_text(queue, "\n\n" + compilation.error_notice, origin="summary")
        await emit_final_response(queue, compilation.final_response)
        queue.put_nowait(None)
        return {
            "execution_path": ["summary"],
            "final_response": compilation.final_response,
            "compiler_metadata": compilation.metadata,
        }

    try:
        await emit_progress(queue, "Generating summary...", origin="summary")
        await emit_text(queue, "## Summary\n\n", origin="summary")

        summary_parts: List[str] = []
        async for chunk in runtime.llm_client.stream(
            user_input=compilation.summary_input,
            system_prompt=loader.get_system_prompt("final_summary"),
            temperature=loader.get_temperature("final_summary"),
            max_output_tokens=loader.get_max_tokens("final_summary"),
        ):
            summary_parts.append(chunk)
            await emit_text(queue, chunk, origin="summary")

        summary_text = "".join(summary_parts)
        await emit_text(queue, "\n\n", origin="summary")

        if compilation.error_notice:
            await emit_text(queue, compilation.error_notice + "\n\n", origin="summary")

        final_response = assemble_with_summary(
            summary_text=summary_text,
            sections=compilation.sections,
            error_notice=compilation.error_notice,
            citations=compilation.citations,
        )
    except Exception as exc:
        logger.error("Summary generation failed; using raw compiler output | error=%s", exc, exc_info=True)
        notices = "\n\n".join(
            part for part in (compilation.error_notice, SUMMARY_FALLBACK_NOTICE)
            if part and part.strip()
        )
        final_response = render_sections(compilation.sections)
        if notices:
            final_response = "\n\n".join(part for part in (final_response, notices) if part)
        final_response = append_citations(final_response, compilation.citations)
        await emit_text(queue, "\n\n" + SUMMARY_FALLBACK_NOTICE + "\n\n", origin="summary")

    await emit_final_response(queue, final_response)
    queue.put_nowait(None)
    await asyncio.sleep(0)

    logger.info(
        "Summary node completed | summary_chars=%d | sources=%s",
        len(final_response), list(source_contents.keys()),
    )

    return {
        "execution_path": ["summary"],
        "final_response": final_response,
        "compiler_metadata": compilation.metadata,
    }
