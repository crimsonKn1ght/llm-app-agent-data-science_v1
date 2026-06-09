from __future__ import annotations

import unittest

from app.orchestrator.results import (
    make_error_result,
    make_out_of_scope_result,
    make_success_result,
    normalize_citations,
)


class AgentResultHelperTests(unittest.TestCase):
    def test_success_result_includes_required_fields_and_defaults(self):
        result = make_success_result(
            sub_query_id=1,
            query="What is machine learning?",
            agent_type="insights",
            result="Machine learning is a field of AI.",
            source="insights",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["agent_type"], "insights")
        self.assertEqual(result["confidence"], "unknown")
        self.assertEqual(result["output_chars"], len(result["result"]))
        self.assertEqual(result["tool_metadata"], {})

    def test_error_result_includes_error_message(self):
        result = make_error_result(
            sub_query_id=2,
            query="Search the web",
            agent_type="web_search",
            error_message="Search backend failed",
            source="web_search",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["result"], "Search backend failed")
        self.assertEqual(result["error_message"], "Search backend failed")

    def test_out_of_scope_result_includes_scope_metadata(self):
        result = make_out_of_scope_result(
            sub_query_id=3,
            query="Write a poem",
            result="This question is outside the scope of what I can help with.",
            source="query_analyzer",
            scope_reasoning="Creative writing is out of scope.",
        )

        self.assertEqual(result["status"], "out_of_scope")
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(
            result["tool_metadata"]["scope_reasoning"],
            "Creative writing is out of scope.",
        )

    def test_normalize_citations_drops_empty_urls(self):
        citations = normalize_citations(
            [
                {"title": "No URL", "body": "Skip this"},
                {
                    "title": "Example",
                    "href": "https://example.com",
                    "body": "Snippet",
                    "provider": "duckduckgo",
                },
            ],
            default_provider="web_search",
        )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["url"], "https://example.com")
        self.assertEqual(citations[0]["rank"], 2)
        self.assertEqual(citations[0]["provider"], "duckduckgo")


if __name__ == "__main__":
    unittest.main()
