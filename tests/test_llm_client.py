from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HAS_ANTHROPIC = importlib.util.find_spec("anthropic") is not None

if HAS_ANTHROPIC:
    from runtime.llm_client import LLMClient


class _FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(content=[SimpleNamespace(text=outcome)])


class _FakeAnthropicClient:
    def __init__(self, messages):
        self.messages = messages


@unittest.skipUnless(HAS_ANTHROPIC, "anthropic is not installed in this Python environment")
class LLMClientRetryTests(unittest.TestCase):
    def test_generate_retries_and_succeeds(self):
        client = LLMClient(api_key="key", model="model", timeout_seconds=1, max_retries=1)
        messages = _FakeMessages([RuntimeError("temporary"), "ok"])
        client._client = _FakeAnthropicClient(messages)

        with patch.object(client, "_retry_delay", new=AsyncMock()):
            result = asyncio.run(client.generate("input", "system"))

        self.assertEqual(result, "ok")
        self.assertEqual(messages.calls, 2)

    def test_generate_raises_after_retry_exhaustion(self):
        client = LLMClient(api_key="key", model="model", timeout_seconds=1, max_retries=1)
        messages = _FakeMessages([RuntimeError("one"), RuntimeError("two")])
        client._client = _FakeAnthropicClient(messages)

        with patch.object(client, "_retry_delay", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                asyncio.run(client.generate("input", "system"))

        self.assertEqual(messages.calls, 2)

    def test_generate_timeout_is_retried(self):
        async def slow_create(**kwargs):
            await asyncio.sleep(0.05)
            return SimpleNamespace(content=[SimpleNamespace(text="late")])

        client = LLMClient(api_key="key", model="model", timeout_seconds=0.001, max_retries=1)
        client._client = _FakeAnthropicClient(SimpleNamespace(create=slow_create))

        with patch.object(client, "_retry_delay", new=AsyncMock()):
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(client.generate("input", "system"))


if __name__ == "__main__":
    unittest.main()
