from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv

from app.clients.claude_client import ClaudeClient
from prompts.prompt_loader import PromptLoader


load_dotenv()


class RuntimeContext:

    def __init__(
        self,
        llm_client: ClaudeClient,
        prompt_loader: PromptLoader,
        compiled_graph: Any = None,
    ):
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.compiled_graph = compiled_graph


_runtime: Optional[RuntimeContext] = None


def build_runtime() -> RuntimeContext:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model_name = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    client = ClaudeClient(api_key=api_key, model_name=model_name)
    loader = PromptLoader()

    return RuntimeContext(llm_client=client, prompt_loader=loader)


def get_runtime() -> RuntimeContext:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def set_runtime(rt: RuntimeContext) -> None:
    global _runtime
    _runtime = rt
