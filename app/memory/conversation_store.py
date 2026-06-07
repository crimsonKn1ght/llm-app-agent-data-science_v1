from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = Path(os.getenv("CONVERSATIONS_DIR", "conversations"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "20"))
KEEP_RECENT_TURNS = int(os.getenv("KEEP_RECENT_TURNS", "10"))


def _conv_path(conversation_id: str) -> Path:
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return CONVERSATIONS_DIR / f"{conversation_id}.json"


def load(conversation_id: str) -> Optional[Dict[str, Any]]:
    path = _conv_path(conversation_id)
    if not path.exists():
        return None
    with open(path, "r") as f:
        return json.load(f)


def build_context(conv_data: Dict[str, Any]) -> Tuple[List[Dict[str, str]], str]:
    history = conv_data.get("history", [])
    summary = conv_data.get("summary", "")
    return history, summary


def append_turn(conversation_id: str, user_query: str, response: str) -> None:
    path = _conv_path(conversation_id)
    if path.exists():
        with open(path, "r") as f:
            data = json.load(f)
    else:
        data = {"history": [], "summary": ""}

    data["history"].append({"role": "user", "content": user_query})
    data["history"].append({"role": "assistant", "content": response})

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


async def compress_history(
    conversation_id: str,
    llm_client: Any,
    summary_prompt: str,
) -> None:
    path = _conv_path(conversation_id)
    if not path.exists():
        return

    with open(path, "r") as f:
        data = json.load(f)

    history = data.get("history", [])
    if len(history) <= MAX_HISTORY_TURNS:
        return

    old_turns = history[:-KEEP_RECENT_TURNS]
    recent_turns = history[-KEEP_RECENT_TURNS:]

    turns_text = "\n".join(
        f"{t['role'].upper()}: {t['content']}" for t in old_turns
    )
    existing_summary = data.get("summary", "")
    user_input = f"Existing summary:\n{existing_summary}\n\nNew turns to compress:\n{turns_text}"

    try:
        new_summary = await llm_client.generate(
            user_input=user_input,
            system_prompt=summary_prompt,
            temperature=0.3,
            max_output_tokens=2048,
        )
        data["summary"] = new_summary
        data["history"] = recent_turns
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Conversation history compressed | conv_id=%s", conversation_id)
    except Exception as e:
        logger.error("Failed to compress history | error=%s", e)
