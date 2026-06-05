from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_query: str = Field(..., min_length=1, max_length=5000)
    conversation_id: Optional[str] = None


class StreamChunk(BaseModel):
    text: str
    is_final: bool
    full_response: Optional[str] = None
    error: Optional[str] = None
