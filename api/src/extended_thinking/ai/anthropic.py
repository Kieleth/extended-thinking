"""Anthropic AI provider."""

import anthropic
from typing import AsyncIterator


class AnthropicProvider:
    """Anthropic Claude provider."""

    name = "anthropic"

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.async_client = anthropic.AsyncAnthropic(api_key=api_key)

    def list_models(self) -> list[str]:
        return [
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ]

    async def complete(self, messages: list[dict], model: str | None = None) -> str:
        model = model or "claude-sonnet-4-6"
        response = await self.async_client.messages.create(
            model=model,
            max_tokens=4096,
            messages=messages,
        )
        return response.content[0].text

    async def complete_cached(
        self,
        system_blocks: list[dict],
        messages: list[dict],
        model: str | None = None,
    ) -> str:
        """Complete with prompt caching via system blocks.

        system_blocks: list of dicts with 'text' and optional 'cache_control'.
        Blocks with cache_control={"type": "ephemeral"} are cached across calls.
        Tsonts pattern: static instructions cached, dynamic KG snapshot not.
        """
        model = model or "claude-opus-4-6"
        response = await self.async_client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_blocks,
            messages=messages,
        )
        return response.content[0].text

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        model = model or "claude-sonnet-4-6"
        async with self.async_client.messages.stream(
            model=model,
            max_tokens=4096,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
