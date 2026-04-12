"""OpenAI AI provider (also covers OpenAI-compatible endpoints)."""

from typing import AsyncIterator

import openai


class OpenAIProvider:
    """OpenAI provider (GPT-4o, etc.). Also works with OpenAI-compatible APIs."""

    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None):
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    def list_models(self) -> list[str]:
        return ["gpt-4o", "gpt-4o-mini"]

    async def complete(self, messages: list[dict], model: str | None = None) -> str:
        model = model or "gpt-4o"
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        model = model or "gpt-4o"
        stream = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
