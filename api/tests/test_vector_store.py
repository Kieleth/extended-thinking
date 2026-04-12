"""Tests for VectorStore protocol and ChromaDB implementation."""

import tempfile
import uuid
from pathlib import Path

import pytest

from extended_thinking.storage.vector_protocol import VectorResult, VectorStore
from extended_thinking.storage.vector_chromadb import ChromaDBVectorStore
from extended_thinking.storage import StorageLayer
from extended_thinking.storage.graph_store import GraphStore


class TestVectorResult:

    def test_creation(self):
        r = VectorResult(id="chunk-1", content="hello world", score=0.95, metadata={"source": "test"})
        assert r.id == "chunk-1"
        assert r.score == 0.95

    def test_immutable(self):
        r = VectorResult(id="a", content="b", score=0.5)
        with pytest.raises(AttributeError):
            r.id = "changed"

    def test_default_metadata(self):
        r = VectorResult(id="a", content="b", score=0.5)
        assert r.metadata == {}


class TestChromaDBVectorStore:

    @pytest.fixture
    def store(self):
        """Ephemeral ChromaDB store with unique collection per test."""
        return ChromaDBVectorStore(persist_dir=None, collection_name=f"test_{uuid.uuid4().hex[:8]}")

    def test_is_vector_store(self, store):
        assert isinstance(store, VectorStore)

    def test_add_and_count(self, store):
        store.add("c1", "The ontology is a type system", {"source": "test"})
        store.add("c2", "Extensions can only add, never remove", {"source": "test"})
        assert store.count() == 2

    def test_add_is_idempotent(self, store):
        store.add("c1", "First version", {"v": "1"})
        store.add("c1", "Updated version", {"v": "2"})
        assert store.count() == 1

        results = store.search("Updated", limit=1)
        assert results[0].content == "Updated version"

    def test_search_returns_results(self, store):
        store.add("c1", "Silk is a Rust CRDT graph engine", {"project": "silk"})
        store.add("c2", "Shelob manages infrastructure deployment", {"project": "shelob"})
        store.add("c3", "The ontology defines types and constraints", {"project": "malleus"})

        results = store.search("graph database Rust", limit=2)
        assert len(results) <= 2
        assert all(isinstance(r, VectorResult) for r in results)
        # Silk should be most relevant to "graph database Rust"
        assert results[0].id == "c1"

    def test_search_scores_are_similarities(self, store):
        store.add("c1", "Silk is a graph engine", {})
        results = store.search("Silk graph", limit=1)
        assert 0.0 <= results[0].score <= 1.0

    def test_search_with_where_filter(self, store):
        store.add("c1", "Silk is fast", {"project": "silk"})
        store.add("c2", "Shelob is fast", {"project": "shelob"})

        results = store.search("fast", limit=10, where={"project": "silk"})
        assert all(r.metadata.get("project") == "silk" for r in results)

    def test_search_empty_store(self, store):
        results = store.search("anything", limit=5)
        assert results == []

    def test_delete(self, store):
        store.add("c1", "hello", {})
        store.add("c2", "world", {})
        assert store.count() == 2

        store.delete(["c1"])
        assert store.count() == 1

    def test_delete_empty_list(self, store):
        store.add("c1", "hello", {})
        store.delete([])
        assert store.count() == 1

    def test_persistent_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vectors"
            store1 = ChromaDBVectorStore(persist_dir=path)
            store1.add("c1", "persisted content", {"key": "value"})
            assert store1.count() == 1

            # New instance reads same data
            store2 = ChromaDBVectorStore(persist_dir=path)
            assert store2.count() == 1


class TestStorageLayer:

    def test_lite_mode_no_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = StorageLayer.lite(Path(tmp))
            assert storage.vectors is None
            assert isinstance(storage.kg, GraphStore)

    def test_default_creates_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = StorageLayer.default(Path(tmp))
            assert storage.vectors is not None
            assert isinstance(storage.kg, GraphStore)
            assert isinstance(storage.vectors, VectorStore)

    def test_lite_kg_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = StorageLayer.lite(Path(tmp))
            storage.kg.add_concept("test", "Test Concept", "topic", "A test")
            concepts = storage.kg.list_concepts()
            assert len(concepts) == 1
