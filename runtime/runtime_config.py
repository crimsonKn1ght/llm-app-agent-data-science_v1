from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class Runtime:
    llm_client: Any
    prompt_loader: Any
    compiled_graph: Any


_runtime: Runtime | None = None


class RuntimeConfigurationError(RuntimeError):
    pass


REQUIRED_ENV_VARS = ("ANTHROPIC_API_KEY",)


def missing_required_env_vars() -> list[str]:
    return [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]


def runtime_status() -> dict[str, Any]:
    missing: list[str] = []

    if _runtime is None:
        missing.extend(missing_required_env_vars())
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

    missing_env = missing_required_env_vars()
    if missing_env:
        missing = ", ".join(missing_env)
        raise RuntimeConfigurationError(
            f"Missing required runtime configuration: {missing}"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    from runtime.llm_client import LLMClient
    from runtime.prompt_loader import PromptLoader

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
