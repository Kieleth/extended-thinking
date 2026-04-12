"""VectorStore protocol — vendor-agnostic semantic search.

ChromaDB is the default implementation. The protocol allows swapping to
FAISS, Qdrant, sqlite-vec, or any other vector store without touching
pipeline code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class VectorResult:
    """A single result from a vector search."""

    id: str
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    """Protocol for semantic search over text chunks."""

    def add(self, id: str, text: str, metadata: dict) -> None:
        """Store a text chunk with embeddings. Idempotent by id."""
        ...

    def search(self, query: str, limit: int = 20,
               where: dict | None = None) -> list[VectorResult]:
        """Semantic search. Returns results sorted by relevance."""
        ...

    def delete(self, ids: list[str]) -> None:
        """Remove chunks by id."""
        ...

    def count(self) -> int:
        """Total number of stored chunks."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed arbitrary texts using this store's embedding function.

        Used by algorithms that need raw vectors (e.g., embedding-based
        entity resolution) without committing them to storage.

        Each embedding has a consistent dimensionality for a given store.
        """
        ...
