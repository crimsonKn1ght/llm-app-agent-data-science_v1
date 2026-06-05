from google import genai
from google.genai import types
from typing import AsyncGenerator


class GeminiClient:

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    async def generate(
        self,
        user_input: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ) -> str:
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if system_prompt:
            config.system_instruction = system_prompt

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=user_input,
            config=config,
        )
        return response.text

    async def stream(
        self,
        user_input: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ) -> AsyncGenerator[str, None]:
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if system_prompt:
            config.system_instruction = system_prompt

        async for chunk in self.client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=user_input,
            config=config,
        ):
            if chunk.text:
                yield chunk.text
