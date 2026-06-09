from __future__ import annotations

import unittest

from app.orchestrator.compiler import (
    ERROR_ONLY_RESPONSE,
    MAX_RENDERED_CITATIONS,
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

    def test_web_citations_render_as_sources(self):
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
        self.assertEqual(plan.metadata["rendered_citation_count"], 1)
        self.assertIn("## Sources", plan.final_response)
        self.assertIn("1. [Example](https://example.com)", plan.final_response)

    def test_duplicate_citation_urls_render_once(self):
        citations = [
            {
                "title": "Example A",
                "url": "https://example.com/path/",
                "snippet": "First",
                "rank": 1,
                "provider": "duckduckgo",
            },
            {
                "title": "Example B",
                "url": "https://EXAMPLE.com/path",
                "snippet": "Duplicate",
                "rank": 2,
                "provider": "brave",
            },
        ]
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
                    citations=citations,
                )
            ],
        )

        self.assertEqual(plan.metadata["citation_count"], 2)
        self.assertEqual(plan.metadata["rendered_citation_count"], 1)
        self.assertEqual(plan.final_response.count("https://example.com/path/"), 1)

    def test_citations_do_not_render_without_web_section(self):
        plan = plan_compilation(
            user_query="Explain",
            source_contents={"insights": "An answer."},
            agent_results=[
                make_success_result(
                    sub_query_id=1,
                    query="Explain",
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
        self.assertEqual(plan.metadata["rendered_citation_count"], 0)
        self.assertNotIn("## Sources", plan.final_response)

    def test_citation_rendering_is_capped(self):
        citations = [
            {
                "title": f"Example {i}",
                "url": f"https://example.com/{i}",
                "snippet": "",
                "rank": i,
                "provider": "duckduckgo",
            }
            for i in range(1, MAX_RENDERED_CITATIONS + 3)
        ]
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
                    citations=citations,
                )
            ],
        )

        self.assertEqual(plan.metadata["rendered_citation_count"], MAX_RENDERED_CITATIONS)
        self.assertIn(f"{MAX_RENDERED_CITATIONS}. [Example {MAX_RENDERED_CITATIONS}]", plan.final_response)
        self.assertNotIn(f"https://example.com/{MAX_RENDERED_CITATIONS + 1}", plan.final_response)

    def test_final_response_order_places_sources_last(self):
        plan = plan_compilation(
            user_query="Explain and search",
            source_contents={"web_search": "Web answer.", "insights": "Useful answer."},
            agent_results=[
                make_success_result(
                    sub_query_id=1,
                    query="Search",
                    agent_type="web_search",
                    result="Web answer.",
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
                ),
                make_error_result(
                    sub_query_id=2,
                    query="Analyze",
                    agent_type="analytical",
                    error_message="Failed",
                    source="analytical",
                ),
            ],
        )
        final_response = assemble_with_summary(
            summary_text="- Summary.",
            sections=plan.sections,
            error_notice=plan.error_notice,
            citations=plan.citations,
        )

        self.assertLess(final_response.index("## Summary"), final_response.index("## Insights"))
        self.assertLess(final_response.index("## Web Search"), final_response.index("## Notices"))
        self.assertLess(final_response.index("## Notices"), final_response.index("## Sources"))


if __name__ == "__main__":
    unittest.main()
