"""Temporal weighting: old chunks produce old edges and decay accordingly.

Covers A (pipeline wires chunk.timestamp → edge t_valid_from), B (activity_score
plugin), C (Physarum source-age-aware decay), D (extractor source_created_at).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from extended_thinking.algorithms import AlgorithmContext, get_by_name
from extended_thinking.algorithms.decay.physarum import PhysarumDecay
from extended_thinking.processing.extractor import (
    ExtractedConcept,
    _parse_extraction_response,
)
from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


# ── A: edge t_valid_from honors caller's timestamp ────────────────────

class TestEdgeValidFromHonorsCaller:

    def test_explicit_valid_from_is_stored(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        old_ts = "2020-01-01T00:00:00+00:00"
        store.add_relationship("a", "b", weight=1.0, context="", t_valid_from=old_ts)
        rels = store.get_relationships("a")
        assert len(rels) == 1
        assert rels[0]["valid_from"] == old_ts

    def test_missing_valid_from_defaults_to_now(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0, context="")
        rels = store.get_relationships("a")
        assert rels[0]["valid_from"]  # non-empty
        # roughly now
        parsed = datetime.fromisoformat(rels[0]["valid_from"])
        delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        assert delta < 5

    def test_valid_from_is_separate_from_created(self, store):
        """t_valid_from = world-time; t_created = transaction time."""
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        old_ts = "2019-06-15T12:00:00+00:00"
        store.add_relationship("a", "b", weight=1.0, t_valid_from=old_ts)
        # t_created stored separately; query directly
        rows = store._query_all(
            "MATCH (a:Concept {id: 'a'})-[r:RelatesTo]->(b:Concept {id: 'b'}) "
            "RETURN r.t_valid_from, r.t_created"
        )
        assert rows[0][0] == old_ts
        assert rows[0][1] != old_ts  # t_created is now, not 2019


# ── B: activity_score/recency_weighted plugin ─────────────────────────

class TestRecencyWeightedActivity:

    def test_plugin_registered(self):
        alg = get_by_name("recency_weighted")
        assert alg is not None
        assert alg.meta.family == "activity_score"

    def test_top_k_returns_scored_concepts(self, store):
        now = datetime.now(timezone.utc)
        store.add_concept("fresh", "Fresh", "topic", "")
        store.add_concept("stale", "Stale", "topic", "")
        # Access both but connect only fresh to boost its degree
        store.add_concept("neighbor", "N", "topic", "")
        store.add_relationship("fresh", "neighbor", weight=2.0)
        # Touch last_accessed
        store.record_access("fresh")
        old = (now - timedelta(days=30)).isoformat()
        store._conn.execute(
            "MATCH (c:Concept {id: 'stale'}) SET c.last_accessed = $ts",
            parameters={"ts": old},
        )

        alg = get_by_name("recency_weighted")
        result = alg.run(AlgorithmContext(kg=store, params={"top_k": 5}))
        ids = [c["id"] for c in result]
        # fresh should outrank stale (more recent + connected)
        assert "fresh" in ids and "stale" in ids
        assert ids.index("fresh") < ids.index("stale")

    def test_effective_degree_demotes_stale_source_edges(self, store):
        """Node ranking must reflect edge t_valid_from: a concept connected
        only to old-evidence edges ranks below one with fresh-evidence edges
        even when every other signal is equal."""
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=400)).isoformat()
        # All concepts: same freq, same last_seen (ingest time ~now).
        # Difference is purely in their edges' t_valid_from.
        for cid in ("fresh_hub", "fresh_leaf", "stale_hub", "stale_leaf"):
            store.add_concept(cid, cid, "topic", "")
        # Fresh pair: edge valid_from = now (default)
        store.add_relationship("fresh_hub", "fresh_leaf", weight=2.0)
        # Stale pair: edge valid_from = 400 days ago
        store.add_relationship("stale_hub", "stale_leaf", weight=2.0,
                               t_valid_from=old)

        alg = get_by_name("recency_weighted")
        result = alg.run(AlgorithmContext(kg=store, params={"top_k": 10}))
        ids = [c["id"] for c in result]
        # Both fresh concepts must rank above both stale concepts
        fresh_positions = [ids.index("fresh_hub"), ids.index("fresh_leaf")]
        stale_positions = [ids.index("stale_hub"), ids.index("stale_leaf")]
        assert max(fresh_positions) < min(stale_positions), (
            f"stale-edge concepts should rank below fresh-edge concepts, got {ids}"
        )

    def test_active_nodes_delegates_to_plugin(self, store):
        """GraphStore.active_nodes should return what the plugin returns."""
        store.add_concept("a", "A", "topic", "")
        store.record_access("a")
        result = store.active_nodes(k=5)
        assert any(c["id"] == "a" for c in result)


# ── C: Physarum source-age-aware decay ─────────────────────────────────

class TestPhysarumSourceAgeAware:

    def test_access_only_mode_ignores_source_age(self):
        decay = PhysarumDecay(decay_rate=0.5, source_age_aware=False)
        now = datetime.now(timezone.utc)
        last_access = now.isoformat()
        # Old source, but just accessed
        old_source = (now - timedelta(days=10)).isoformat()
        w = decay.compute_effective_weight(
            base_weight=1.0,
            last_accessed=last_access,
            t_valid_from=old_source,
            now=now,
        )
        # access_days ~ 0 → no decay
        assert w == pytest.approx(1.0, abs=0.01)

    def test_source_age_aware_mode_decays_old_evidence(self):
        decay = PhysarumDecay(decay_rate=0.5, source_age_aware=True)
        now = datetime.now(timezone.utc)
        last_access = now.isoformat()          # just touched
        old_source = (now - timedelta(days=10)).isoformat()
        w = decay.compute_effective_weight(
            base_weight=1.0,
            last_accessed=last_access,
            t_valid_from=old_source,
            now=now,
        )
        # 10 days * 0.5^day = 1 / 1024
        assert w == pytest.approx(0.5 ** 10, abs=1e-4)

    def test_source_age_aware_with_old_access_uses_oldest_signal(self):
        decay = PhysarumDecay(decay_rate=0.5, source_age_aware=True)
        now = datetime.now(timezone.utc)
        old_access = (now - timedelta(days=3)).isoformat()
        older_source = (now - timedelta(days=8)).isoformat()
        w = decay.compute_effective_weight(
            base_weight=1.0,
            last_accessed=old_access,
            t_valid_from=older_source,
            now=now,
        )
        # Should use max(3, 8) = 8 days
        assert w == pytest.approx(0.5 ** 8, abs=1e-4)

    def test_missing_timestamps_returns_base(self):
        decay = PhysarumDecay(decay_rate=0.5, source_age_aware=True)
        w = decay.compute_effective_weight(1.0, last_accessed="", t_valid_from="")
        assert w == 1.0

    def test_graph_store_effective_weight_uses_source_age(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=20)).isoformat()
        store.add_relationship("a", "b", weight=1.0, t_valid_from=old)
        # Fresh access after sync
        store.record_edge_access("a", "b")

        w_aware = store.effective_weight("a", "b", decay_rate=0.9, source_age_aware=True)
        w_unaware = store.effective_weight("a", "b", decay_rate=0.9, source_age_aware=False)
        assert w_aware < w_unaware  # source-age-aware should decay more


# ── D: extractor source_created_at field ───────────────────────────────

class TestExtractorSourceCreatedAt:

    def test_parses_iso_date(self):
        response = """[
          {"name": "Test", "category": "topic", "description": "d",
           "source_quote": "q", "supersedes": [],
           "source_created_at": "2024-06-12"}
        ]"""
        concepts = _parse_extraction_response(response)
        assert len(concepts) == 1
        assert concepts[0].source_created_at.startswith("2024-06-12")

    def test_parses_full_iso_timestamp(self):
        response = """[
          {"name": "Test", "category": "topic", "description": "d",
           "source_quote": "q", "source_created_at": "2024-06-12T14:30:00+00:00"}
        ]"""
        concepts = _parse_extraction_response(response)
        assert concepts[0].source_created_at == "2024-06-12T14:30:00+00:00"

    def test_drops_invalid_date_strings(self):
        response = """[
          {"name": "Test", "category": "topic", "description": "d",
           "source_quote": "q", "source_created_at": "Q1 2024"}
        ]"""
        concepts = _parse_extraction_response(response)
        assert concepts[0].source_created_at == ""

    def test_missing_field_defaults_empty(self):
        response = """[
          {"name": "Test", "category": "topic", "description": "d",
           "source_quote": "q"}
        ]"""
        concepts = _parse_extraction_response(response)
        assert concepts[0].source_created_at == ""

    def test_dataclass_has_field(self):
        c = ExtractedConcept(name="x", category="topic", description="")
        assert c.source_created_at == ""


# ── Integration: pipeline threads old timestamps through ──────────────

class TestPipelineTemporalIntegration:

    def test_old_chunk_timestamp_becomes_edge_valid_from(self, store, monkeypatch):
        """The full path: chunk.timestamp → _detect_relationships →
        add_relationship(t_valid_from=...) → stored in Kuzu."""
        from extended_thinking.processing.pipeline_v2 import Pipeline
        from extended_thinking.providers.protocol import MemoryChunk

        class FakeProvider:
            name = "fake"
            def get_recent(self, since=None, limit=50):
                return []
            def get_stats(self):
                return {"total_memories": 0}

        p = Pipeline(FakeProvider(), store)

        old_ts = "2021-03-15T09:00:00+00:00"
        chunks = [MemoryChunk(
            id="c1", content="the quick brown fox jumps",
            source="old.md", timestamp=old_ts,
            metadata={"provider": "folder"},
        )]
        concepts = [
            ExtractedConcept(name="Quick", category="topic",
                             description="q", source_quote="the quick brown fox"),
            ExtractedConcept(name="Fox", category="entity",
                             description="f", source_quote="brown fox jumps"),
        ]
        store.add_concept("quick", "Quick", "topic", "")
        store.add_concept("fox", "Fox", "entity", "")

        p._detect_relationships(chunks, concepts)

        rels = store.get_relationships("quick")
        assert len(rels) >= 1
        assert rels[0]["valid_from"] == old_ts

    def test_extractor_inline_date_overrides_chunk_timestamp(self, store):
        """If extractor returns source_created_at, edge uses it (older)."""
        from extended_thinking.processing.pipeline_v2 import Pipeline
        from extended_thinking.providers.protocol import MemoryChunk

        class FakeProvider:
            name = "fake"
            def get_recent(self, since=None, limit=50):
                return []
            def get_stats(self):
                return {"total_memories": 0}

        p = Pipeline(FakeProvider(), store)

        chunk_ts = "2024-05-01T00:00:00+00:00"
        inline_ts = "2019-07-04T00:00:00+00:00"
        chunks = [MemoryChunk(
            id="c1", content="dated journal entry body",
            source="journal.md", timestamp=chunk_ts,
        )]
        concepts = [
            ExtractedConcept(name="Alpha", category="topic",
                             description="a", source_quote="dated journal",
                             source_created_at=inline_ts),
            ExtractedConcept(name="Beta", category="topic",
                             description="b", source_quote="entry body",
                             source_created_at=inline_ts),
        ]
        store.add_concept("alpha", "Alpha", "topic", "")
        store.add_concept("beta", "Beta", "topic", "")

        p._detect_relationships(chunks, concepts)

        rels = store.get_relationships("alpha")
        assert rels[0]["valid_from"] == inline_ts  # older of the two wins
