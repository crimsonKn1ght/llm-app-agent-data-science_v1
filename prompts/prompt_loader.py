from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any, Dict, List


_PROMPT_FILE = Path(__file__).parent / "prompts.yaml"


class PromptLoader:
    """Loads prompts from prompts.yaml and provides typed accessors.

    Each entry in the YAML has the shape:
        <prompt_name>:
          system_prompt: |
            ...
          temperature: 0.7
          max_output_tokens: 4096
    """

    def __init__(self, yaml_path: Path | str = _PROMPT_FILE):
        with open(yaml_path, "r", encoding="utf-8") as f:
            self._data: Dict[str, Any] = yaml.safe_load(f) or {}

    # ─── Selectors ───────────────────────────────────────────────────────────

    def get_system_prompt(self, name: str) -> str:
        """Return the system prompt string for the named prompt."""
        return self._data.get(name, {}).get("system_prompt", "")

    def get_temperature(self, name: str, default: float = 0.7) -> float:
        """Return the temperature configured for the named prompt."""
        return float(self._data.get(name, {}).get("temperature", default))

    def get_max_tokens(self, name: str, default: int = 4096) -> int:
        """Return the max_output_tokens configured for the named prompt."""
        return int(self._data.get(name, {}).get("max_output_tokens", default))

    def get_config(self, name: str) -> Dict[str, Any]:
        """Return the full config dict for the named prompt."""
        return self._data.get(name, {})

    def list_prompts(self) -> List[str]:
        """Return all prompt names registered in the YAML."""
        return list(self._data.keys())
