from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = Path(os.getenv("CONVERSATIONS_DIR", "conversations"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "20"))
KEEP_RECENT_TURNS = int(os.getenv("KEEP_RECENT_TURNS", "10"))
_locks_guard = threading.Lock()
_conversation_locks: Dict[str, threading.RLock] = {}


def normalize_conversation_id(conversation_id: str) -> str:
    return str(uuid.UUID(str(conversation_id)))


def _conversation_lock(conversation_id: str) -> threading.RLock:
    with _locks_guard:
        lock = _conversation_locks.get(conversation_id)
        if lock is None:
            lock = threading.RLock()
            _conversation_locks[conversation_id] = lock
        return lock


def _conversations_root() -> Path:
    root = CONVERSATIONS_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _conv_path(conversation_id: str) -> Path:
    normalized_id = normalize_conversation_id(conversation_id)
    root = _conversations_root()
    path = (root / f"{normalized_id}.json").resolve()
    if path.parent != root:
        raise ValueError("Conversation path resolved outside conversations directory")
    return path


def _empty_data() -> Dict[str, Any]:
    return {"history": [], "summary": ""}


def _iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _title_from_history(history: List[Dict[str, str]], max_chars: int = 80) -> str:
    for item in history:
        if item.get("role") == "user":
            title = " ".join(item.get("content", "").split())
            if len(title) > max_chars:
                return f"{title[: max_chars - 1].rstrip()}..."
            return title or "Untitled conversation"
    return "Untitled conversation"


def _snippet(text: str, term: str, radius: int = 70) -> str:
    normalized_text = " ".join(text.split())
    if not normalized_text:
        return ""

    index = normalized_text.lower().find(term.lower())
    if index < 0:
        return normalized_text[: radius * 2].rstrip()

    start = max(0, index - radius)
    end = min(len(normalized_text), index + len(term) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized_text) else ""
    return f"{prefix}{normalized_text[start:end].strip()}{suffix}"


def _normalize_data(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return _empty_data()

    history = data.get("history", [])
    if not isinstance(history, list):
        history = []

    normalized_history: List[Dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant", "system"} or not isinstance(content, str):
            continue
        normalized_history.append({"role": role, "content": content})

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        summary = ""

    return {
        "history": normalized_history,
        "summary": summary,
    }


def _recover_corrupted_file(path: Path) -> None:
    if not path.exists():
        return

    backup = path.with_name(f"{path.stem}.corrupt-{int(time.time() * 1000)}{path.suffix}")
    try:
        os.replace(path, backup)
        logger.warning("Corrupted conversation file moved aside | path=%s | backup=%s", path, backup)
    except OSError as e:
        logger.error("Failed to recover corrupted conversation file | path=%s | error=%s", path, e)


def _read_data(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _empty_data()

    try:
        with open(path, "r", encoding="utf-8") as f:
            return _normalize_data(json.load(f))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Conversation file could not be read | path=%s | error=%s", path, e)
        _recover_corrupted_file(path)
        return _empty_data()


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_normalize_data(data), f, indent=2)
    os.replace(tmp_path, path)


def load(conversation_id: str) -> Optional[Dict[str, Any]]:
    conversation_id = normalize_conversation_id(conversation_id)
    path = _conv_path(conversation_id)
    with _conversation_lock(conversation_id):
        if not path.exists():
            return None
        return _read_data(path)


def list_conversations(limit: int = 50) -> List[Dict[str, Any]]:
    root = _conversations_root()
    items: List[Dict[str, Any]] = []

    for path in root.glob("*.json"):
        try:
            conversation_id = normalize_conversation_id(path.stem)
        except ValueError:
            continue

        with _conversation_lock(conversation_id):
            if not path.exists():
                continue
            data = _read_data(path)
            if not path.exists():
                continue

        history = data["history"]
        items.append(
            {
                "conversation_id": conversation_id,
                "title": _title_from_history(history),
                "updated_at": _iso_from_mtime(path),
                "message_count": len(history),
            }
        )

    items.sort(key=lambda item: item["updated_at"], reverse=True)
    return items[:limit]


def conversation_status(conversation_id: str) -> Dict[str, Any]:
    conversation_id = normalize_conversation_id(conversation_id)
    path = _conv_path(conversation_id)
    with _conversation_lock(conversation_id):
        if not path.exists():
            return {
                "conversation_id": conversation_id,
                "exists": False,
                "message_count": 0,
                "updated_at": None,
            }
        data = _read_data(path)
        if not path.exists():
            return {
                "conversation_id": conversation_id,
                "exists": False,
                "message_count": 0,
                "updated_at": None,
            }
        return {
            "conversation_id": conversation_id,
            "exists": True,
            "message_count": len(data["history"]),
            "updated_at": _iso_from_mtime(path),
        }


def search_conversations(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    term = " ".join(query.split())
    if not term:
        return []

    root = _conversations_root()
    items: List[Dict[str, Any]] = []

    for path in root.glob("*.json"):
        try:
            conversation_id = normalize_conversation_id(path.stem)
        except ValueError:
            continue

        with _conversation_lock(conversation_id):
            if not path.exists():
                continue
            data = _read_data(path)
            if not path.exists():
                continue

        matches = []
        for item in data["history"]:
            content = item.get("content", "")
            if term.lower() not in content.lower():
                continue
            matches.append(
                {
                    "role": item.get("role", "assistant"),
                    "snippet": _snippet(content, term),
                }
            )

        if matches:
            items.append(
                {
                    "conversation_id": conversation_id,
                    "title": _title_from_history(data["history"]),
                    "updated_at": _iso_from_mtime(path),
                    "matches": matches[:3],
                }
            )

    items.sort(key=lambda item: item["updated_at"], reverse=True)
    return items[:limit]


def build_context(conv_data: Dict[str, Any]) -> Tuple[List[Dict[str, str]], str]:
    normalized = _normalize_data(conv_data)
    history = normalized["history"]
    summary = normalized["summary"]
    return history, summary


def append_turn(conversation_id: str, user_query: str, response: str) -> None:
    conversation_id = normalize_conversation_id(conversation_id)
    path = _conv_path(conversation_id)
    with _conversation_lock(conversation_id):
        data = _read_data(path)
        data["history"].append({"role": "user", "content": user_query})
        data["history"].append({"role": "assistant", "content": response})
        _atomic_write(path, data)


async def compress_history(
    conversation_id: str,
    llm_client: Any,
    summary_prompt: str,
) -> None:
    conversation_id = normalize_conversation_id(conversation_id)
    path = _conv_path(conversation_id)

    with _conversation_lock(conversation_id):
        if not path.exists():
            return
        data = _read_data(path)

    history = data["history"]
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
        with _conversation_lock(conversation_id):
            latest = _read_data(path)
            if len(latest["history"]) <= MAX_HISTORY_TURNS:
                return
            latest["summary"] = new_summary
            latest["history"] = latest["history"][-KEEP_RECENT_TURNS:]
            _atomic_write(path, latest)
        logger.info("Conversation history compressed | conv_id=%s", conversation_id)
    except Exception as e:
        logger.error("Failed to compress history | error=%s", e)
