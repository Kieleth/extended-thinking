"""Tests for entity resolution, concept merging, and co-occurrence groups."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.processing.concept_store import ConceptStore, CURRENT_SCHEMA_VERSION


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ConceptStore(Path(tmp) / "test.db")


class TestSchemaV3:

    def test_schema_version_is_3(self, store):
        assert store.schema_version == 3

    def test_canonical_id_column_exists(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        c = store.get_concept("a")
        assert c["canonical_id"] == ""

    def test_co_occurrences_table_exists(self, store):
        cid = store.add_co_occurrence("chunk-1", ["a", "b"], "test")
        assert cid.startswith("cooccur-")


class TestFindSimilarConcept:

    def test_exact_match_by_id(self, store):
        store.add_concept("additive-only-extensions", "Additive-only extensions", "decision", "")
        match = store.find_similar_concept("Additive-only extensions")
        assert match is not None
        assert match["id"] == "additive-only-extensions"

    def test_similar_name_above_threshold(self, store):
        store.add_concept("additive-only-extensions", "Additive-only extensions", "decision", "")
        match = store.find_similar_concept("Additive only extensions", threshold=0.85)
        assert match is not None
        assert match["id"] == "additive-only-extensions"

    def test_dissimilar_name_returns_none(self, store):
        store.add_concept("additive-only-extensions", "Additive-only extensions", "decision", "")
        match = store.find_similar_concept("Fleet deployment model", threshold=0.85)
        assert match is None

    def test_empty_store_returns_none(self, store):
        match = store.find_similar_concept("anything")
        assert match is None

    def test_case_insensitive(self, store):
        store.add_concept("silk-validation", "Silk as validation layer", "entity", "")
        match = store.find_similar_concept("silk as validation layer", threshold=0.85)
        assert match is not None


class TestMergeConcept:

    def test_merge_sums_frequency(self, store):
        store.add_concept("a", "Alpha", "topic", "First")
        store.add_concept("a", "Alpha", "topic", "First")  # freq = 2
        store.add_concept("b", "Alpha variant", "topic", "Second")

        store.merge_concept("b", "a")

        target = store.get_concept("a")
        assert target["frequency"] == 3  # 2 + 1

    def test_merge_sets_canonical_id(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Alpha variant", "topic", "")

        store.merge_concept("b", "a")

        source = store.get_concept("b")
        assert source["canonical_id"] == "a"

    def test_merge_repoints_relationships(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "topic", "")
        store.add_concept("c", "Gamma", "topic", "")
        store.add_relationship("b", "c", weight=1.0)

        store.merge_concept("b", "a")

        # Relationship should now be a -> c
        rels = store.get_relationships("a")
        assert any(r["target_id"] == "c" for r in rels)

    def test_merge_repoints_provenance(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "topic", "")
        store.add_provenance("b", "claude-code", "chunk-1")

        store.merge_concept("b", "a")

        prov = store.get_provenance("a")
        assert len(prov) == 1
        assert prov[0]["source_chunk_id"] == "chunk-1"

    def test_merge_nonexistent_is_safe(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.merge_concept("nonexistent", "a")  # no crash
        assert store.get_concept("a")["frequency"] == 1


class TestCoOccurrence:

    def test_add_and_retrieve(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_co_occurrence("chunk-1", ["a", "b"], "same file")

        groups = store.get_co_occurrences("a")
        assert len(groups) == 1
        assert "a" in groups[0]["concept_ids"]
        assert "b" in groups[0]["concept_ids"]
        assert groups[0]["context"] == "same file"

    def test_multiple_groups(self, store):
        store.add_co_occurrence("chunk-1", ["a", "b", "c"], "file1")
        store.add_co_occurrence("chunk-2", ["a", "d"], "file2")

        groups = store.get_co_occurrences("a")
        assert len(groups) == 2

    def test_concept_not_in_any_group(self, store):
        store.add_co_occurrence("chunk-1", ["a", "b"], "test")
        groups = store.get_co_occurrences("z")
        assert groups == []

    def test_co_occurrence_preserves_full_group(self, store):
        """Unlike pairwise edges, co-occurrence keeps the full set."""
        store.add_co_occurrence("chunk-1", ["a", "b", "c", "d"], "same session")
        groups = store.get_co_occurrences("a")
        assert len(groups[0]["concept_ids"]) == 4
