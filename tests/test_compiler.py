from __future__ import annotations

import unittest

from app.orchestrator.compiler import (
    ERROR_ONLY_RESPONSE,
    assemble_with_summary,
    plan_compilation,
)
from app.orchestrator.results import (
    make_error_result,
    make_out_of_scope_result,
    make_success_result,
)


class CompilerHelperTests(unittest.TestCase):
    def test_all_out_of_scope_returns_existing_message(self):
        message = "This question is outside the scope of what I can help with."
        plan = plan_compilation(
            user_query="Write a poem",
            source_contents={},
            agent_results=[
                make_out_of_scope_result(
                    sub_query_id=1,
                    query="Write a poem",
                    result=message,
                    source="query_analyzer",
                )
            ],
        )

        self.assertEqual(plan.strategy, "out_of_scope")
        self.assertEqual(plan.final_response, message)
        self.assertEqual(plan.metadata["skipped_agents"], ["query_analyzer"])

    def test_single_short_success_skips_summary(self):
        plan = plan_compilation(
            user_query="Explain ML",
            source_contents={"insights": "Machine learning finds patterns."},
            agent_results=[
                make_success_result(
                    sub_query_id=1,
                    query="Explain ML",
                    agent_type="insights",
                    result="Machine learning finds patterns.",
                    source="insights",
                )
            ],
        )

        self.assertEqual(plan.strategy, "passthrough")
        self.assertEqual(plan.summary_input, "")
        self.assertEqual(
            plan.final_response,
            "## Insights\n\nMachine learning finds patterns.",
        )

    def test_multiple_success_sections_request_summary(self):
        plan = plan_compilation(
            user_query="Compare ML and current AI news",
            source_contents={
                "web_search": "Recent AI news.",
                "insights": "Machine learning background.",
            },
            agent_results=[
                make_success_result(
                    sub_query_id=1,
                    query="Explain ML",
                    agent_type="insights",
                    result="Machine learning background.",
                    source="insights",
                ),
                make_success_result(
                    sub_query_id=2,
                    query="Current AI news",
                    agent_type="web_search",
                    result="Recent AI news.",
                    source="web_search",
                ),
            ],
        )

        self.assertEqual(plan.strategy, "summarize")
        self.assertIn("[## Insights]", plan.summary_input)
        self.assertIn("[## Web Search]", plan.summary_input)

        final_response = assemble_with_summary(
            summary_text="- Combined summary.",
            sections=plan.sections,
        )
        self.assertTrue(final_response.startswith("## Summary\n\n- Combined summary."))
        self.assertIn("## Insights", final_response)
        self.assertIn("## Web Search", final_response)

    def test_mixed_success_and_error_renders_notice(self):
        plan = plan_compilation(
            user_query="Explain and search",
            source_contents={"insights": "Useful answer."},
            agent_results=[
                make_success_result(
                    sub_query_id=1,
                    query="Explain",
                    agent_type="insights",
                    result="Useful answer.",
                    source="insights",
                ),
                make_error_result(
                    sub_query_id=2,
                    query="Search",
                    agent_type="web_search",
                    error_message="Search failed",
                    source="web_search",
                ),
            ],
        )

        self.assertEqual(plan.strategy, "passthrough")
        self.assertIn("## Notices", plan.final_response)
        self.assertIn("web_search", plan.final_response)
        self.assertEqual(plan.metadata["failed_agents"], ["web_search"])

    def test_error_only_uses_concise_fallback(self):
        plan = plan_compilation(
            user_query="Search",
            source_contents={},
            agent_results=[
                make_error_result(
                    sub_query_id=1,
                    query="Search",
                    agent_type="web_search",
                    error_message="Backend failed with private details",
                    source="web_search",
                )
            ],
        )

        self.assertEqual(plan.strategy, "error_only")
        self.assertEqual(plan.final_response, ERROR_ONLY_RESPONSE)
        self.assertNotIn("private details", plan.final_response)

    def test_citation_metadata_is_counted_not_rendered(self):
        plan = plan_compilation(
            user_query="Current news",
            source_contents={"web_search": "A web answer."},
            agent_results=[
                make_success_result(
                    sub_query_id=1,
                    query="Current news",
                    agent_type="web_search",
                    result="A web answer.",
                    source="web_search",
                    citations=[
                        {
                            "title": "Example",
                            "url": "https://example.com",
                            "snippet": "Snippet",
                            "rank": 1,
                            "provider": "duckduckgo",
                        }
                    ],
                )
            ],
        )

        self.assertEqual(plan.metadata["citation_count"], 1)
        self.assertNotIn("https://example.com", plan.final_response)


if __name__ == "__main__":
    unittest.main()
