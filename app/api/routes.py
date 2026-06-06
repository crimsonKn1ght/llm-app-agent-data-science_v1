from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models.schemas import ChatRequest
from app.orchestrator.orchestrator_entry import orchestrate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/chat/generate")
async def generate(request: ChatRequest):
    queue: asyncio.Queue = asyncio.Queue()

    async def run_graph():
        try:
            await orchestrate(
                user_query=request.user_query,
                conversation_id=request.conversation_id,
                stream_queue=queue,
            )
        except Exception as e:
            logger.error("Orchestration failed: %s", e)
            try:
                queue.put_nowait({"type": "error", "message": str(e)})
                queue.put_nowait(None)
            except Exception:
                pass

    graph_task = asyncio.create_task(run_graph())

    async def event_stream() -> AsyncGenerator[bytes, None]:
        accumulated = ""
        final_response_override = None
        try:
            while True:
                item = await asyncio.wait_for(queue.get(), timeout=120.0)
                if item is None:
                    break
                if isinstance(item, dict):
                    event_type = item.get("type", "text")
                    if event_type == "progress":
                        line = json.dumps({
                            "type": "progress",
                            "message": item["message"],
                            "is_final": False,
                        })
                        yield (line + "\n").encode("utf-8")
                    elif event_type == "final_response":
                        final_response_override = item["content"]
                    elif event_type == "error":
                        line = json.dumps({
                            "type": "error",
                            "message": item["message"],
                            "is_final": False,
                        })
                        yield (line + "\n").encode("utf-8")
                    else:
                        text = item.get("text", "")
                        accumulated += text
                        line = json.dumps({
                            "type": "text",
                            "text": text,
                            "is_final": False,
                        })
                        yield (line + "\n").encode("utf-8")
                else:
                    accumulated += item
                    line = json.dumps({
                        "type": "text",
                        "text": item,
                        "is_final": False,
                    })
                    yield (line + "\n").encode("utf-8")
        except asyncio.TimeoutError:
            logger.warning("Stream timed out after 120s")
            accumulated += "\n[Stream timed out]"
        except Exception as e:
            logger.error("Stream error: %s", e)

        final_line = json.dumps({
            "type": "text",
            "text": "",
            "is_final": True,
            "full_response": final_response_override if final_response_override is not None else accumulated,
        })
        yield (final_line + "\n").encode("utf-8")

        await graph_task

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )
