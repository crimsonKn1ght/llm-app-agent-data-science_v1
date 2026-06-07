from __future__ import annotations

import logging
from typing import AsyncIterator

import anthropic

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(
        self,
        user_input: str,
        system_prompt: str,
        temperature: float = 0.5,
        max_output_tokens: int = 4096,
    ) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_output_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_input}],
        )
        return response.content[0].text

    async def stream(
        self,
        user_input: str,
        system_prompt: str,
        temperature: float = 0.5,
        max_output_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_output_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_input}],
        ) as stream:
            async for text in stream.text_stream:
                yield text
