from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv

from app.clients.gemini_client import GeminiClient
from prompts.prompt_loader import PromptLoader


load_dotenv()


class RuntimeContext:

    def __init__(
        self,
        gemini_client: GeminiClient,
        prompt_loader: PromptLoader,
        compiled_graph: Any = None,
    ):
        self.gemini_client = gemini_client
        self.prompt_loader = prompt_loader
        self.compiled_graph = compiled_graph


_runtime: Optional[RuntimeContext] = None


def build_runtime() -> RuntimeContext:
    api_key = os.getenv("GEMINI_API_KEY", "")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    client = GeminiClient(api_key=api_key, model_name=model_name)
    loader = PromptLoader()

    return RuntimeContext(gemini_client=client, prompt_loader=loader)


def get_runtime() -> RuntimeContext:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def set_runtime(rt: RuntimeContext) -> None:
    global _runtime
    _runtime = rt
