from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CONVERSATIONS_DIR = os.getenv("CONVERSATIONS_DIR", "conversations")
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "20"))
KEEP_RECENT_TURNS = int(os.getenv("KEEP_RECENT_TURNS", "10"))


def _conversations_path() -> Path:
    p = Path(CONVERSATIONS_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file_for(conversation_id: str) -> Path:
    safe_id = conversation_id.replace("/", "_").replace("\\", "_")
    return _conversations_path() / f"{safe_id}.json"


def load(conversation_id: str) -> Optional[Dict[str, Any]]:
    path = _file_for(conversation_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(conversation_id: str, data: Dict[str, Any]) -> None:
    path = _file_for(conversation_id)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def new_conversation(conversation_id: Optional[str] = None) -> Dict[str, Any]:
    cid = conversation_id or str(uuid.uuid4())
    return {
        "conversation_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "summary": "",
        "turns": [],
    }


def build_context(conversation: Dict[str, Any]) -> Tuple[List[Dict[str, str]], str]:
    summary = conversation.get("summary", "")
    turns = conversation.get("turns", [])
    recent = turns[-KEEP_RECENT_TURNS:]

    history: List[Dict[str, str]] = []
    for turn in recent:
        history.append({"role": "user", "content": turn["user_query"]})
        history.append({"role": "assistant", "content": turn["response"]})

    return history, summary


def append_turn(
    conversation_id: str,
    user_query: str,
    response: str,
) -> Dict[str, Any]:
    conv = load(conversation_id)
    if conv is None:
        conv = new_conversation(conversation_id)

    turn_number = len(conv["turns"]) + 1
    conv["turns"].append({
        "turn_number": turn_number,
        "user_query": user_query,
        "response": response,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    conv["updated_at"] = datetime.now(timezone.utc).isoformat()
    save(conversation_id, conv)
    return conv


async def compress_history(conversation_id: str, gemini_client: Any, summary_prompt: str) -> None:
    conv = load(conversation_id)
    if conv is None:
        return

    turns = conv.get("turns", [])
    if len(turns) <= MAX_HISTORY_TURNS:
        return

    older = turns[:-KEEP_RECENT_TURNS]
    recent = turns[-KEEP_RECENT_TURNS:]

    older_text = ""
    for t in older:
        older_text += f"USER: {t['user_query']}\nASSISTANT: {t['response']}\n\n"

    try:
        user_input = f"Conversation to summarize:\n\n{older_text}"
        new_summary = await gemini_client.generate(
            user_input=user_input,
            system_prompt=summary_prompt,
            temperature=0.3,
            max_output_tokens=2048,
        )
    except Exception:
        previews = []
        for t in older[:3]:
            preview = t["response"][:80]
            previews.append(preview)
        new_summary = "Previous context: " + "; ".join(previews)

    existing = conv.get("summary", "")
    if existing:
        conv["summary"] = existing + "\n\n" + new_summary
    else:
        conv["summary"] = new_summary

    conv["turns"] = recent
    conv["updated_at"] = datetime.now(timezone.utc).isoformat()
    save(conversation_id, conv)
