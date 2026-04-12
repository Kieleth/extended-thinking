"""Tests for living graph dynamics: spreading activation, Physarum decay, sparse active set."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from extended_thinking.processing.concept_store import ConceptStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = ConceptStore(Path(tmp) / "test.db")
        # Build a small graph: A -- B -- C -- D, plus E isolated
        s.add_concept("a", "Alpha", "topic", "First concept")
        s.add_concept("b", "Beta", "theme", "Second concept")
        s.add_concept("c", "Gamma", "decision", "Third concept")
        s.add_concept("d", "Delta", "topic", "Fourth concept")
        s.add_concept("e", "Epsilon", "topic", "Isolated concept")
        s.add_relationship("a", "b", weight=2.0)
        s.add_relationship("b", "c", weight=1.0)
        s.add_relationship("c", "d", weight=0.5)
        yield s


class TestEffectiveWeight:

    def test_base_weight_without_access(self, store):
        """No access history: returns base weight."""
        w = store.effective_weight("a", "b")
        assert w == 2.0

    def test_weight_decays_after_access(self, store):
        """After access, weight should equal base (no decay yet, just accessed)."""
        store.record_edge_access("a", "b")
        w = store.effective_weight("a", "b")
        # Just accessed = ~0 days ago = base * 0.95^0 = base
        assert w == pytest.approx(2.0, abs=0.1)

    def test_nonexistent_edge_returns_zero(self, store):
        assert store.effective_weight("a", "z") == 0.0

    def test_decay_formula(self, store):
        """Manually set last_accessed to 10 days ago, verify decay."""
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        store._conn.execute(
            "UPDATE relationships SET last_accessed = ? WHERE source_id = ? AND target_id = ?",
            (ten_days_ago, "a", "b"),
        )
        store._conn.commit()
        w = store.effective_weight("a", "b")
        expected = 2.0 * (0.95 ** 10)  # ~1.19
        assert w == pytest.approx(expected, abs=0.05)


class TestSpreadingActivation:

    def test_spreads_from_seed(self, store):
        """Activation spreads from A through the chain."""
        results = store.spread_activation(["a"], depth=3)
        ids = [cid for cid, _ in results]
        # B should be activated (direct neighbor of A)
        assert "b" in ids

    def test_scores_decrease_with_distance(self, store):
        """Closer nodes get higher activation scores."""
        results = store.spread_activation(["a"], depth=3)
        scores = {cid: score for cid, score in results}
        # B is closer to A than C, C closer than D
        if "b" in scores and "c" in scores:
            assert scores["b"] >= scores["c"]
        if "c" in scores and "d" in scores:
            assert scores["c"] >= scores["d"]

    def test_isolated_not_reached(self, store):
        """E has no edges, should not be activated."""
        results = store.spread_activation(["a"], depth=3)
        ids = [cid for cid, _ in results]
        assert "e" not in ids

    def test_seed_excluded_from_results(self, store):
        results = store.spread_activation(["a"], depth=3)
        ids = [cid for cid, _ in results]
        assert "a" not in ids

    def test_budget_limits_results(self, store):
        results = store.spread_activation(["a"], depth=10, budget=3)
        # Budget 3 = seeds (1) + 2 others max
        assert len(results) <= 3

    def test_empty_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = ConceptStore(Path(tmp) / "test.db")
            s.add_concept("x", "X", "topic", "")
            results = s.spread_activation(["x"], depth=3)
            assert results == []

    def test_multiple_seeds(self, store):
        """Spreading from both ends of the chain."""
        results = store.spread_activation(["a", "d"], depth=2)
        ids = [cid for cid, _ in results]
        # B and C should both be activated
        assert "b" in ids
        assert "c" in ids


class TestActiveNodes:

    def test_returns_k_nodes(self, store):
        active = store.active_nodes(k=3)
        assert len(active) <= 3

    def test_accessed_nodes_rank_higher(self, store):
        """Nodes that have been accessed should rank higher."""
        store.record_access("a")
        store.record_access("a")
        store.record_access("a")
        active = store.active_nodes(k=5)
        names = [c["name"] for c in active]
        # Alpha (accessed 3x) should be first
        assert names[0] == "Alpha"

    def test_connected_nodes_rank_higher_than_isolated(self, store):
        """Connected nodes (higher degree) rank above isolated ones."""
        active = store.active_nodes(k=5)
        names = [c["name"] for c in active]
        # Epsilon (isolated, degree 0) should be last
        assert names[-1] == "Epsilon"

    def test_returns_dicts(self, store):
        active = store.active_nodes(k=2)
        assert all(isinstance(c, dict) for c in active)
        assert all("name" in c for c in active)
