from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator

import anthropic

logger = logging.getLogger(__name__)

DEFAULT_LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
DEFAULT_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
RETRY_BACKOFF_SECONDS = 0.25


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_LLM_MAX_RETRIES,
    ):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def _retry_delay(self, attempt: int) -> None:
        await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

    async def close(self) -> None:
        close_func = getattr(self._client, "aclose", None)
        if close_func is not None:
            await close_func()
            return

        close_func = getattr(self._client, "close", None)
        if close_func is not None:
            maybe_awaitable = close_func()
            if hasattr(maybe_awaitable, "__await__"):
                await maybe_awaitable

    async def generate(
        self,
        user_input: str,
        system_prompt: str,
        temperature: float = 0.5,
        max_output_tokens: int = 4096,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.messages.create(
                        model=self._model,
                        max_tokens=max_output_tokens,
                        temperature=temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_input}],
                    ),
                    timeout=self._timeout_seconds,
                )
                return response.content[0].text
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM generate failed | attempt=%d/%d | error=%s",
                    attempt + 1,
                    self._max_retries + 1,
                    type(exc).__name__,
                )
                if attempt < self._max_retries:
                    await self._retry_delay(attempt)

        assert last_error is not None
        raise last_error

    async def stream(
        self,
        user_input: str,
        system_prompt: str,
        temperature: float = 0.5,
        max_output_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        last_error: Exception | None = None
        yielded_any = False

        for attempt in range(self._max_retries + 1):
            stream_context = self._client.messages.stream(
                model=self._model,
                max_tokens=max_output_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_input}],
            )
            try:
                stream = await asyncio.wait_for(
                    stream_context.__aenter__(),
                    timeout=self._timeout_seconds,
                )
                try:
                    async for text in stream.text_stream:
                        yielded_any = True
                        yield text
                finally:
                    await stream_context.__aexit__(None, None, None)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM stream failed | attempt=%d/%d | error=%s",
                    attempt + 1,
                    self._max_retries + 1,
                    type(exc).__name__,
                )
                if yielded_any or attempt >= self._max_retries:
                    raise
                await self._retry_delay(attempt)

        assert last_error is not None
        raise last_error
