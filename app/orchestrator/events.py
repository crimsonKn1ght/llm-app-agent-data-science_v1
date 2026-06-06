from __future__ import annotations

import asyncio


async def emit_progress(queue: asyncio.Queue, message: str, origin: str = "system") -> None:
    queue.put_nowait({"type": "progress", "message": message, "origin": origin})
    await asyncio.sleep(0)


async def emit_text(queue: asyncio.Queue, text: str, origin: str = "") -> None:
    item = {"type": "text", "text": text}
    if origin:
        item["origin"] = origin
    queue.put_nowait(item)
    await asyncio.sleep(0)


async def emit_final_response(queue: asyncio.Queue, content: str) -> None:
    queue.put_nowait({"type": "final_response", "content": content})
    await asyncio.sleep(0)
