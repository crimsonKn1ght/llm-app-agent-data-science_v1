from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from runtime.llm_client import LLMClient
from runtime.prompt_loader import PromptLoader


@dataclass
class Runtime:
    llm_client: LLMClient
    prompt_loader: PromptLoader
    compiled_graph: Any


_runtime: Runtime | None = None


def runtime_status() -> dict[str, Any]:
    missing: list[str] = []

    if _runtime is None:
        missing.append("runtime")
    else:
        if _runtime.llm_client is None:
            missing.append("llm_client")
        if _runtime.prompt_loader is None:
            missing.append("prompt_loader")
        if _runtime.compiled_graph is None:
            missing.append("compiled_graph")

    return {
        "ready": not missing,
        "missing": missing,
    }


def init_runtime() -> Runtime:
    global _runtime

    api_key = os.environ["ANTHROPIC_API_KEY"]
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    llm_client = LLMClient(api_key=api_key, model=model)
    prompt_loader = PromptLoader()

    from app.orchestrator.orchestrator_workflow import build_orchestrator_graph
    compiled_graph = build_orchestrator_graph()

    _runtime = Runtime(
        llm_client=llm_client,
        prompt_loader=prompt_loader,
        compiled_graph=compiled_graph,
    )
    return _runtime


def get_runtime() -> Runtime:
    if _runtime is None:
        return init_runtime()
    return _runtime
