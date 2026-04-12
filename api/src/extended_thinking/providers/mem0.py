"""Mem0Provider. Reads from a local Mem0 installation.

Mem0 is vector-based and user-scoped. It has no structured knowledge graph,
so `get_knowledge_graph()` always returns None and `get_entities()` is empty.

Stub status: protocol-compliant, exercised against Mem0's documented API.
Real integrations across Mem0 deployment modes (local vs hosted, Python vs
REST) are landed as they are actually needed.

Requires: pip install extended-thinking[mem0]
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from extended_thinking.providers.protocol import Entity, KnowledgeGraphView, MemoryChunk

logger = logging.getLogger(__name__)


class Mem0Provider:
    """Memory provider backed by a local Mem0 installation.

    Mem0 is scoped by user_id. The provider takes one user_id and treats
    that user's memories as the entire corpus. If you need multi-user,
    instantiate one provider per user.
    """

    def __init__(self, user_id: str, config: dict | None = None):
        if not user_id:
            raise ValueError("Mem0Provider requires a non-empty user_id")
        self._user_id = user_id
        self._config = config or {}
        self._memory = None

    @property
    def name(self) -> str:
        return "mem0"

    def _get_memory(self):
        if self._memory is None:
            try:
                from mem0 import Memory
            except ImportError as e:
                raise ImportError(
                    "Mem0Provider requires the mem0ai package. "
                    "Install with: pip install extended-thinking[mem0]"
                ) from e
            self._memory = Memory.from_config(self._config) if self._config else Memory()
        return self._memory

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        try:
            mem = self._get_memory()
            results = mem.search(query=query, user_id=self._user_id, limit=limit)
            return [self._to_chunk(r) for r in _unwrap(results)]
        except Exception as e:
            logger.error("Mem0 search failed: %s", e)
            return []

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        try:
            mem = self._get_memory()
            results = mem.get_all(user_id=self._user_id, limit=limit)
            chunks = [self._to_chunk(r) for r in _unwrap(results)]
            if since:
                chunks = [c for c in chunks if c.timestamp >= since]
            chunks.sort(key=lambda c: c.timestamp, reverse=True)
            return chunks[:limit]
        except Exception as e:
            logger.error("Mem0 get_recent failed: %s", e)
            return []

    def get_entities(self) -> list[Entity]:
        # Mem0 is vector-based; no native entity extraction.
        return []

    def store_insight(self, title: str, description: str, related_concepts: list[str]) -> str:
        try:
            mem = self._get_memory()
            content = f"# {title}\n\n{description}\n\nRelated: {', '.join(related_concepts)}"
            result = mem.add(
                messages=[{"role": "assistant", "content": content}],
                user_id=self._user_id,
                metadata={"source": "extended-thinking", "kind": "insight", "title": title},
            )
            ids = _unwrap(result)
            if ids and isinstance(ids[0], dict) and "id" in ids[0]:
                return str(ids[0]["id"])
            # Fallback deterministic id if Mem0 did not return one.
            return hashlib.sha256(f"{title}{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16]
        except Exception as e:
            logger.error("Mem0 store_insight failed: %s", e)
            return ""

    def get_insights(self) -> list[MemoryChunk]:
        try:
            mem = self._get_memory()
            results = mem.get_all(user_id=self._user_id, limit=200)
            chunks = [self._to_chunk(r) for r in _unwrap(results)]
            return [c for c in chunks if c.metadata.get("kind") == "insight"]
        except Exception as e:
            logger.error("Mem0 get_insights failed: %s", e)
            return []

    def get_stats(self) -> dict:
        try:
            mem = self._get_memory()
            results = _unwrap(mem.get_all(user_id=self._user_id, limit=10000))
            return {
                "total_memories": len(results),
                "total_entities": 0,
                "total_insights": sum(1 for r in results if _get_meta(r).get("kind") == "insight"),
                "last_updated": None,
                "provider": self.name,
                "user_id": self._user_id,
            }
        except Exception as e:
            logger.error("Mem0 get_stats failed: %s", e)
            return {"total_memories": 0, "provider": self.name}

    def get_knowledge_graph(self) -> KnowledgeGraphView | None:
        # Mem0 has no structured KG surface. ET's UnifiedGraph will use
        # ConceptStore alone for this provider.
        return None

    def _to_chunk(self, record: dict) -> MemoryChunk:
        meta = _get_meta(record)
        return MemoryChunk(
            id=str(record.get("id", "")),
            content=record.get("memory") or record.get("text") or record.get("content") or "",
            source=meta.get("source", "mem0"),
            timestamp=str(record.get("created_at") or record.get("updated_at") or ""),
            metadata={**meta, "user_id": self._user_id},
        )


def _unwrap(results) -> list[dict]:
    """Mem0's responses are sometimes a list, sometimes {'results': [...]}.
    Normalize to a list of dicts."""
    if results is None:
        return []
    if isinstance(results, dict):
        inner = results.get("results") or results.get("memories") or []
        return inner if isinstance(inner, list) else []
    if isinstance(results, list):
        return results
    return []


def _get_meta(record: dict) -> dict:
    meta = record.get("metadata")
    return meta if isinstance(meta, dict) else {}
