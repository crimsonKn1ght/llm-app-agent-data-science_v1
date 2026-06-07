from __future__ import annotations

from typing import Any, Dict

import yaml


class PromptLoader:
    def __init__(self, prompts_path: str = "prompts/prompts.yaml"):
        with open(prompts_path, "r") as f:
            self._prompts: Dict[str, Any] = yaml.safe_load(f)

    def get_system_prompt(self, name: str) -> str:
        return self._prompts[name]["system_prompt"].strip()

    def get_temperature(self, name: str) -> float:
        return self._prompts[name].get("temperature", 0.5)

    def get_max_tokens(self, name: str) -> int:
        return self._prompts[name].get("max_output_tokens", 4096)
