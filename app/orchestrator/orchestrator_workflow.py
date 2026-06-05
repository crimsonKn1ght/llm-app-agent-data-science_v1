from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.nodes.analytical_node import analytical_node
from app.nodes.compiler_node import compiler_node
from app.nodes.insights_node import insights_node
from app.nodes.query_analyzer import query_analyzer_node
from app.orchestrator.state import GraphState


async def router_node(state: GraphState) -> dict:
    return {
        "execution_path": ["router_node"],
        "expected_branches": ["internal"],
    }


def type_router(state: GraphState) -> str:
    if not state.get("sub_queries"):
        return "compiler"
    if state.get("query_type") == "analytical":
        return "analytical"
    return "insights"


def branch_router(state: GraphState) -> str:
    expected = set(state.get("expected_branches", []))
    completed = set(state.get("completed_branches", []))
    if expected.issubset(completed):
        return "compiler"
    return END


def build_orchestrator_graph():
    graph = StateGraph(GraphState)

    graph.add_node("router_node", router_node)
    graph.add_node("query_analyzer", query_analyzer_node)
    graph.add_node("analytical", analytical_node)
    graph.add_node("insights", insights_node)
    graph.add_node("compiler", compiler_node)

    graph.set_entry_point("router_node")

    graph.add_edge("router_node", "query_analyzer")

    graph.add_conditional_edges("query_analyzer", type_router, {
        "analytical": "analytical",
        "insights": "insights",
        "compiler": "compiler",
    })

    graph.add_conditional_edges("analytical", branch_router, {
        "compiler": "compiler",
        END: END,
    })

    graph.add_conditional_edges("insights", branch_router, {
        "compiler": "compiler",
        END: END,
    })

    graph.add_edge("compiler", END)

    return graph.compile()
