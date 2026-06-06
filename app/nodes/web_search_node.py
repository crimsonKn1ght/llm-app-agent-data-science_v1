from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from duckduckgo_search import AsyncDDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException

from app.orchestrator.state import AgentResult, GraphState

logger = logging.getLogger(__name__)

_PROMPT_NAME = "web_search"
MAX_SEARCH_RESULTS = 5


async def _search(query: str) -> List[Dict[str, str]]:
    try:
        results = await AsyncDDGS().atext(query, max_results=MAX_SEARCH_RESULTS)
        return results or []
    except DuckDuckGoSearchException as e:
        logger.warning("DuckDuckGo search error: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected search error: %s", e)
        return []


def _format_results(results: List[Dict[str, str]]) -> str:
    if not results:
        return "No search results were found for this query."
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


async def _process_sub_query(
    sub_query: Dict[str, Any],
    system_prompt: str,
    temperature: float,
    max_output_tokens: int,
    llm_client: Any,
) -> AgentResult:
    query = sub_query["query"]
    sub_id = sub_query["sub_query_id"]

    search_results = await _search(query)
    formatted = _format_results(search_results)

    user_input = f"User query: {query}\n\nWeb search results:\n{formatted}"

    try:
        answer = await llm_client.generate(
            user_input=user_input,
            system_prompt=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        status = "success" if search_results else "error"
        return {
            "sub_query_id": sub_id,
            "query": query,
            "agent_type": "web_search",
            "result": answer,
            "status": status,
        }
    except Exception as e:
        logger.error("Web search node failed for sub_query %d: %s", sub_id, e)
        return {
            "sub_query_id": sub_id,
            "query": query,
            "agent_type": "web_search",
            "result": f"Failed to generate an answer from search results: {e}",
            "status": "error",
        }


async def web_search_node(state: GraphState) -> Dict[str, Any]:
    runtime = state["runtime"]
    loader = runtime.prompt_loader
    system_prompt = loader.get_system_prompt(_PROMPT_NAME)
    temperature = loader.get_temperature(_PROMPT_NAME)
    max_output_tokens = loader.get_max_tokens(_PROMPT_NAME)

    sub_queries = state["sub_queries"]

    tasks = [
        _process_sub_query(
            sq, system_prompt, temperature, max_output_tokens,
            runtime.llm_client,
        )
        for sq in sub_queries
    ]
    results: List[AgentResult] = await asyncio.gather(*tasks)

    return {
        "execution_path": ["web_search"],
        "completed_branches": ["internal"],
        "agent_results": list(results),
    }
