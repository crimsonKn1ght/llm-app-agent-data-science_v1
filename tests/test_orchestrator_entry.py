from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.orchestrator import orchestrator_entry


class _FakeCompiledGraph:
    def __init__(self):
        self.state = None

    async def ainvoke(self, state):
        self.state = state
        return {
            **state,
            "execution_path": ["test"],
            "final_response": "",
        }


class _FakePromptLoader:
    def get_system_prompt(self, name: str) -> str:
        return ""


class _FakeRuntime:
    def __init__(self):
        self.compiled_graph = _FakeCompiledGraph()
        self.prompt_loader = _FakePromptLoader()
        self.llm_client = object()


class OrchestratorEntryTests(unittest.TestCase):
    def test_web_search_flag_is_carried_into_initial_state(self):
        runtime = _FakeRuntime()
        conversation_id = str(uuid.uuid4())

        with patch.object(orchestrator_entry, "get_runtime", return_value=runtime), \
            patch.object(orchestrator_entry.conversation_store, "load", return_value=None), \
            patch.object(orchestrator_entry.conversation_store, "append_turn"), \
            patch.object(orchestrator_entry.conversation_store, "compress_history", new=AsyncMock()):
            asyncio.run(orchestrator_entry.orchestrate(
                user_query="Latest AI news",
                conversation_id=conversation_id,
                web_search=True,
                stream_queue=asyncio.Queue(),
            ))

        self.assertIsNotNone(runtime.compiled_graph.state)
        self.assertTrue(runtime.compiled_graph.state["web_search"])


if __name__ == "__main__":
    unittest.main()
