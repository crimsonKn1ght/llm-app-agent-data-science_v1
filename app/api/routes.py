from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.memory import conversation_store
from app.orchestrator.orchestrator_entry import orchestrate

logger = logging.getLogger(__name__)

router = APIRouter()
MAX_USER_QUERY_CHARS = 8000


class ChatRequest(BaseModel):
    user_query: str = Field(min_length=1, max_length=MAX_USER_QUERY_CHARS)
    conversation_id: Optional[str] = None
    web_search: bool = False

    @field_validator("user_query", mode="before")
    @classmethod
    def _trim_and_validate_query(cls, value):
        if value is None:
            raise ValueError("user_query is required")
        query = str(value).strip()
        if not query:
            raise ValueError("user_query must not be empty")
        return query


class ChatHistoryItem(BaseModel):
    conversation_id: str
    title: str
    updated_at: str
    message_count: int


class ChatHistoryResponse(BaseModel):
    items: list[ChatHistoryItem]


class ChatMessageRecord(BaseModel):
    role: str
    content: str


class ChatConversationResponse(BaseModel):
    conversation_id: str
    history: list[ChatMessageRecord]
    summary: str


class ChatStatusResponse(BaseModel):
    conversation_id: str
    exists: bool
    message_count: int
    updated_at: Optional[str] = None


class ChatSearchMatch(BaseModel):
    role: str
    snippet: str


class ChatSearchItem(BaseModel):
    conversation_id: str
    title: str
    updated_at: str
    matches: list[ChatSearchMatch]


class ChatSearchResponse(BaseModel):
    items: list[ChatSearchItem]


@router.post("/chat/generate")
async def chat_endpoint(request: ChatRequest):
    stream_queue: asyncio.Queue = asyncio.Queue()

    async def run_pipeline():
        await orchestrate(
            user_query=request.user_query,
            conversation_id=request.conversation_id,
            web_search=request.web_search,
            stream_queue=stream_queue,
        )

    asyncio.create_task(run_pipeline())

    async def event_generator():
        final_response_override = None

        while True:
            item = await stream_queue.get()

            if item is None:
                if final_response_override:
                    payload = {
                        "type": "final_response",
                        "content": final_response_override,
                    }
                    yield json.dumps(payload) + "\n"
                break

            event_type = item.get("type", "")

            if event_type == "final_response":
                final_response_override = item.get("content", "")
                continue

            if event_type == "progress":
                payload = {
                    "type": "progress",
                    "message": item.get("message", ""),
                    "origin": item.get("origin", "system"),
                }
                yield json.dumps(payload) + "\n"

            elif event_type == "text":
                payload = {"type": "text", "text": item.get("text", "")}
                origin = item.get("origin")
                if origin:
                    payload["origin"] = origin
                yield json.dumps(payload) + "\n"

            elif event_type == "error":
                payload = {"type": "error", "message": item.get("message", "")}
                yield json.dumps(payload) + "\n"

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/chat/history", response_model=ChatHistoryResponse)
async def chat_history(limit: int = Query(default=50, ge=1, le=100)):
    return {"items": conversation_store.list_conversations(limit=limit)}


@router.get("/chat/search", response_model=ChatSearchResponse)
async def chat_search(
    q: str = Query(default=""),
    limit: int = Query(default=25, ge=1, le=100),
):
    return {"items": conversation_store.search_conversations(q, limit=limit)}


@router.get("/chat/status", response_model=ChatStatusResponse)
async def chat_status(conversation_id: str = Query(...)):
    try:
        return conversation_store.conversation_status(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid conversation_id") from exc


@router.get("/chat/{conversation_id}", response_model=ChatConversationResponse)
async def chat_conversation(conversation_id: str):
    try:
        normalized_id = conversation_store.normalize_conversation_id(conversation_id)
        data = conversation_store.load(normalized_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid conversation_id") from exc

    if data is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": normalized_id,
        "history": data["history"],
        "summary": data["summary"],
    }
