from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, StateGraph

from app.nodes.analytical_node import analytical_node
from app.nodes.insights_node import insights_node
from app.nodes.query_analyzer import query_analyzer_node
from app.nodes.router_node import router_node
from app.nodes.summary_node import summary_node
from app.nodes.web_search_node import web_search_node
from app.orchestrator.state import GraphState


async def parallel_start_node(state: GraphState) -> Dict[str, Any]:
    return {"execution_path": ["parallel_start"]}


def route_after_analysis(state: GraphState) -> str:
    sub_queries = state.get("sub_queries", [])
    if not sub_queries:
        return "summary"

    tool_hints = set(sq["tool_hint"] for sq in sub_queries)

    if len(tool_hints) == 1:
        hint = tool_hints.pop()
        if hint in ("insights", "analytical", "web_search"):
            return hint
        return "insights"

    return "parallel"


def branch_router(state: GraphState) -> str:
    expected = set(state.get("expected_branches", []))
    completed = set(state.get("completed_branches", []))

    if not expected or expected.issubset(completed):
        return "summary"
    return "__end__"


def build_orchestrator_graph():
    graph = StateGraph(GraphState)

    graph.add_node("router_node", router_node)
    graph.add_node("query_analyzer", query_analyzer_node)
    graph.add_node("parallel_start", parallel_start_node)
    graph.add_node("insights", insights_node)
    graph.add_node("analytical", analytical_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("summary", summary_node)

    graph.set_entry_point("router_node")
    graph.add_edge("router_node", "query_analyzer")

    graph.add_conditional_edges("query_analyzer", route_after_analysis, {
        "insights": "insights",
        "analytical": "analytical",
        "web_search": "web_search",
        "parallel": "parallel_start",
        "summary": "summary",
    })

    graph.add_edge("parallel_start", "insights")
    graph.add_edge("parallel_start", "analytical")
    graph.add_edge("parallel_start", "web_search")

    graph.add_conditional_edges("insights", branch_router, {
        "summary": "summary",
        "__end__": END,
    })
    graph.add_conditional_edges("analytical", branch_router, {
        "summary": "summary",
        "__end__": END,
    })
    graph.add_conditional_edges("web_search", branch_router, {
        "summary": "summary",
        "__end__": END,
    })

    graph.add_edge("summary", END)

    return graph.compile()
