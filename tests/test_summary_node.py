from __future__ import annotations

import asyncio
import unittest

from app.nodes.summary_node import SUMMARY_FALLBACK_NOTICE, summary_node
from app.orchestrator.results import make_success_result


class _FakePromptLoader:
    def get_system_prompt(self, name: str) -> str:
        return "Summarize"

    def get_temperature(self, name: str) -> float:
        return 0.3

    def get_max_tokens(self, name: str) -> int:
        return 512


class _FailingStreamClient:
    async def stream(self, **kwargs):
        raise RuntimeError("summary model failed")
        yield ""


class _FakeRuntime:
    def __init__(self):
        self.llm_client = _FailingStreamClient()
        self.prompt_loader = _FakePromptLoader()


class SummaryFallbackTests(unittest.TestCase):
    def test_summary_failure_returns_raw_sections_and_final_event(self):
        queue = asyncio.Queue()
        state = {
            "user_query": "Compare AI basics and news",
            "conversation_id": "conv",
            "web_search": False,
            "conversation_history": [],
            "conversation_summary": "",
            "query_type": "insights",
            "is_complex": True,
            "sub_queries": [],
            "agent_results": [
                make_success_result(
                    sub_query_id=1,
                    query="Explain AI",
                    agent_type="insights",
                    result="AI basics.",
                    source="insights",
                ),
                make_success_result(
                    sub_query_id=2,
                    query="Latest AI news",
                    agent_type="web_search",
                    result="AI news.",
                    source="web_search",
                ),
            ],
            "source_contents": {
                "insights": "AI basics.",
                "web_search": "AI news.",
            },
            "final_response": "",
            "compiler_metadata": {},
            "stream_queue": queue,
            "runtime": _FakeRuntime(),
            "execution_path": [],
            "expected_branches": [],
            "completed_branches": [],
            "error": {"has_error": False},
            "is_error_state": False,
        }

        result = asyncio.run(summary_node(state))

        self.assertIn("## Insights", result["final_response"])
        self.assertIn("## Web Search", result["final_response"])
        self.assertIn(SUMMARY_FALLBACK_NOTICE, result["final_response"])

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        self.assertIsNone(events[-1])
        self.assertEqual(events[-2]["type"], "final_response")
        self.assertEqual(events[-2]["content"], result["final_response"])


if __name__ == "__main__":
    unittest.main()
