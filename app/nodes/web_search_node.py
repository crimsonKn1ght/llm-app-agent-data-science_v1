from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from duckduckgo_search import DDGS

from app.orchestrator.events import emit_progress, emit_text
from app.orchestrator.results import make_success_result, normalize_citations
from app.orchestrator.state import DecomposedQuery, GraphState

logger = logging.getLogger(__name__)

SOURCE_KEY = "web_search"
HEADER = "## Web Search\n\n"
NO_RELEVANT_ANSWER = "No relevant answer retrieved based on this query"
MAX_SEARCH_RESULTS = 5
WEB_SEARCH_TIMEOUT_SECONDS = float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "15"))
WEB_SEARCH_MAX_RETRIES = int(os.getenv("WEB_SEARCH_MAX_RETRIES", "1"))
WEB_SEARCH_RETRY_BACKOFF_SECONDS = 0.25


def _get_my_queries(state: GraphState) -> List[DecomposedQuery]:
    return [sq for sq in state["sub_queries"] if sq["tool_hint"] == SOURCE_KEY]


# ── Search backends ──────────────────────────────────────────────────


def _ddgs_text(query: str) -> List[Dict[str, str]]:
    backends = ("auto", "html", "lite")
    for i, backend in enumerate(backends):
        try:
            if i > 0:
                time.sleep(1.5)
            logger.info("DDGS attempting backend=%s | query=%r", backend, query[:60])
            results = DDGS().text(query, max_results=MAX_SEARCH_RESULTS, backend=backend)
            if results:
                logger.info("DDGS success | backend=%s | results=%d", backend, len(results))
                return results
        except Exception as e:
            logger.warning("DDGS backend=%s failed | error=%s: %s", backend, type(e).__name__, e)
    logger.warning("DDGS exhausted all backends for: %s", query[:60])
    return []


def _brave_search(query: str) -> List[Dict[str, str]]:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "")
    if not api_key:
        return []

    params = urllib.parse.urlencode({"q": query, "count": MAX_SEARCH_RESULTS})
    url = f"https://api.search.brave.com/res/v1/web/search?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=WEB_SEARCH_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("web", {}).get("results", [])
        results = [
            {
                "title": item.get("title", ""),
                "href": item.get("url", ""),
                "body": item.get("description", ""),
                "provider": "brave",
            }
            for item in items
        ]
        logger.info("Brave Search returned %d results for: %s", len(results), query[:60])
        return results
    except Exception as e:
        logger.warning("Brave Search failed | error=%s: %s", type(e).__name__, e)
        return []


async def _search(query: str) -> List[Dict[str, str]]:
    last_error: Exception | None = None

    for attempt in range(WEB_SEARCH_MAX_RETRIES + 1):
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_ddgs_text, query),
                timeout=WEB_SEARCH_TIMEOUT_SECONDS,
            )
            if results:
                return [{**r, "provider": r.get("provider", "duckduckgo")} for r in results]

            if os.getenv("BRAVE_SEARCH_API_KEY"):
                logger.info("DDGS returned no results, trying Brave Search fallback")
                results = await asyncio.wait_for(
                    asyncio.to_thread(_brave_search, query),
                    timeout=WEB_SEARCH_TIMEOUT_SECONDS,
                )
            return results
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Web search attempt failed | attempt=%d/%d | error=%s",
                attempt + 1,
                WEB_SEARCH_MAX_RETRIES + 1,
                type(exc).__name__,
            )
            if attempt < WEB_SEARCH_MAX_RETRIES:
                await asyncio.sleep(WEB_SEARCH_RETRY_BACKOFF_SECONDS * (attempt + 1))

    assert last_error is not None
    raise last_error


# ── Result formatting ────────────────────────────────────────────────


def _format_results_for_llm(results: List[Dict[str, str]]) -> str:
    if not results:
        return "No search results found."
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        body = r.get("body", "")
        href = r.get("href", "")
        lines.append(f"[{i}] {title}\n    {body}\n    URL: {href}")
    return "\n\n".join(lines)


# ── Node implementation ──────────────────────────────────────────────


async def _stream_web_answer(state: GraphState, search_context: str, query: str) -> str:
    runtime = state["runtime"]
    queue = state["stream_queue"]
    loader = runtime.prompt_loader

    user_input = f"Query: {query}\n\nSearch results:\n{search_context}"

    collected: List[str] = []
    async for chunk in runtime.llm_client.stream(
        user_input=user_input,
        system_prompt=loader.get_system_prompt("web_search"),
        temperature=loader.get_temperature("web_search"),
        max_output_tokens=loader.get_max_tokens("web_search"),
    ):
        collected.append(chunk)
        await emit_text(queue, chunk, origin=SOURCE_KEY)

    return "".join(collected)


async def _generate_web_answer(state: GraphState, search_context: str, query: str) -> str:
    runtime = state["runtime"]
    loader = runtime.prompt_loader

    user_input = f"Query: {query}\n\nSearch results:\n{search_context}"

    return await runtime.llm_client.generate(
        user_input=user_input,
        system_prompt=loader.get_system_prompt("web_search"),
        temperature=loader.get_temperature("web_search"),
        max_output_tokens=loader.get_max_tokens("web_search"),
    )


async def web_search_node(state: GraphState) -> Dict[str, Any]:
    started = time.perf_counter()
    my_queries = _get_my_queries(state)

    if not my_queries:
        logger.debug("Web search node: no queries to process, skipping")
        return {
            "execution_path": ["web_search"],
            "completed_branches": ["web_search"],
            "agent_results": [],
            "source_contents": {},
        }

    queue = state["stream_queue"]
    await emit_progress(queue, "Searching the web...", origin=SOURCE_KEY)
    await emit_text(queue, HEADER, origin=SOURCE_KEY)

    all_search_results: List[Dict[str, str]] = []
    synthesis_used = len(my_queries) > 1

    if len(my_queries) == 1:
        query = my_queries[0]["query"]
        search_results = await _search(query)
        all_search_results.extend(search_results)
        search_context = _format_results_for_llm(search_results)
        logger.info("Web search completed | results=%d | query=%r", len(search_results), query[:60])
        result_text = await _stream_web_answer(state, search_context, query)
    else:
        logger.info("Web search node: processing %d sub-queries", len(my_queries))
        sub_answers: List[str] = []
        for sq in my_queries:
            search_results = await _search(sq["query"])
            all_search_results.extend(search_results)
            search_context = _format_results_for_llm(search_results)
            answer = await _generate_web_answer(state, search_context, sq["query"])
            if answer and answer.strip():
                sub_answers.append(answer)

        if not sub_answers:
            result_text = NO_RELEVANT_ANSWER
            await emit_text(queue, result_text, origin=SOURCE_KEY)
        else:
            runtime = state["runtime"]
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
            result_text = "".join(collected)

    await emit_text(queue, "\n\n", origin=SOURCE_KEY)

    if not result_text or not result_text.strip():
        result_text = NO_RELEVANT_ANSWER

    logger.info("Web search node completed | result_chars=%d", len(result_text))

    citations = normalize_citations(all_search_results, default_provider="web_search")
    latency_ms = int((time.perf_counter() - started) * 1000)
    agent_results = [
        make_success_result(
            sub_query_id=sq["sub_query_id"],
            query=sq["query"],
            agent_type=SOURCE_KEY,
            result=result_text,
            source=SOURCE_KEY,
            latency_ms=latency_ms,
            citations=citations,
            confidence="unknown",
            tool_metadata={
                "intent": sq["intent"],
                "prompt_name": "source_synthesis" if synthesis_used else "web_search",
                "sub_query_count": len(my_queries),
                "synthesis_used": synthesis_used,
                "search_result_count": len(all_search_results),
            },
        )
        for sq in my_queries
    ]

    return {
        "execution_path": ["web_search"],
        "completed_branches": ["web_search"],
        "agent_results": agent_results,
        "source_contents": {SOURCE_KEY: result_text},
    }
