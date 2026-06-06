from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.nodes.execution_node import execution_node
from app.nodes.query_analyzer import query_analyzer_node
from app.orchestrator.events import emit_progress
from app.orchestrator.state import GraphState


async def router_node(state: GraphState) -> dict:
    await emit_progress(state["stream_queue"], "Pipeline started")
    return {"execution_path": ["router_node"]}


def build_orchestrator_graph():
    graph = StateGraph(GraphState)

    graph.add_node("router_node", router_node)
    graph.add_node("query_analyzer", query_analyzer_node)
    graph.add_node("execution", execution_node)

    graph.set_entry_point("router_node")
    graph.add_edge("router_node", "query_analyzer")
    graph.add_edge("query_analyzer", "execution")
    graph.add_edge("execution", END)

    return graph.compile()
