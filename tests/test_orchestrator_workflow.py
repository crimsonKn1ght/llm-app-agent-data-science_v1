from __future__ import annotations

import importlib.util
import unittest

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

if HAS_LANGGRAPH:
    from app.orchestrator.orchestrator_workflow import route_after_analysis


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph is not installed in this Python environment")
class OrchestratorWorkflowRoutingTests(unittest.TestCase):
    def test_web_search_false_insights_routes_insights_only(self):
        route = route_after_analysis({
            "web_search": False,
            "sub_queries": [{
                "sub_query_id": 1,
                "query": "Explain AI",
                "intent": "insights",
                "scope": "in_scope",
                "scope_reasoning": "",
                "tool_hint": "insights",
            }],
        })

        self.assertEqual(route, "insights")

    def test_web_search_false_analytical_routes_analytical_only(self):
        route = route_after_analysis({
            "web_search": False,
            "sub_queries": [{
                "sub_query_id": 1,
                "query": "How many?",
                "intent": "analytical",
                "scope": "in_scope",
                "scope_reasoning": "",
                "tool_hint": "analytical",
            }],
        })

        self.assertEqual(route, "analytical")

    def test_web_search_true_with_insights_routes_parallel(self):
        route = route_after_analysis({
            "web_search": True,
            "sub_queries": [
                {
                    "sub_query_id": 1,
                    "query": "Explain AI",
                    "intent": "insights",
                    "scope": "in_scope",
                    "scope_reasoning": "",
                    "tool_hint": "insights",
                },
                {
                    "sub_query_id": 2,
                    "query": "Explain AI",
                    "intent": "web_search",
                    "scope": "in_scope",
                    "scope_reasoning": "Web search requested by API flag",
                    "tool_hint": "web_search",
                },
            ],
        })

        self.assertEqual(route, "insights_web")

    def test_web_search_true_with_analytical_routes_parallel(self):
        route = route_after_analysis({
            "web_search": True,
            "sub_queries": [
                {
                    "sub_query_id": 1,
                    "query": "How many?",
                    "intent": "analytical",
                    "scope": "in_scope",
                    "scope_reasoning": "",
                    "tool_hint": "analytical",
                },
                {
                    "sub_query_id": 2,
                    "query": "How many?",
                    "intent": "web_search",
                    "scope": "in_scope",
                    "scope_reasoning": "Web search requested by API flag",
                    "tool_hint": "web_search",
                },
            ],
        })

        self.assertEqual(route, "analytical_web")

    def test_insights_and_analytical_routes_without_web(self):
        route = route_after_analysis({
            "web_search": False,
            "sub_queries": [
                {
                    "sub_query_id": 1,
                    "query": "Explain AI",
                    "intent": "insights",
                    "scope": "in_scope",
                    "scope_reasoning": "",
                    "tool_hint": "insights",
                },
                {
                    "sub_query_id": 2,
                    "query": "How many?",
                    "intent": "analytical",
                    "scope": "in_scope",
                    "scope_reasoning": "",
                    "tool_hint": "analytical",
                },
            ],
        })

        self.assertEqual(route, "insights_analytical")

    def test_all_three_tools_routes_all(self):
        route = route_after_analysis({
            "web_search": True,
            "sub_queries": [
                {
                    "sub_query_id": 1,
                    "query": "Explain AI",
                    "intent": "insights",
                    "scope": "in_scope",
                    "scope_reasoning": "",
                    "tool_hint": "insights",
                },
                {
                    "sub_query_id": 2,
                    "query": "How many?",
                    "intent": "analytical",
                    "scope": "in_scope",
                    "scope_reasoning": "",
                    "tool_hint": "analytical",
                },
                {
                    "sub_query_id": 3,
                    "query": "Latest AI news",
                    "intent": "web_search",
                    "scope": "in_scope",
                    "scope_reasoning": "Web search requested by API flag",
                    "tool_hint": "web_search",
                },
            ],
        })

        self.assertEqual(route, "all")


if __name__ == "__main__":
    unittest.main()
