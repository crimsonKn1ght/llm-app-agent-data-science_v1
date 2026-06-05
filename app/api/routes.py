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
                await queue.put(json.dumps({"error": str(e)}))
                await queue.put(None)
            except Exception:
                pass

    graph_task = asyncio.create_task(run_graph())

    async def event_stream() -> AsyncGenerator[bytes, None]:
        accumulated = ""
        try:
            while True:
                chunk = await asyncio.wait_for(queue.get(), timeout=120.0)
                if chunk is None:
                    break
                accumulated += chunk
                line = json.dumps({
                    "text": chunk,
                    "is_final": False,
                    "full_response": None,
                    "error": None,
                })
                yield (line + "\n").encode("utf-8")
        except asyncio.TimeoutError:
            logger.warning("Stream timed out after 120s")
            accumulated += "\n[Stream timed out]"
        except Exception as e:
            logger.error("Stream error: %s", e)

        final_line = json.dumps({
            "text": "",
            "is_final": True,
            "full_response": accumulated,
            "error": None,
        })
        yield (final_line + "\n").encode("utf-8")

        await graph_task

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
