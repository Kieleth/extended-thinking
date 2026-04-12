"""Tests for the pluggable algorithm chassis (ADR 003) and built-in plugins."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from extended_thinking.algorithms import (
    Algorithm,
    AlgorithmContext,
    AlgorithmMeta,
    get_active,
    list_available,
    register,
)
from extended_thinking.algorithms.registry import get_by_name
from extended_thinking.algorithms.decay.physarum import PhysarumDecay
from extended_thinking.algorithms.bow_tie.in_out_degree import InOutDegreeBowTie
from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


class TestProtocolBasics:

    def test_physarum_implements_protocol(self):
        alg = PhysarumDecay()
        assert isinstance(alg, Algorithm)
        assert alg.meta.name == "physarum"
        assert alg.meta.family == "decay"
        assert alg.meta.paper_citation  # non-empty citation required
        assert alg.meta.temporal_aware is True

    def test_bow_tie_implements_protocol(self):
        alg = InOutDegreeBowTie()
        assert isinstance(alg, Algorithm)
        assert alg.meta.name == "in_out_degree"
        assert alg.meta.family == "bow_tie"
        assert alg.meta.paper_citation

    def test_all_built_ins_have_citations(self):
        """Every registered algorithm must cite its source research (ADR 003 contract)."""
        for meta in list_available():
            assert meta.paper_citation, f"Algorithm {meta.name} missing paper_citation"


class TestRegistry:

    def test_list_available_returns_built_ins(self):
        metas = list_available()
        names = {m.name for m in metas}
        assert "physarum" in names
        assert "in_out_degree" in names

    def test_filter_by_family(self):
        decay_algs = list_available(family="decay")
        assert all(m.family == "decay" for m in decay_algs)
        bow_tie_algs = list_available(family="bow_tie")
        assert all(m.family == "bow_tie" for m in bow_tie_algs)

    def test_get_active_default_returns_all_in_family(self):
        algs = get_active("decay")
        assert len(algs) >= 1
        assert any(isinstance(a, PhysarumDecay) for a in algs)

    def test_get_active_respects_config(self):
        config = {"algorithms": {"decay": []}}  # explicitly none
        algs = get_active("decay", config)
        assert algs == []

    def test_get_active_applies_parameters(self):
        config = {
            "algorithms": {"decay": ["physarum"]},
            "parameters": {"physarum": {"decay_rate": 0.5}},
        }
        algs = get_active("decay", config)
        assert len(algs) == 1
        assert algs[0].decay_rate == 0.5

    def test_get_by_name(self):
        alg = get_by_name("physarum")
        assert isinstance(alg, PhysarumDecay)
        assert get_by_name("nonexistent") is None

    def test_register_requires_meta(self):
        class BadAlg:
            pass
        with pytest.raises(TypeError):
            register(BadAlg)


class TestPhysarumDecay:

    def test_no_access_returns_base_weight(self):
        alg = PhysarumDecay(decay_rate=0.95)
        w = alg.compute_effective_weight(1.0, last_accessed="")
        assert w == 1.0

    def test_decays_over_time(self):
        alg = PhysarumDecay(decay_rate=0.95)
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        w = alg.compute_effective_weight(1.0, last_accessed=ten_days_ago)
        expected = 1.0 * (0.95 ** 10)
        assert w == pytest.approx(expected, abs=0.01)

    def test_configurable_rate(self):
        alg_fast = PhysarumDecay(decay_rate=0.5)
        alg_slow = PhysarumDecay(decay_rate=0.99)
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        w_fast = alg_fast.compute_effective_weight(1.0, ten_days_ago)
        w_slow = alg_slow.compute_effective_weight(1.0, ten_days_ago)
        assert w_fast < w_slow

    def test_future_timestamp_does_not_amplify(self):
        """Clock skew / bad data shouldn't amplify weight above base."""
        alg = PhysarumDecay(decay_rate=0.95)
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        w = alg.compute_effective_weight(1.0, last_accessed=future)
        assert w <= 1.0  # max(0, days) means future == 0 days == no decay


class TestBowTieAlgorithm:

    def test_empty_graph_returns_empty(self, store):
        alg = InOutDegreeBowTie()
        ctx = AlgorithmContext(kg=store)
        assert alg.run(ctx) == []

    def test_identifies_convergence_point(self, store):
        """A concept with many in-edges AND many out-edges scores highest."""
        # Core concept: many chunks feed it, it feeds many concepts
        store.add_concept("core", "Core Theme", "theme", "")
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_concept("c", "C", "topic", "")

        # Many chunks → core
        for i in range(5):
            store.add_provenance("core", "auto", source_chunk_id=f"chunk-{i}",
                                 source=f"/doc{i}.md", source_type="note")

        # Core → many concepts (out-degree)
        store.add_relationship("core", "a", weight=1.0)
        store.add_relationship("core", "b", weight=1.0)
        store.add_relationship("core", "c", weight=1.0)

        # Peripheral: one chunk, no outgoing edges
        store.add_concept("peripheral", "Peripheral", "topic", "")
        store.add_provenance("peripheral", "auto", source_chunk_id="chunk-peri",
                             source="/doc-peri.md", source_type="note")

        alg = InOutDegreeBowTie(min_in_degree=2, min_out_degree=2)
        result = alg.run(AlgorithmContext(kg=store))

        assert len(result) == 1
        assert result[0]["concept"]["id"] == "core"
        assert result[0]["in_degree"] == 5
        assert result[0]["out_degree"] == 3

    def test_respects_min_thresholds(self, store):
        """Concepts below min_in/out_degree are excluded."""
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_provenance("a", "auto", source_chunk_id="c1",
                             source="/x.md", source_type="note")
        store.add_relationship("a", "b", weight=1.0)

        # a has in_degree=1, out_degree=1. min=2 excludes it.
        alg = InOutDegreeBowTie(min_in_degree=2, min_out_degree=2)
        result = alg.run(AlgorithmContext(kg=store))
        assert result == []

    def test_bow_tie_score_is_geometric_mean(self, store):
        store.add_concept("x", "X", "topic", "")
        store.add_concept("y", "Y", "topic", "")
        store.add_concept("z", "Z", "topic", "")
        store.add_concept("w", "W", "topic", "")

        for i in range(4):
            store.add_provenance("x", "auto", source_chunk_id=f"c-{i}",
                                 source=f"/doc{i}.md", source_type="note")
        store.add_relationship("x", "y", weight=1.0)
        store.add_relationship("x", "z", weight=1.0)
        store.add_relationship("x", "w", weight=1.0)

        alg = InOutDegreeBowTie(min_in_degree=2, min_out_degree=2)
        result = alg.run(AlgorithmContext(kg=store))

        import math
        expected_score = math.sqrt(4 * 3)
        assert result[0]["bow_tie_score"] == pytest.approx(expected_score, abs=0.01)

    def test_top_k_limits_results(self, store):
        # Create many bow-tie candidates
        for i in range(10):
            cid = f"core{i}"
            store.add_concept(cid, f"Core {i}", "theme", "")
            for j in range(3):
                store.add_provenance(cid, "auto", source_chunk_id=f"chunk-{cid}-{j}",
                                     source=f"/{cid}-{j}.md", source_type="note")
            for j in range(3):
                target = f"t{i}-{j}"
                store.add_concept(target, f"T{i}-{j}", "topic", "")
                store.add_relationship(cid, target, weight=1.0)

        alg = InOutDegreeBowTie(top_k=3, min_in_degree=2, min_out_degree=2)
        result = alg.run(AlgorithmContext(kg=store))
        assert len(result) == 3


class TestThirdPartyRegistration:

    def test_can_register_custom_algorithm(self):
        class CustomAlg:
            meta = AlgorithmMeta(
                name="test_custom_xyz",
                family="decay",
                description="Test custom algorithm",
                paper_citation="Test reference 2026",
                parameters={},
            )

            def run(self, context):
                return "custom result"

        register(CustomAlg)
        alg = get_by_name("test_custom_xyz")
        assert alg is not None
        assert alg.run(None) == "custom result"
