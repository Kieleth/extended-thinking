"""ET-owned storage layer: VectorStore + KnowledgeGraph.

StorageLayer is the interface boundary. Pipeline and MCP server couple to it,
not to internal storage details. Changes inside StorageLayer are invisible
to callers.

The KG backend is Kuzu (embedded graph database). Previous SQLite ConceptStore
is still available for tests and backward compatibility.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

from extended_thinking.processing.concept_store import ConceptStore
from extended_thinking.storage.graph_store import GraphStore
from extended_thinking.storage.vector_protocol import VectorStore

# The KG type: either Kuzu GraphStore or legacy SQLite ConceptStore
KnowledgeStore = Union[GraphStore, ConceptStore]


@dataclass
class StorageLayer:
    """ET's owned storage: vectors (optional) + knowledge graph."""

    vectors: VectorStore | None
    kg: KnowledgeStore

    @classmethod
    def default(cls, data_dir: Path) -> StorageLayer:
        """Create StorageLayer with Kuzu KG + ChromaDB vectors.

        Falls back gracefully: no ChromaDB = no vectors.
        """
        data_dir.mkdir(parents=True, exist_ok=True)

        vectors = None
        try:
            from extended_thinking.storage.vector_chromadb import ChromaDBVectorStore
            vectors = ChromaDBVectorStore(data_dir / "vectors")
        except ImportError:
            logger.info("ChromaDB not available, running without VectorStore")

        # Pass vectors into the GraphStore so typed inserts (ADR 013 C6)
        # index automatically. Kuzu + Chroma share the same data dir.
        kg = GraphStore(data_dir / "knowledge", vectors=vectors)

        return cls(vectors=vectors, kg=kg)

    @classmethod
    def lite(cls, data_dir: Path) -> StorageLayer:
        """Create StorageLayer without VectorStore (no ChromaDB dependency)."""
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(vectors=None, kg=GraphStore(data_dir / "knowledge"))

    @classmethod
    def sqlite(cls, data_dir: Path) -> StorageLayer:
        """Create StorageLayer with legacy SQLite ConceptStore (for migration/tests)."""
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(vectors=None, kg=ConceptStore(data_dir / "concepts.db"))
