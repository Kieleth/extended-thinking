"""Fake AIProvider implementations for the fast-path acceptance suite.

Two flavors:

- FakeListLLM: returns a scripted sequence of responses. Raises on exhaustion.
  Model after LangChain's FakeListLLM and Mem0's `side_effect=[...]` pattern.

- DummyLM: keys responses by a substring match on the prompt. Multiple prompts
  can coexist in one test, order-independent. Model after DSPy's DummyLM.

Both satisfy the AIProvider protocol at `extended_thinking.ai.base.AIProvider`:
    name, list_models(), complete(messages, model=None), stream(messages, model=None)

Tests patch `extended_thinking.ai.registry.get_provider` (the same patch point
unit tests already use) to return a fake instance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Iterable


class FakeListLLM:
    """Returns responses from a fixed sequence, one per complete() call.

    Example:
        fake = FakeListLLM(["first reply", "second reply"])
        await fake.complete([{"role": "user", "content": "hi"}])   # -> "first reply"
        await fake.complete([{"role": "user", "content": "ok"}])   # -> "second reply"
        await fake.complete([{"role": "user", "content": "..."}])  # -> IndexError

    Attributes:
        calls: list of (messages, model) tuples recorded for later assertions.
    """

    name = "fake-list"

    def __init__(self, responses: Iterable[str], *, model_ids: list[str] | None = None):
        self._responses = list(responses)
        self._cursor = 0
        self._model_ids = model_ids or ["fake-small", "fake-large"]
        self.calls: list[tuple[list[dict], str | None]] = []

    def list_models(self) -> list[str]:
        return list(self._model_ids)

    async def complete(self, messages: list[dict], model: str | None = None) -> str:
        self.calls.append((messages, model))
        if self._cursor >= len(self._responses):
            raise IndexError(
                f"FakeListLLM exhausted: asked for response {self._cursor + 1}, "
                f"only {len(self._responses)} scripted"
            )
        response = self._responses[self._cursor]
        self._cursor += 1
        return response

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        text = await self.complete(messages, model)
        for chunk in _chunks_of(text, 80):
            yield chunk


class DummyLM:
    """Returns responses keyed by a substring match on the prompt.

    Rules:
      - If any key in `responses` appears as a substring of the concatenated
        prompt text, that key's value is returned. First match in insertion
        order wins.
      - If no key matches, `default` is returned.
      - Order-independent across tests; good for multi-prompt pipelines where
        calling sequence is not stable.

    Example:
        dummy = DummyLM({
            "extract concepts": EXTRACTION_JSON,
            "generate wisdom": WISDOM_JSON,
        }, default="{}")

    Attributes:
        calls: list of (messages, model) tuples recorded for later assertions.
        hits: dict keyed by matched key, incremented on every match.
    """

    name = "dummy-lm"

    def __init__(
        self,
        responses: dict[str, str],
        *,
        default: str = "{}",
        model_ids: list[str] | None = None,
    ):
        self._responses = dict(responses)
        self._default = default
        self._model_ids = model_ids or ["dummy-small"]
        self.calls: list[tuple[list[dict], str | None]] = []
        self.hits: dict[str, int] = {key: 0 for key in self._responses}

    def list_models(self) -> list[str]:
        return list(self._model_ids)

    async def complete(self, messages: list[dict], model: str | None = None) -> str:
        self.calls.append((messages, model))
        prompt = _concat_prompt(messages)
        for key, reply in self._responses.items():
            if key in prompt:
                self.hits[key] += 1
                return reply
        return self._default

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        text = await self.complete(messages, model)
        for chunk in _chunks_of(text, 80):
            yield chunk


def _concat_prompt(messages: list[dict]) -> str:
    """Flatten message list into a single string for substring matching."""
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
    return "\n".join(parts)


def _chunks_of(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i : i + size]
