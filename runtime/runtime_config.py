from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from app.clients.gemini_client import GeminiClient


load_dotenv()


class RuntimeContext:

    def __init__(
        self,
        gemini_client: GeminiClient,
        prompts: Dict[str, str],
        compiled_graph: Any = None,
    ):
        self.gemini_client = gemini_client
        self.prompts = prompts
        self.compiled_graph = compiled_graph


_runtime: Optional[RuntimeContext] = None


def _load_prompts(prompts_dir: str = "prompts") -> Dict[str, str]:
    prompts: Dict[str, str] = {}
    root = Path(prompts_dir)
    if not root.exists():
        return prompts
    for path in root.glob("*.txt"):
        prompts[path.stem] = path.read_text(encoding="utf-8")
    return prompts


def build_runtime() -> RuntimeContext:
    api_key = os.getenv("GEMINI_API_KEY", "")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    client = GeminiClient(api_key=api_key, model_name=model_name)
    prompts = _load_prompts()

    return RuntimeContext(gemini_client=client, prompts=prompts)


def get_runtime() -> RuntimeContext:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def set_runtime(rt: RuntimeContext) -> None:
    global _runtime
    _runtime = rt
