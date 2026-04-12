"""MemPalaceProvider — reads from a local mempalace installation.

Uses mempalace's ChromaDB (semantic search, 60k+ drawers) and SQLite KG
(temporal entities/triples). Highest quality retrieval of all providers.

Requires: pip install mempalace (chromadb, pyyaml)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, Fact, KnowledgeGraphView, MemoryChunk

logger = logging.getLogger(__name__)

DEFAULT_PALACE_DIR = Path.home() / ".mempalace"


class MemPalaceProvider:
    """Memory provider backed by a local mempalace installation.

    Reads drawers from ChromaDB, entities from SQLite KG.
    Stores insights as drawers in wing_wisdom + KG triples.
    """

    def __init__(self, palace_dir: Path | None = None):
        self._palace_dir = palace_dir or DEFAULT_PALACE_DIR
        self._palace_path = str(self._palace_dir / "palace")
        self._kg_path = str(self._palace_dir / "knowledge_graph.sqlite3")

        # Lazy imports — only fail when actually used
        self._collection = None
        self._kg = None

    @property
    def name(self) -> str:
        return "mempalace"

    def _get_collection(self):
        if self._collection is None:
            from mempalace.palace import get_collection
            self._collection = get_collection(self._palace_path)
        return self._collection

    def _get_kg(self):
        if self._kg is None:
            from mempalace.knowledge_graph import KnowledgeGraph
            self._kg = KnowledgeGraph(db_path=self._kg_path)
        return self._kg

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        """Semantic search via ChromaDB embeddings."""
        try:
            from mempalace.searcher import search_memories
            results = search_memories(query, self._palace_path, n_results=limit)

            chunks = []
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            ids = results.get("ids", [[]])[0]

            for doc, meta, doc_id in zip(documents, metadatas, ids):
                chunks.append(self._to_chunk(doc_id, doc, meta))

            return chunks
        except Exception as e:
            logger.error("MemPalace search failed: %s", e)
            return []

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        """Get recent drawers by date_mined metadata."""
        try:
            collection = self._get_collection()

            where = {}
            if since:
                where = {"date_mined": {"$gte": since}}

            results = collection.get(
                limit=limit,
                where=where if where else None,
                include=["documents", "metadatas"],
            )

            chunks = []
            documents = results.get("documents", [])
            metadatas = results.get("metadatas", [])
            ids = results.get("ids", [])

            for doc, meta, doc_id in zip(documents, metadatas, ids):
                chunks.append(self._to_chunk(doc_id, doc, meta))

            # Sort by timestamp descending
            chunks.sort(key=lambda c: c.timestamp, reverse=True)
            return chunks[:limit]
        except Exception as e:
            logger.error("MemPalace get_recent failed: %s", e)
            return []

    def get_entities(self) -> list[Entity]:
        """Get entities from the mempalace knowledge graph."""
        try:
            kg = self._get_kg()
            kg_stats = kg.stats()
            entities = []

            # Query all entities via the stats
            # The KG stores entities as (name, type, properties)
            import sqlite3
            conn = sqlite3.connect(self._kg_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM entities LIMIT 200").fetchall()
            conn.close()

            for row in rows:
                entities.append(Entity(
                    name=row["name"],
                    entity_type=row["type"],
                    properties={"id": row["id"]},
                ))

            return entities
        except Exception as e:
            logger.error("MemPalace get_entities failed: %s", e)
            return []

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        """Store insight as a drawer in wing_wisdom + KG triples."""
        try:
            collection = self._get_collection()
            now = datetime.now(timezone.utc).isoformat()
            insight_id = hashlib.sha256(f"{title}{now}".encode()).hexdigest()[:16]

            content = f"# {title}\n\n{description}\n\nRelated: {', '.join(related_concepts)}"

            collection.add(
                ids=[insight_id],
                documents=[content],
                metadatas=[{
                    "wing": "wing_wisdom",
                    "room": "insights",
                    "date_mined": now,
                    "source_file": "extended-thinking",
                    "memory_type": "hall_discoveries",
                }],
            )

            # Also add to KG as triples
            try:
                kg = self._get_kg()
                for concept in related_concepts[:5]:
                    kg.add_triple(
                        subject=f"wisdom:{insight_id[:8]}",
                        predicate="suggests_exploring",
                        obj=concept,
                        valid_from=now[:10],
                        source_file="extended-thinking",
                    )
            except Exception as e:
                logger.warning("Failed to add KG triples: %s", e)

            return insight_id
        except Exception as e:
            logger.error("MemPalace store_insight failed: %s", e)
            return ""

    def get_insights(self) -> list[MemoryChunk]:
        """Get insights from wing_wisdom."""
        try:
            collection = self._get_collection()
            results = collection.get(
                where={"wing": "wing_wisdom"},
                include=["documents", "metadatas"],
                limit=50,
            )

            chunks = []
            for doc, meta, doc_id in zip(
                results.get("documents", []),
                results.get("metadatas", []),
                results.get("ids", []),
            ):
                chunks.append(self._to_chunk(doc_id, doc, meta))

            return chunks
        except Exception as e:
            logger.error("MemPalace get_insights failed: %s", e)
            return []

    def get_stats(self) -> dict:
        """Palace statistics."""
        try:
            collection = self._get_collection()
            total = collection.count()

            # Count wings
            # ChromaDB doesn't have a direct "distinct" query, so approximate
            wings = set()
            sample = collection.get(limit=100, include=["metadatas"])
            for meta in sample.get("metadatas", []):
                if meta and "wing" in meta:
                    wings.add(meta["wing"])

            entity_count = 0
            try:
                kg = self._get_kg()
                entity_count = kg.stats().get("entities", 0)
            except Exception as e:
                logger.debug("KG stats unavailable: %s", e)

            return {
                "total_memories": total,
                "total_wings": len(wings),
                "total_entities": entity_count,
                "total_insights": 0,  # TODO: count wing_wisdom
                "last_updated": None,
                "provider": self.name,
                "palace_dir": str(self._palace_dir),
            }
        except Exception as e:
            logger.error("MemPalace get_stats failed: %s", e)
            return {"total_memories": 0, "provider": self.name}

    def get_knowledge_graph(self) -> "MemPalaceKGView | None":
        """Return a queryable view of mempalace's temporal knowledge graph."""
        try:
            return MemPalaceKGView(self._kg_path)
        except Exception as e:
            logger.error("Failed to create KG view: %s", e)
            return None

    # ── Private ──────────────────────────────────────────────────────

    def _to_chunk(self, doc_id: str, content: str, metadata: dict) -> MemoryChunk:
        """Convert a mempalace drawer to a MemoryChunk."""
        meta = metadata or {}
        timestamp = meta.get("date_mined") or meta.get("filed_at") or ""
        return MemoryChunk(
            id=doc_id,
            content=content or "",
            source=meta.get("source_file", ""),
            timestamp=timestamp,
            metadata=meta,
        )


class MemPalaceKGView:
    """Read-only view of mempalace's temporal knowledge graph.

    Wraps the SQLite entities + triples tables. Implements KnowledgeGraphView
    protocol for use with the UnifiedGraph layer.
    """

    def __init__(self, kg_path: str):
        import sqlite3
        self._conn = sqlite3.connect(kg_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def facts(self, subject: str | None = None) -> list[Fact]:
        # Filter out ET-originated triples to prevent echo loop
        if subject:
            rows = self._conn.execute(
                "SELECT * FROM triples WHERE subject=? AND valid_to IS NULL "
                "AND (source_file IS NULL OR source_file != 'extended-thinking')",
                (subject,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM triples WHERE valid_to IS NULL "
                "AND (source_file IS NULL OR source_file != 'extended-thinking')"
            ).fetchall()

        return [
            Fact(
                subject=r["subject"],
                predicate=r["predicate"],
                object=r["object"],
                valid_from=r["valid_from"] or "",
                valid_to=r["valid_to"],
                confidence=r["confidence"],
                source=r["source_file"] or "",
            )
            for r in rows
        ]

    def entities(self) -> list[Entity]:
        rows = self._conn.execute("SELECT * FROM entities").fetchall()
        return [
            Entity(
                name=r["name"],
                entity_type=r["type"],
                properties={"id": r["id"], "created_at": r["created_at"]},
            )
            for r in rows
        ]

    def predicates(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT predicate FROM triples").fetchall()
        return [r[0] for r in rows]

    def neighbors(self, entity_id: str) -> list[str]:
        outgoing = self._conn.execute(
            "SELECT DISTINCT object FROM triples WHERE subject=? AND valid_to IS NULL",
            (entity_id,),
        ).fetchall()
        incoming = self._conn.execute(
            "SELECT DISTINCT subject FROM triples WHERE object=? AND valid_to IS NULL",
            (entity_id,),
        ).fetchall()
        return list({r[0] for r in outgoing} | {r[0] for r in incoming})
