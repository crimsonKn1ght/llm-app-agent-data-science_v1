from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

from duckduckgo_search import DDGS

from app.orchestrator.events import emit_final_response, emit_progress, emit_text
from app.orchestrator.state import AgentResult, GraphState

logger = logging.getLogger(__name__)

SOURCE_HEADERS = {
    "insights": "## Insights",
    "analytical": "## Analysis",
    "web_search": "## Web Search",
}

PROGRESS_MESSAGES = {
    "insights": "Generating insights...",
    "analytical": "Running analytical reasoning...",
    "web_search": "Searching the web...",
}

NO_RELEVANT_ANSWER = "No relevant answer retrieved based on this query"
MAX_SEARCH_RESULTS = 5


# ── Shared helpers ──────────────────────────────────────────────────

def _build_qa_prompt(
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
    lines: List[str] = []
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


# ── Generate (non-streaming, for multi-query sources) ───────────────

async def _generate_qa(
    sub_query: Dict[str, Any], prompt_name: str, state: GraphState,
) -> AgentResult:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    query = sub_query["query"]
    sub_id = sub_query["sub_query_id"]

    user_input = _build_qa_prompt(
        query,
        state.get("conversation_summary", ""),
        state.get("conversation_history", []),
    )
    try:
        answer = await runtime.llm_client.generate(
            user_input=user_input,
            system_prompt=loader.get_system_prompt(prompt_name),
            temperature=loader.get_temperature(prompt_name),
            max_output_tokens=loader.get_max_tokens(prompt_name),
        )
        return {
            "sub_query_id": sub_id, "query": query,
            "agent_type": prompt_name, "result": answer, "status": "success",
        }
    except Exception as e:
        logger.error("%s failed for sub_query %d: %s", prompt_name, sub_id, e)
        return {
            "sub_query_id": sub_id, "query": query,
            "agent_type": prompt_name, "result": NO_RELEVANT_ANSWER, "status": "error",
        }


async def _generate_web(
    sub_query: Dict[str, Any], state: GraphState,
) -> AgentResult:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    query = sub_query["query"]
    sub_id = sub_query["sub_query_id"]

    search_results = await _search(query)
    if not search_results:
        return {
            "sub_query_id": sub_id, "query": query,
            "agent_type": "web_search", "result": NO_RELEVANT_ANSWER, "status": "error",
        }

    user_input = f"Query: {query}\n\nSearch results:\n{_format_results_for_llm(search_results)}"
    try:
        answer = await runtime.llm_client.generate(
            user_input=user_input,
            system_prompt=loader.get_system_prompt("web_search"),
            temperature=loader.get_temperature("web_search"),
            max_output_tokens=loader.get_max_tokens("web_search"),
        )
        return {
            "sub_query_id": sub_id, "query": query,
            "agent_type": "web_search", "result": answer, "status": "success",
        }
    except Exception as e:
        logger.error("web_search failed for sub_query %d: %s", sub_id, e)
        return {
            "sub_query_id": sub_id, "query": query,
            "agent_type": "web_search", "result": NO_RELEVANT_ANSWER, "status": "error",
        }


async def _generate_one(
    hint: str, sub_query: Dict[str, Any], state: GraphState,
) -> AgentResult:
    if hint == "web_search":
        return await _generate_web(sub_query, state)
    return await _generate_qa(sub_query, hint, state)


# ── Stream (single-query source, tokens go straight to queue) ───────

async def _stream_single_qa(
    sub_query: Dict[str, Any], prompt_name: str,
    queue: asyncio.Queue, origin: str, state: GraphState,
) -> str:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    user_input = _build_qa_prompt(
        sub_query["query"],
        state.get("conversation_summary", ""),
        state.get("conversation_history", []),
    )
    accumulated = ""
    try:
        async for chunk in runtime.llm_client.stream(
            user_input=user_input,
            system_prompt=loader.get_system_prompt(prompt_name),
            temperature=loader.get_temperature(prompt_name),
            max_output_tokens=loader.get_max_tokens(prompt_name),
        ):
            await emit_text(queue, chunk, origin=origin)
            accumulated += chunk
    except Exception as e:
        logger.error("Streaming %s failed: %s", prompt_name, e)
        if not accumulated:
            accumulated = NO_RELEVANT_ANSWER
            await emit_text(queue, accumulated, origin=origin)
    return accumulated


async def _stream_single_web(
    sub_query: Dict[str, Any],
    queue: asyncio.Queue, state: GraphState,
) -> str:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    query = sub_query["query"]
    origin = "web_search"

    search_results = await _search(query)
    if not search_results:
        await emit_text(queue, NO_RELEVANT_ANSWER + "\n", origin=origin)
        return NO_RELEVANT_ANSWER

    user_input = f"Query: {query}\n\nSearch results:\n{_format_results_for_llm(search_results)}"
    accumulated = ""
    try:
        async for chunk in runtime.llm_client.stream(
            user_input=user_input,
            system_prompt=loader.get_system_prompt("web_search"),
            temperature=loader.get_temperature("web_search"),
            max_output_tokens=loader.get_max_tokens("web_search"),
        ):
            await emit_text(queue, chunk, origin=origin)
            accumulated += chunk
    except Exception as e:
        logger.error("Web search streaming failed: %s", e)
        if not accumulated:
            accumulated = NO_RELEVANT_ANSWER
            await emit_text(queue, accumulated, origin=origin)
    return accumulated


# ── Compile (multi-result synthesis, streamed) ──────────────────────

async def _compile_and_stream(
    results: List[AgentResult], queue: asyncio.Queue,
    origin: str, state: GraphState,
) -> str:
    runtime = state["runtime"]
    loader = runtime.prompt_loader

    sub_answers = [
        f"Sub-answer {i} (for: {r['query']}):\n{r['result']}"
        for i, r in enumerate(results, start=1)
    ]
    user_input = (
        f"Original user question: {state['user_query']}\n\n"
        + "\n\n---\n\n".join(sub_answers)
    )

    accumulated = ""
    try:
        async for chunk in runtime.llm_client.stream(
            user_input=user_input,
            system_prompt=loader.get_system_prompt("source_synthesis"),
            temperature=loader.get_temperature("source_synthesis"),
            max_output_tokens=loader.get_max_tokens("source_synthesis"),
        ):
            await emit_text(queue, chunk, origin=origin)
            accumulated += chunk
    except Exception as e:
        logger.error("Compilation streaming failed for %s: %s", origin, e)
        fallback = "\n\n".join(r["result"] for r in results)
        if not accumulated:
            accumulated = fallback
            await emit_text(queue, fallback, origin=origin)
    return accumulated


# ── Chunk pre-generated text to queue ───────────────────────────────

async def _stream_text_chunks(
    text: str, queue: asyncio.Queue, origin: str,
) -> None:
    chunk_size = 80
    for i in range(0, len(text), chunk_size):
        await emit_text(queue, text[i : i + chunk_size], origin=origin)


# ── Main execution node ────────────────────────────────────────────

async def execution_node(state: GraphState) -> Dict[str, Any]:
    queue = state["stream_queue"]
    runtime = state["runtime"]
    sub_queries = state["sub_queries"]
    user_query = state["user_query"]

    # Handle empty (all out-of-scope)
    if not sub_queries:
        msg = "This question is outside the scope of what I can help with."
        await emit_text(queue, msg)
        queue.put_nowait(None)
        return {
            "execution_path": ["execution"],
            "final_response": msg,
            "agent_results": [],
        }

    # ── Group sub-queries by tool_hint (the gate) ──
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for sq in sub_queries:
        hint = sq.get("tool_hint", "insights")
        groups.setdefault(hint, []).append(sq)

    is_single_source = len(groups) == 1
    all_agent_results: List[AgentResult] = []
    source_contents: Dict[str, str] = {}

    if is_single_source:
        hint = next(iter(groups))
        queries = groups[hint]
        origin = hint
        header = SOURCE_HEADERS.get(hint, f"## {hint.replace('_', ' ').title()}")

        await emit_progress(queue, PROGRESS_MESSAGES.get(hint, f"Processing {hint}..."), origin=origin)
        await emit_text(queue, header + "\n", origin=origin)

        if len(queries) == 1:
            # ── Single source, single query → stream directly ──
            if hint == "web_search":
                content = await _stream_single_web(queries[0], queue, state)
            else:
                content = await _stream_single_qa(queries[0], hint, queue, origin, state)

            all_agent_results.append({
                "sub_query_id": queries[0]["sub_query_id"],
                "query": queries[0]["query"],
                "agent_type": hint,
                "result": content,
                "status": "success" if content != NO_RELEVANT_ANSWER else "error",
            })
        else:
            # ── Single source, multi-query → generate all, compile + stream ──
            tasks = [_generate_one(hint, sq, state) for sq in queries]
            results = await asyncio.gather(*tasks)
            all_agent_results.extend(results)

            success = [r for r in results if r["status"] == "success"]
            if success:
                content = await _compile_and_stream(success, queue, origin, state)
            else:
                content = NO_RELEVANT_ANSWER
                await emit_text(queue, content + "\n", origin=origin)

        source_contents[hint] = content

    else:
        # ── Multiple source types → generate concurrently, stream in completion order ──

        async def _process_source(h: str, qs: List[Dict[str, Any]]) -> List[AgentResult]:
            coros = [_generate_one(h, sq, state) for sq in qs]
            return list(await asyncio.gather(*coros))

        for h in groups:
            await emit_progress(queue, PROGRESS_MESSAGES.get(h, f"Processing {h}..."), origin=h)

        source_tasks: Dict[str, asyncio.Task] = {
            h: asyncio.create_task(_process_source(h, qs))
            for h, qs in groups.items()
        }
        task_to_hint = {source_tasks[h]: h for h in groups}

        remaining = set(source_tasks.keys())
        while remaining:
            done, _ = await asyncio.wait(
                {source_tasks[h] for h in remaining},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                h = task_to_hint[task]
                remaining.discard(h)

                results = task.result()
                all_agent_results.extend(results)

                origin = h
                header = SOURCE_HEADERS.get(h, f"## {h.replace('_', ' ').title()}")
                await emit_text(queue, "\n" + header + "\n", origin=origin)

                success = [r for r in results if r["status"] == "success"]
                if not success:
                    content = NO_RELEVANT_ANSWER
                    await emit_text(queue, content + "\n", origin=origin)
                elif len(success) == 1:
                    content = success[0]["result"]
                    await _stream_text_chunks(content, queue, origin)
                else:
                    content = await _compile_and_stream(success, queue, origin, state)

                source_contents[h] = content

    # ── Summary (only when multi-query or multi-source) ──
    needs_summary = len(sub_queries) > 1 or len(groups) > 1
    summary_text = ""

    if needs_summary:
        await emit_progress(queue, "Generating summary...", origin="summary")
        await emit_text(queue, "\n## Summary\n", origin="summary")

        source_sections = []
        for h, content in source_contents.items():
            label = SOURCE_HEADERS.get(h, h).lstrip("#").strip()
            source_sections.append(f"From {label}:\n{content}")

        summary_input = (
            f"User question: {user_query}\n\n"
            + "\n\n---\n\n".join(source_sections)
        )
        loader = runtime.prompt_loader
        try:
            async for chunk in runtime.llm_client.stream(
                user_input=summary_input,
                system_prompt=loader.get_system_prompt("final_summary"),
                temperature=loader.get_temperature("final_summary"),
                max_output_tokens=loader.get_max_tokens("final_summary"),
            ):
                await emit_text(queue, chunk, origin="summary")
                summary_text += chunk
        except Exception as e:
            logger.error("Summary generation failed: %s", e)
            if not summary_text:
                summary_text = "Summary generation failed."
                await emit_text(queue, summary_text, origin="summary")

    # ── Build structured final_response ──
    final_parts: List[str] = []
    if summary_text:
        final_parts.append("## Summary\n" + summary_text)
    for h in source_contents:
        header = SOURCE_HEADERS.get(h, f"## {h.replace('_', ' ').title()}")
        final_parts.append(header + "\n" + source_contents[h])
    final_response = "\n\n".join(final_parts)

    await emit_final_response(queue, final_response)
    queue.put_nowait(None)

    return {
        "execution_path": ["execution"],
        "final_response": final_response,
        "agent_results": all_agent_results,
    }
