"""AI provider abstraction."""

from typing import AsyncIterator, Protocol


class AIProvider(Protocol):
    """Protocol for AI provider implementations."""

    name: str

    def list_models(self) -> list[str]:
        """Return available model IDs."""
        ...

    async def complete(self, messages: list[dict], model: str | None = None) -> str:
        """Send messages and return the full response text."""
        ...

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        """Send messages and stream response chunks."""
        ...
