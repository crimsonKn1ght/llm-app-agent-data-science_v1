from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from duckduckgo_search import DDGS

from app.orchestrator.events import emit_final_response, emit_progress, emit_text
from app.orchestrator.state import AgentResult, GraphState

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = "web_search_summary"
MAX_SEARCH_RESULTS = 5
NO_ANSWER = "No relevant answer retrieved based on this query"


def _ddgs_text(query: str) -> List[Dict[str, str]]:
    return list(DDGS().text(query, max_results=MAX_SEARCH_RESULTS))


async def _search(query: str) -> List[Dict[str, str]]:
    try:
        results = await asyncio.to_thread(_ddgs_text, query)
        return results or []
    except Exception as e:
        logger.warning("DuckDuckGo search error: %s", e)
        return []


def _format_results_for_llm(results: List[Dict[str, str]]) -> str:
    if not results:
        return "No search results found."
    lines = []
    for i, r in enumerate(results, start=1):
        title = r.get("title", "No title")
        url = r.get("href", "")
        body = r.get("body", "")
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(f"    URL: {url}")
        if body:
            lines.append(f"    {body}")
        lines.append("")
    return "\n".join(lines)


async def web_search_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    queue = state["stream_queue"]

    await emit_progress(queue, "Searching the web...")

    sub_query = state["sub_queries"][0]
    query = sub_query["query"]
    sub_id = sub_query["sub_query_id"]

    search_results = await _search(query)

    # --- Stream each source immediately after search returns ---
    source_blocks: List[str] = []

    if not search_results:
        block = NO_ANSWER
        await emit_text(queue, block + "\n")
        source_blocks.append(block)
    else:
        for i, r in enumerate(search_results, start=1):
            title = r.get("title", "No title")
            url = r.get("href", "")
            body = (r.get("body", "") or "").strip() or NO_ANSWER

            block = f"**[{i}] {title}**"
            if url:
                block += f"\n{url}"
            block += f"\n{body}"
            source_blocks.append(block)
            await emit_text(queue, block + "\n\n")

    sources_section = "\n\n".join(source_blocks)

    # --- LLM summary: bullet-point synthesis, streamed ---
    summary_text = ""
    if search_results:
        await emit_progress(queue, "Generating summary...")

        system_prompt = loader.get_system_prompt(_SUMMARY_PROMPT)
        temperature = loader.get_temperature(_SUMMARY_PROMPT)
        max_output_tokens = loader.get_max_tokens(_SUMMARY_PROMPT)

        user_input = f"Query: {query}\n\nSearch results:\n{_format_results_for_llm(search_results)}"

        divider = "\n---\n\n**Summary**\n"
        await emit_text(queue, divider)
        summary_text = divider

        try:
            async for chunk in runtime.llm_client.stream(
                user_input=user_input,
                system_prompt=system_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ):
                await emit_text(queue, chunk)
                summary_text += chunk
        except Exception as e:
            logger.error("Web search summary failed: %s", e)
            err = "Summary generation failed."
            await emit_text(queue, err)
            summary_text += err

    # --- Emit structured final_response (summary first, then sources) ---
    if summary_text.strip():
        structured = summary_text.strip() + "\n\n---\n\n**Sources**\n\n" + sources_section
    else:
        structured = sources_section

    await emit_final_response(queue, structured)

    return {
        "execution_path": ["web_search"],
        "completed_branches": ["internal"],
        "agent_results": [{
            "sub_query_id": sub_id,
            "query": query,
            "agent_type": "web_search",
            "result": structured,
            "status": "streamed",
        }],
    }
