from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest

HAS_PYDANTIC = importlib.util.find_spec("pydantic") is not None

if HAS_PYDANTIC:
    from app.nodes import query_analyzer


class _FakePromptLoader:
    def get_system_prompt(self, name: str) -> str:
        return "Analyze query"

    def get_temperature(self, name: str) -> float:
        return 0.1

    def get_max_tokens(self, name: str) -> int:
        return 512


class _FakeLLMClient:
    def __init__(self, raw: str):
        self.raw = raw

    async def generate(self, **kwargs):
        return self.raw


class _FakeRuntime:
    def __init__(self, raw: str):
        self.llm_client = _FakeLLMClient(raw)
        self.prompt_loader = _FakePromptLoader()


@unittest.skipUnless(HAS_PYDANTIC, "pydantic is not installed in this Python environment")
class QueryAnalyzerValidationTests(unittest.TestCase):
    def _raw(self, payload: dict) -> str:
        return json.dumps(payload)

    def test_valid_analyzer_json_passes_validation(self):
        result = query_analyzer._parse_validate_repair_result(
            self._raw({
                "query_type": "insights",
                "is_complex": False,
                "sub_queries": [{
                    "query": "Explain transformers",
                    "intent": "insights",
                    "scope": "in_scope",
                    "scope_reasoning": "Knowledge query",
                    "tool_hint": "insights",
                }],
            }),
            "Explain transformers",
        )

        self.assertEqual(result["query_type"], "insights")
        self.assertEqual(result["sub_queries"][0]["tool_hint"], "insights")

    def test_markdown_fenced_json_parses(self):
        raw = """```json
{"query_type":"web_search","is_complex":false,"sub_queries":[{"query":"latest AI news","intent":"web_search","scope":"in_scope","tool_hint":"web_search"}]}
```"""
        result = query_analyzer._parse_validate_repair_result(raw, "latest AI news")

        self.assertEqual(result["query_type"], "insights")
        self.assertEqual(result["sub_queries"][0]["query"], "latest AI news")
        self.assertEqual(result["sub_queries"][0]["tool_hint"], "insights")

    def test_extra_text_around_json_is_extracted(self):
        raw = """Here is the result:
{"query_type":"insights","is_complex":false,"sub_queries":[{"query":"Explain AI","intent":"insights","scope":"in_scope","tool_hint":"insights"}]}
Thanks."""
        result = query_analyzer._parse_validate_repair_result(raw, "Explain AI")

        self.assertEqual(result["query_type"], "insights")

    def test_invalid_enum_values_are_normalized_safely(self):
        result = query_analyzer._parse_validate_repair_result(
            self._raw({
                "query_type": "made_up",
                "is_complex": False,
                "sub_queries": [{
                    "query": "Explain AI",
                    "intent": "made_up",
                    "scope": "strange",
                    "tool_hint": "unknown",
                }],
            }),
            "Explain AI",
        )

        self.assertEqual(result["query_type"], "insights")
        self.assertEqual(result["sub_queries"][0]["intent"], "insights")
        self.assertEqual(result["sub_queries"][0]["scope"], "in_scope")
        self.assertEqual(result["sub_queries"][0]["tool_hint"], "insights")

    def test_missing_sub_queries_triggers_fallback(self):
        with self.assertRaises(Exception):
            query_analyzer._parse_validate_repair_result(
                self._raw({"query_type": "insights", "is_complex": False}),
                "Explain AI",
            )

    def test_analytical_fallback_chooses_analytical(self):
        result = query_analyzer._fallback_result(
            "What is the average response time?",
            "invalid JSON",
        )

        self.assertEqual(result["query_type"], "analytical")
        self.assertEqual(result["sub_queries"][0]["tool_hint"], "analytical")

    def test_current_query_fallback_chooses_insights(self):
        result = query_analyzer._fallback_result(
            "What is the latest AI news today?",
            "invalid JSON",
        )

        self.assertEqual(result["query_type"], "insights")
        self.assertEqual(result["sub_queries"][0]["tool_hint"], "insights")

    def test_generic_fallback_chooses_insights(self):
        result = query_analyzer._fallback_result(
            "Explain neural networks",
            "invalid JSON",
        )

        self.assertEqual(result["query_type"], "insights")
        self.assertEqual(result["sub_queries"][0]["tool_hint"], "insights")

    def test_more_than_three_sub_queries_are_capped(self):
        result = query_analyzer._parse_validate_repair_result(
            self._raw({
                "query_type": "insights",
                "is_complex": True,
                "sub_queries": [
                    {
                        "query": f"Question {i}",
                        "intent": "insights",
                        "scope": "in_scope",
                        "tool_hint": "insights",
                    }
                    for i in range(5)
                ],
            }),
            "Complex question",
        )

        self.assertEqual(len(result["sub_queries"]), query_analyzer.MAX_SUB_QUERIES)
        self.assertTrue(result["is_complex"])

    def test_out_of_scope_validated_output_normalizes_to_insights(self):
        raw = self._raw({
            "query_type": "out_of_scope",
            "is_complex": False,
            "sub_queries": [{
                "query": "Write a poem",
                "intent": "insights",
                "scope": "out_of_scope",
                "tool_hint": "insights",
            }],
        })
        state = {
            "user_query": "Write a poem",
            "conversation_id": "conv",
            "web_search": False,
            "conversation_history": [],
            "conversation_summary": "",
            "query_type": "",
            "is_complex": False,
            "sub_queries": [],
            "agent_results": [],
            "source_contents": {},
            "final_response": "",
            "compiler_metadata": {},
            "stream_queue": asyncio.Queue(),
            "runtime": _FakeRuntime(raw),
            "execution_path": [],
            "expected_branches": [],
            "completed_branches": [],
            "error": {"has_error": False},
            "is_error_state": False,
        }

        result = asyncio.run(query_analyzer.query_analyzer_node(state))

        self.assertEqual(result["query_type"], "insights")
        self.assertEqual(result["agent_results"], [])
        self.assertEqual(result["sub_queries"][0]["tool_hint"], "insights")
        self.assertEqual(result["sub_queries"][0]["scope"], "in_scope")

    def test_web_search_flag_appends_synthetic_web_query(self):
        raw = self._raw({
            "query_type": "analytical",
            "is_complex": False,
            "sub_queries": [{
                "query": "How many launches happened?",
                "intent": "analytical",
                "scope": "in_scope",
                "tool_hint": "analytical",
            }],
        })
        state = {
            "user_query": "How many launches happened and what is latest news?",
            "conversation_id": "conv",
            "web_search": True,
            "conversation_history": [],
            "conversation_summary": "",
            "query_type": "",
            "is_complex": False,
            "sub_queries": [],
            "agent_results": [],
            "source_contents": {},
            "final_response": "",
            "compiler_metadata": {},
            "stream_queue": asyncio.Queue(),
            "runtime": _FakeRuntime(raw),
            "execution_path": [],
            "expected_branches": [],
            "completed_branches": [],
            "error": {"has_error": False},
            "is_error_state": False,
        }

        result = asyncio.run(query_analyzer.query_analyzer_node(state))

        self.assertEqual(
            [sq["tool_hint"] for sq in result["sub_queries"]],
            ["analytical", "web_search"],
        )
        self.assertEqual(result["expected_branches"], ["analytical", "web_search"])
        self.assertEqual(
            result["sub_queries"][-1]["scope_reasoning"],
            "Web search requested by API flag",
        )

    def test_web_search_false_does_not_append_web_query(self):
        raw = self._raw({
            "query_type": "insights",
            "is_complex": False,
            "sub_queries": [{
                "query": "Explain AI",
                "intent": "insights",
                "scope": "in_scope",
                "tool_hint": "insights",
            }],
        })
        state = {
            "user_query": "Explain AI",
            "conversation_id": "conv",
            "web_search": False,
            "conversation_history": [],
            "conversation_summary": "",
            "query_type": "",
            "is_complex": False,
            "sub_queries": [],
            "agent_results": [],
            "source_contents": {},
            "final_response": "",
            "compiler_metadata": {},
            "stream_queue": asyncio.Queue(),
            "runtime": _FakeRuntime(raw),
            "execution_path": [],
            "expected_branches": [],
            "completed_branches": [],
            "error": {"has_error": False},
            "is_error_state": False,
        }

        result = asyncio.run(query_analyzer.query_analyzer_node(state))

        self.assertEqual([sq["tool_hint"] for sq in result["sub_queries"]], ["insights"])
        self.assertEqual(result["expected_branches"], ["insights"])


if __name__ == "__main__":
    unittest.main()
