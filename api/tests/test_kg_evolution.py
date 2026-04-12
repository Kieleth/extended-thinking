"""Tests for KG evolution: schema migrations, typed entities, temporal edges, provenance, access tracking."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.processing.concept_store import ConceptStore, CURRENT_SCHEMA_VERSION


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ConceptStore(Path(tmp) / "test.db")


class TestSchemaMigration:

    def test_schema_version_exists(self, store):
        assert store.schema_version == CURRENT_SCHEMA_VERSION

    def test_schema_version_is_current(self, store):
        assert store.schema_version == CURRENT_SCHEMA_VERSION

    def test_new_columns_exist_on_concepts(self, store):
        """Migration 2 adds entity_type, provider_source, access_count, last_accessed."""
        store.add_concept("test", "Test", "topic", "desc")
        concept = store.get_concept("test")
        assert concept["entity_type"] == "concept"
        assert concept["provider_source"] == ""
        assert concept["access_count"] == 0
        assert concept["last_accessed"] == ""

    def test_new_columns_exist_on_relationships(self, store):
        """Migration 2 adds edge_type, valid_from, valid_to, provenance, access_count."""
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "topic", "")
        store.add_relationship("a", "b", weight=1.0)
        rels = store.get_relationships("a")
        assert len(rels) == 1
        assert rels[0]["edge_type"] == "RelatesTo"
        assert rels[0]["access_count"] == 0

    def test_migration_is_idempotent(self):
        """Opening the same DB twice doesn't fail or double-migrate."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            store1 = ConceptStore(db_path)
            store1.add_concept("x", "X", "topic", "")
            store2 = ConceptStore(db_path)
            assert store2.schema_version == CURRENT_SCHEMA_VERSION
            assert store2.get_concept("x")["name"] == "X"


class TestAccessTracking:

    def test_record_access_increments(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.record_access("a")
        store.record_access("a")
        concept = store.get_concept("a")
        assert concept["access_count"] == 2
        assert concept["last_accessed"] != ""

    def test_record_access_nonexistent_is_safe(self, store):
        """Recording access on a missing entity doesn't crash."""
        store.record_access("nonexistent")  # no-op, no error

    def test_record_edge_access(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)
        store.record_edge_access("a", "b")
        rels = store.get_relationships("a")
        assert rels[0]["access_count"] == 1
        assert rels[0]["last_accessed"] != ""


class TestProvenance:

    def test_add_and_get_provenance(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        prov_id = store.add_provenance(
            entity_id="a",
            source_provider="claude-code",
            source_chunk_id="chunk-123",
            llm_model="haiku",
        )
        assert prov_id.startswith("prov-")

        records = store.get_provenance("a")
        assert len(records) == 1
        assert records[0]["source_provider"] == "claude-code"
        assert records[0]["source_chunk_id"] == "chunk-123"
        assert records[0]["llm_model"] == "haiku"
        assert records[0]["created_at"] != ""

    def test_multiple_provenance_records(self, store):
        """A concept can have multiple provenance entries (seen in multiple chunks)."""
        store.add_concept("a", "Alpha", "topic", "")
        store.add_provenance("a", "claude-code", "chunk-1")
        store.add_provenance("a", "claude-code", "chunk-2")
        store.add_provenance("a", "mempalace", "drawer-99")

        records = store.get_provenance("a")
        assert len(records) == 3
        providers = {r["source_provider"] for r in records}
        assert providers == {"claude-code", "mempalace"}

    def test_provenance_empty_for_unknown_entity(self, store):
        assert store.get_provenance("nonexistent") == []


class TestTemporalEdges:

    def test_relationship_has_temporal_fields(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)
        rels = store.get_relationships("a")
        assert "valid_from" in rels[0]
        assert "valid_to" in rels[0]
        # Defaults: empty valid_from, null valid_to
        assert rels[0]["valid_from"] == ""
        assert rels[0]["valid_to"] is None
