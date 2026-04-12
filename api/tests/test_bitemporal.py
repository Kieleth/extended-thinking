"""Tests for bitemporal schema (ADR 002).

Verifies every edge carries t_valid_from, t_valid_to, t_created, t_expired,
and that GraphStore queries respect temporal semantics by default.
"""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


class TestConceptTimestamps:

    def test_concept_has_first_and_last_observed(self, store):
        store.add_concept("a", "Alpha", "topic", "first")
        c = store.get_concept("a")
        assert c["first_seen"] != ""
        assert c["last_seen"] != ""
        assert c["first_seen"] == c["last_seen"]  # just created

    def test_concept_last_observed_updates(self, store):
        store.add_concept("a", "Alpha", "topic", "first")
        first_obs = store.get_concept("a")["first_seen"]
        import time
        time.sleep(0.01)  # ensure different timestamp
        store.add_concept("a", "Alpha", "topic", "updated")
        c = store.get_concept("a")
        assert c["first_seen"] == first_obs  # unchanged
        assert c["last_seen"] > first_obs  # updated

    def test_deprecated_field_defaults_empty(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        c = store.get_concept("a")
        assert c.get("t_deprecated", "") == ""


class TestEdgeBitemporal:

    def test_relates_to_has_all_four_timestamps(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        # Query raw edge properties
        rows = store._query_all(
            "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) "
            "RETURN r.t_valid_from, r.t_valid_to, r.t_created, r.t_expired, r.t_superseded_by"
        )
        assert len(rows) == 1
        r = rows[0]
        assert r[0] != ""      # t_valid_from populated
        assert r[1] == ""      # t_valid_to empty (still valid)
        assert r[2] != ""      # t_created populated
        assert r[3] == ""      # t_expired empty (still live)
        assert r[4] == ""      # t_superseded_by empty

    def test_valid_from_equals_created_on_fresh_insert(self, store):
        """For a fresh edge with no retroactive info, valid_from == created."""
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        rows = store._query_all(
            "MATCH ()-[r:RelatesTo]->() RETURN r.t_valid_from, r.t_created"
        )
        assert rows[0][0] == rows[0][1]

    def test_get_relationships_filters_expired(self, store):
        """Expired edges should not appear in default get_relationships()."""
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        # Manually expire the edge
        store._conn.execute(
            "MATCH (a:Concept {id: 'a'})-[r:RelatesTo]->(b:Concept {id: 'b'}) "
            "SET r.t_expired = '2026-04-11T00:00:00'"
        )

        rels = store.get_relationships("a")
        assert rels == []


class TestChunkTimestamps:

    def test_chunk_has_source_and_ingested_timestamps(self, store):
        store.mark_chunk_processed("chunk-1", source="/tmp/test.md",
                                    source_type="note")
        rows = store._query_all(
            "MATCH (c:Chunk) RETURN c.t_source_created, c.t_ingested"
        )
        assert len(rows) == 1
        assert rows[0][0] != ""
        assert rows[0][1] != ""

    def test_source_created_can_differ_from_ingested(self, store):
        """t_source_created can be set explicitly (user wrote it in the past)."""
        store.mark_chunk_processed(
            "chunk-1",
            source="/tmp/test.md",
            source_type="note",
            t_source_created="2026-01-01T00:00:00",
        )
        rows = store._query_all(
            "MATCH (c:Chunk {id: 'chunk-1'}) RETURN c.t_source_created, c.t_ingested"
        )
        assert rows[0][0] == "2026-01-01T00:00:00"
        assert rows[0][1] != "2026-01-01T00:00:00"  # ingested is now, not Jan 1


class TestProvenanceBitemporal:

    def test_provenance_edge_has_timestamps(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_provenance("a", "claude-code", source_chunk_id="chunk-1",
                             llm_model="haiku", source="/tmp/conv.jsonl",
                             source_type="conversation")

        rows = store._query_all(
            "MATCH (c:Concept)-[p:HasProvenance]->(ch:Chunk) "
            "RETURN p.t_valid_from, p.t_created, p.t_expired"
        )
        assert len(rows) == 1
        assert rows[0][0] != ""
        assert rows[0][1] != ""
        assert rows[0][2] == ""  # not expired

    def test_get_provenance_filters_expired(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_provenance("a", "claude-code", source_chunk_id="chunk-1")

        # Expire the provenance edge
        store._conn.execute(
            "MATCH (c:Concept {id: 'a'})-[p:HasProvenance]->(ch:Chunk) "
            "SET p.t_expired = '2026-04-11T00:00:00'"
        )

        prov = store.get_provenance("a")
        assert prov == []


class TestWisdomTimestamps:

    def test_wisdom_has_generated_timestamp(self, store):
        wid = store.add_wisdom("Test wisdom", "A description")
        w = store.get_wisdom(wid)
        assert w["created_at"] != ""  # mapped from t_generated


class TestAsOfQueries:

    def test_list_concepts_as_of_excludes_future(self, store):
        """Concepts created after as_of should not appear."""
        store.add_concept("a", "Alpha", "topic", "")
        # Use a past date — should return nothing
        result = store.list_concepts(as_of="2020-01-01")
        assert result == []

    def test_list_concepts_as_of_includes_current(self, store):
        """Concepts created before as_of should appear."""
        store.add_concept("a", "Alpha", "topic", "")
        # Use a future date — concept exists by then
        result = store.list_concepts(as_of="2099-01-01")
        assert len(result) == 1

    def test_get_relationships_as_of_filters_by_valid_time(self, store):
        """as_of respects t_valid_from / t_valid_to, not t_expired."""
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        # Manually set an explicit valid range in the past
        store._conn.execute(
            "MATCH (a:Concept {id: 'a'})-[r:RelatesTo]->(b:Concept {id: 'b'}) "
            "SET r.t_valid_from = '2026-01-01', r.t_valid_to = '2026-02-01'"
        )

        # as_of in the middle of the range: edge present
        rels = store.get_relationships("a", as_of="2026-01-15")
        assert len(rels) == 1

        # as_of before the range: edge absent
        rels = store.get_relationships("a", as_of="2025-12-01")
        assert rels == []

        # as_of after the range: edge absent
        rels = store.get_relationships("a", as_of="2026-03-01")
        assert rels == []

    def test_get_stats_as_of(self, store):
        store.add_concept("a", "A", "topic", "")
        stats_past = store.get_stats(as_of="2020-01-01")
        assert stats_past["total_concepts"] == 0
        stats_future = store.get_stats(as_of="2099-01-01")
        assert stats_future["total_concepts"] == 1


class TestDiff:

    def test_diff_returns_concepts_added(self, store):
        import time
        before = "2020-01-01T00:00:00"
        store.add_concept("a", "A", "topic", "")
        time.sleep(0.01)
        after = "2099-01-01T00:00:00"

        changes = store.diff(before, after)
        assert len(changes["concepts_added"]) == 1
        assert changes["concepts_added"][0]["name"] == "A"

    def test_diff_returns_edges_created(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        changes = store.diff("2020-01-01", "2099-01-01")
        assert len(changes["edges_created"]) == 1
        assert changes["edges_created"][0]["source_id"] == "a"
        assert changes["edges_created"][0]["target_id"] == "b"

    def test_diff_window_excludes_before_and_after(self, store):
        """Events outside [from, to] don't appear in diff."""
        store.add_concept("a", "A", "topic", "")
        # Window entirely in the past: should be empty
        changes = store.diff("2020-01-01", "2020-12-31")
        assert changes["concepts_added"] == []


class TestSupersession:

    def test_supersede_edge_marks_expired(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        result = store.supersede_edge("a", "b", new_edge_ref="new-edge-1",
                                       reason="contradicted by later claim")
        assert result is True

        # Raw edge inspection
        rows = store._query_all(
            "MATCH ()-[r:RelatesTo]->() "
            "RETURN r.t_expired, r.t_valid_to, r.t_superseded_by"
        )
        assert rows[0][0] != ""  # t_expired set
        assert rows[0][1] != ""  # t_valid_to set
        assert rows[0][2] == "new-edge-1"

    def test_supersede_edge_returns_false_if_missing(self, store):
        result = store.supersede_edge("nonexistent", "also-nonexistent")
        assert result is False

    def test_superseded_edge_excluded_from_current_queries(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        store.supersede_edge("a", "b", new_edge_ref="replacement")

        # Default get_relationships should not return superseded edges
        rels = store.get_relationships("a")
        assert rels == []

    def test_superseded_edge_appears_in_diff_expired(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)

        store.supersede_edge("a", "b", new_edge_ref="new-ref")

        changes = store.diff("2020-01-01", "2099-01-01")
        assert len(changes["edges_expired"]) == 1
        assert changes["edges_expired"][0]["superseded_by"] == "new-ref"


class TestExtractorSupersession:
    """Tests that the extractor parses the new `supersedes` field."""

    def test_parser_reads_supersedes_field(self):
        from extended_thinking.processing.extractor import _parse_extraction_response
        response = '''[
          {"name": "Kuzu", "category": "decision", "description": "chose Kuzu",
           "source_quote": "let's use Kuzu", "supersedes": ["SQLite decision"]}
        ]'''
        concepts = _parse_extraction_response(response)
        assert len(concepts) == 1
        assert concepts[0].supersedes == ["SQLite decision"]

    def test_parser_handles_missing_supersedes(self):
        from extended_thinking.processing.extractor import _parse_extraction_response
        response = '''[
          {"name": "Kuzu", "category": "decision", "description": "chose Kuzu",
           "source_quote": "let's use Kuzu"}
        ]'''
        concepts = _parse_extraction_response(response)
        assert concepts[0].supersedes == []

    def test_parser_handles_empty_supersedes(self):
        from extended_thinking.processing.extractor import _parse_extraction_response
        response = '''[
          {"name": "X", "category": "topic", "description": "x",
           "source_quote": "x", "supersedes": []}
        ]'''
        concepts = _parse_extraction_response(response)
        assert concepts[0].supersedes == []

    def test_parser_accepts_string_supersedes(self):
        """Haiku sometimes returns a string instead of a list — handle gracefully."""
        from extended_thinking.processing.extractor import _parse_extraction_response
        response = '''[
          {"name": "X", "category": "topic", "description": "x",
           "source_quote": "x", "supersedes": "Old concept"}
        ]'''
        concepts = _parse_extraction_response(response)
        assert concepts[0].supersedes == ["Old concept"]
