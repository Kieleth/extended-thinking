"""ChromaDB implementation of VectorStore.

Uses ChromaDB's built-in embedding function (all-MiniLM-L6-v2 by default).
Persistent storage under a configurable directory.
"""

from __future__ import annotations

from pathlib import Path

import chromadb

from extended_thinking.storage.vector_protocol import VectorResult

DEFAULT_COLLECTION = "et_chunks"


class ChromaDBVectorStore:
    """VectorStore backed by ChromaDB."""

    def __init__(self, persist_dir: Path | None = None,
                 collection_name: str = DEFAULT_COLLECTION):
        if persist_dir is not None:
            persist_dir = Path(persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_dir))
        else:
            self._client = chromadb.EphemeralClient()

        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, id: str, text: str, metadata: dict) -> None:
        """Store a text chunk. Idempotent: upserts by id."""
        # ChromaDB rejects empty metadata dicts
        meta = metadata if metadata else {"_": ""}
        self._collection.upsert(
            ids=[id],
            documents=[text],
            metadatas=[meta],
        )

    def search(self, query: str, limit: int = 20,
               where: dict | None = None) -> list[VectorResult]:
        """Semantic search via ChromaDB embeddings."""
        total = self._collection.count()
        if total == 0:
            return []

        kwargs: dict = {
            "query_texts": [query],
            "n_results": min(limit, total),
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]

        return [
            VectorResult(
                id=rid,
                content=doc,
                score=1.0 - dist,  # ChromaDB cosine distance → similarity
                metadata=meta or {},
            )
            for rid, doc, meta, dist in zip(ids, documents, metadatas, distances)
        ]

    def delete(self, ids: list[str]) -> None:
        """Remove chunks by id."""
        if ids:
            self._collection.delete(ids=ids)

    def count(self) -> int:
        """Total stored chunks."""
        return self._collection.count()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed arbitrary texts using this collection's embedding function.

        Returns raw vectors without storing them. Used by entity resolution
        and similar algorithms that need ad-hoc similarity computations.
        """
        if not texts:
            return []
        ef = self._collection._embedding_function
        if ef is None:
            raise RuntimeError("ChromaDB collection has no embedding function")
        return list(ef(texts))
