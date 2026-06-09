from __future__ import annotations

import unittest
import importlib.util

HAS_API_DEPS = (
    importlib.util.find_spec("fastapi") is not None
    and importlib.util.find_spec("pydantic") is not None
)

if HAS_API_DEPS:
    import asyncio
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import ChatRequest
    from app.api.routes import router as chat_router


@unittest.skipUnless(HAS_API_DEPS, "fastapi/pydantic are not installed in this Python environment")
class ChatRequestContractTests(unittest.TestCase):
    def test_user_query_is_trimmed(self):
        request = ChatRequest(user_query="  Explain AI  ")

        self.assertEqual(request.user_query, "Explain AI")

    def test_whitespace_query_is_rejected(self):
        with self.assertRaises(Exception):
            ChatRequest(user_query="   ")

    def test_query_over_max_length_is_rejected(self):
        with self.assertRaises(Exception):
            ChatRequest(user_query="x" * 8001)

    def test_web_search_defaults_to_false(self):
        request = ChatRequest(user_query="Explain AI")

        self.assertFalse(request.web_search)

    def test_web_search_accepts_true(self):
        request = ChatRequest(user_query="Latest AI news", web_search=True)

        self.assertTrue(request.web_search)

    def test_chat_endpoint_streams_ndjson_final_response(self):
        app = FastAPI()
        app.include_router(chat_router, prefix="/api")

        async def fake_orchestrate(*, stream_queue: asyncio.Queue, **kwargs):
            stream_queue.put_nowait({"type": "progress", "message": "Testing", "origin": "test"})
            stream_queue.put_nowait({"type": "final_response", "content": "Final answer"})
            stream_queue.put_nowait(None)
            return "Final answer"

        with patch("app.api.routes.orchestrate", new=fake_orchestrate):
            response = TestClient(app).post(
                "/api/chat/generate",
                json={"user_query": "Explain AI", "web_search": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/x-ndjson")
        self.assertIn('"type": "progress"', response.text)
        self.assertIn('"type": "final_response"', response.text)
        self.assertIn("Final answer", response.text)


if __name__ == "__main__":
    unittest.main()
