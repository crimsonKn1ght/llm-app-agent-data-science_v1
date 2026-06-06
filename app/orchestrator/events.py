from __future__ import annotations

import asyncio


async def emit_progress(queue: asyncio.Queue, message: str) -> None:
    await queue.put({"type": "progress", "message": message})


async def emit_text(queue: asyncio.Queue, text: str) -> None:
    await queue.put({"type": "text", "text": text})
