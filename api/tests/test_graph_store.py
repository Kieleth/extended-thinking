"""Tests for GraphStore (Kuzu-backed). Same interface as ConceptStore."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


class TestGraphStoreConcepts:

    def test_add_concept(self, store):
        store.add_concept("test", "Test Concept", "topic", "A test concept")
        c = store.get_concept("test")
        assert c is not None
        assert c["name"] == "Test Concept"
        assert c["category"] == "topic"
        assert c["frequency"] == 1

    def test_add_duplicate_increments_frequency(self, store):
        store.add_concept("test", "Test", "topic", "First")
        store.add_concept("test", "Test", "topic", "Second")
        c = store.get_concept("test")
        assert c["frequency"] == 2

    def test_list_concepts(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "theme", "")
        concepts = store.list_concepts()
        assert len(concepts) == 2

    def test_list_concepts_by_frequency(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "theme", "")
        store.add_concept("b", "Beta", "theme", "")
        concepts = store.list_concepts(order_by="frequency")
        assert concepts[0]["name"] == "Beta"


class TestGraphStoreRelationships:

    def test_add_relationship(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=2.0, context="co-occur")
        rels = store.get_relationships("a")
        assert len(rels) == 1
        assert rels[0]["target_id"] == "b"
        assert rels[0]["weight"] == 2.0

    def test_undirected_relationships(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)
        # Should find from both sides
        rels_a = store.get_relationships("a")
        rels_b = store.get_relationships("b")
        assert len(rels_a) == 1
        assert len(rels_b) == 1


class TestGraphStoreWisdoms:

    def test_add_wisdom(self, store):
        store.add_concept("a", "A", "topic", "")
        wid = store.add_wisdom("Test Wisdom", "Description", related_concept_ids=["a"])
        assert wid.startswith("wisdom-")

    def test_list_wisdoms(self, store):
        store.add_wisdom("W1", "D1")
        store.add_wisdom("W2", "D2")
        wisdoms = store.list_wisdoms()
        assert len(wisdoms) == 2

    def test_update_status(self, store):
        wid = store.add_wisdom("W", "D")
        store.update_wisdom_status(wid, "seen")
        w = store.get_wisdom(wid)
        assert w["status"] == "seen"


class TestGraphStoreChunkTracking:

    def test_mark_and_check(self, store):
        store.mark_chunk_processed("chunk-1")
        assert store.is_chunk_processed("chunk-1")
        assert not store.is_chunk_processed("chunk-2")

    def test_filter_unprocessed(self, store):
        store.mark_chunk_processed("a")
        store.mark_chunk_processed("b")
        result = store.filter_unprocessed(["a", "b", "c", "d"])
        assert result == ["c", "d"]


class TestGraphStoreAccessTracking:

    def test_record_access(self, store):
        store.add_concept("a", "A", "topic", "")
        store.record_access("a")
        store.record_access("a")
        c = store.get_concept("a")
        assert c["access_count"] == 2
        assert c["last_accessed"] != ""


class TestGraphStoreProvenance:

    def test_add_and_get(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_provenance("a", "claude-code", "chunk-1", "haiku")
        prov = store.get_provenance("a")
        assert len(prov) == 1
        assert prov[0]["source_provider"] == "claude-code"
        assert prov[0]["llm_model"] == "haiku"


class TestGraphStoreGraphQueries:

    def test_find_path(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_concept("c", "C", "topic", "")
        store.add_relationship("a", "b", weight=1.0)
        store.add_relationship("b", "c", weight=1.0)
        path = store.find_path("a", "c")
        assert path is not None
        assert len(path) == 3

    def test_find_path_no_connection(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("z", "Z", "topic", "")
        path = store.find_path("a", "z")
        assert path is None

    def test_get_stats(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b")
        store.add_wisdom("W", "D")
        stats = store.get_stats()
        assert stats["total_concepts"] == 2
        assert stats["total_relationships"] == 1
        assert stats["total_wisdoms"] == 1

    def test_get_neighborhood(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0, context="test")
        hood = store.get_neighborhood("a")
        assert hood is not None
        assert len(hood["connections"]) == 1

    def test_spread_activation(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_concept("c", "C", "topic", "")
        store.add_relationship("a", "b", weight=2.0)
        store.add_relationship("b", "c", weight=1.0)
        results = store.spread_activation(["a"], depth=3)
        ids = [cid for cid, _ in results]
        assert "b" in ids

    def test_active_nodes(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.record_access("a")
        active = store.active_nodes(k=2)
        assert len(active) <= 2
