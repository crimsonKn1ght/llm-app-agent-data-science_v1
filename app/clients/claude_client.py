from __future__ import annotations

import anthropic
from typing import AsyncGenerator


class ClaudeClient:

    def __init__(self, api_key: str, model_name: str = "claude-sonnet-4-6"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model_name = model_name

    async def generate(
        self,
        user_input: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ) -> str:
        kwargs = dict(
            model=self.model_name,
            max_tokens=max_output_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": user_input}],
        )
        if system_prompt:
            kwargs["system"] = system_prompt

        response = await self.client.messages.create(**kwargs)
        return response.content[0].text

    async def stream(
        self,
        user_input: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        kwargs = dict(
            model=self.model_name,
            max_tokens=max_output_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": user_input}],
        )
        if system_prompt:
            kwargs["system"] = system_prompt

        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
