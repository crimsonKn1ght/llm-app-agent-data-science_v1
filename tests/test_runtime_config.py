from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from runtime import runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.old_runtime = runtime_config._runtime
        runtime_config._runtime = None

    def tearDown(self):
        runtime_config._runtime = self.old_runtime

    @patch.dict(os.environ, {}, clear=True)
    def test_runtime_status_reports_missing_api_key_without_initializing(self):
        status = runtime_config.runtime_status()

        self.assertFalse(status["ready"])
        self.assertIn("ANTHROPIC_API_KEY", status["missing"])
        self.assertIn("runtime", status["missing"])

    @patch.dict(os.environ, {}, clear=True)
    def test_init_runtime_raises_clear_configuration_error(self):
        with self.assertRaises(runtime_config.RuntimeConfigurationError) as ctx:
            runtime_config.init_runtime()

        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True)
    def test_runtime_status_reports_runtime_missing_when_config_present(self):
        status = runtime_config.runtime_status()

        self.assertFalse(status["ready"])
        self.assertNotIn("ANTHROPIC_API_KEY", status["missing"])
        self.assertIn("runtime", status["missing"])

    def test_close_runtime_closes_llm_client_and_clears_runtime(self):
        class _FakeClient:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        fake_client = _FakeClient()
        runtime_config._runtime = runtime_config.Runtime(
            llm_client=fake_client,
            prompt_loader=object(),
            compiled_graph=object(),
        )

        import asyncio

        asyncio.run(runtime_config.close_runtime())

        self.assertTrue(fake_client.closed)
        self.assertIsNone(runtime_config._runtime)


if __name__ == "__main__":
    unittest.main()
