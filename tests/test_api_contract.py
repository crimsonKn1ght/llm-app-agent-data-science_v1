from __future__ import annotations

import unittest
import importlib.util

HAS_API_DEPS = (
    importlib.util.find_spec("fastapi") is not None
    and importlib.util.find_spec("pydantic") is not None
)

if HAS_API_DEPS:
    from app.api.routes import ChatRequest


@unittest.skipUnless(HAS_API_DEPS, "fastapi/pydantic are not installed in this Python environment")
class ChatRequestContractTests(unittest.TestCase):
    def test_web_search_defaults_to_false(self):
        request = ChatRequest(user_query="Explain AI")

        self.assertFalse(request.web_search)

    def test_web_search_accepts_true(self):
        request = ChatRequest(user_query="Latest AI news", web_search=True)

        self.assertTrue(request.web_search)


if __name__ == "__main__":
    unittest.main()
