from __future__ import annotations

import asyncio


async def emit_progress(queue: asyncio.Queue, message: str) -> None:
    queue.put_nowait({"type": "progress", "message": message})
    await asyncio.sleep(0)


async def emit_text(queue: asyncio.Queue, text: str) -> None:
    queue.put_nowait({"type": "text", "text": text})
    await asyncio.sleep(0)


async def emit_final_response(queue: asyncio.Queue, content: str) -> None:
    """Internal event — restructures full_response in the final sentinel (not visible mid-stream)."""
    queue.put_nowait({"type": "final_response", "content": content})
    await asyncio.sleep(0)
