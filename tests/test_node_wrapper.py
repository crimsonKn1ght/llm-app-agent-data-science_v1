from __future__ import annotations

import asyncio
import unittest

from app.orchestrator.node_wrapper import with_node_error_handling


class NodeWrapperTests(unittest.TestCase):
    def test_failed_node_returns_structured_error_result(self):
        async def failing_node(state):
            raise RuntimeError("backend exploded")

        state = {
            "user_query": "Search latest news",
            "conversation_id": "conv",
            "web_search": False,
            "conversation_history": [],
            "conversation_summary": "",
            "query_type": "web_search",
            "is_complex": False,
            "sub_queries": [{
                "sub_query_id": 1,
                "query": "Search latest news",
                "intent": "web_search",
                "scope": "in_scope",
                "scope_reasoning": "",
                "tool_hint": "web_search",
            }],
            "agent_results": [],
            "source_contents": {},
            "final_response": "",
            "compiler_metadata": {},
            "stream_queue": asyncio.Queue(),
            "runtime": object(),
            "execution_path": [],
            "expected_branches": ["web_search"],
            "completed_branches": [],
            "error": {"has_error": False},
            "is_error_state": False,
        }

        wrapped = with_node_error_handling(
            failing_node,
            node_name="web_search",
            branch_name="web_search",
        )
        result = asyncio.run(wrapped(state))

        self.assertEqual(result["execution_path"], ["web_search"])
        self.assertEqual(result["completed_branches"], ["web_search"])
        self.assertEqual(result["source_contents"], {})
        self.assertTrue(result["is_error_state"])
        self.assertEqual(result["agent_results"][0]["status"], "error")
        self.assertEqual(result["agent_results"][0]["source"], "web_search")
        self.assertNotIn("backend exploded", result["agent_results"][0]["result"])


if __name__ == "__main__":
    unittest.main()
