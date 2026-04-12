"""Tests for the bridges/ family — rich-club hub detection."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.algorithms import AlgorithmContext
from extended_thinking.algorithms.bridges.top_percentile import TopPercentileBridges
from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


@pytest.fixture
def hub_store(store):
    """A 'hub' concept connected to many peripherals."""
    store.add_concept("hub", "Hub", "theme", "the hub")
    for i in range(10):
        store.add_concept(f"p{i}", f"Peripheral {i}", "topic", f"peripheral {i}")
        store.add_relationship("hub", f"p{i}", weight=1.0)
    # Isolated triangle, not a bridge
    store.add_concept("t1", "T1", "topic", "")
    store.add_concept("t2", "T2", "topic", "")
    store.add_concept("t3", "T3", "topic", "")
    store.add_relationship("t1", "t2", weight=1.0)
    store.add_relationship("t2", "t3", weight=1.0)
    return store


class TestTopPercentileBasics:

    def test_empty_graph_returns_empty(self, store):
        alg = TopPercentileBridges()
        assert alg.run(AlgorithmContext(kg=store)) == []

    def test_no_edges_returns_empty(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        alg = TopPercentileBridges()
        assert alg.run(AlgorithmContext(kg=store)) == []

    def test_identifies_hub_as_bridge(self, hub_store):
        """The hub connected to 10 peripherals should be the top bridge."""
        alg = TopPercentileBridges(percentile=0.1, min_degree=5)
        results = alg.run(AlgorithmContext(kg=hub_store))
        assert len(results) >= 1
        assert results[0]["concept"]["id"] == "hub"
        assert results[0]["degree"] == 10

    def test_min_degree_floor_excludes_low_degree(self, hub_store):
        """With min_degree=5, triangle nodes (degree 1-2) are excluded."""
        alg = TopPercentileBridges(percentile=0.5, min_degree=5)
        results = alg.run(AlgorithmContext(kg=hub_store))
        ids = {r["concept"]["id"] for r in results}
        assert "t1" not in ids
        assert "t2" not in ids

    def test_sorted_by_degree_descending(self, store):
        # Build a graph with varying degrees
        store.add_concept("big", "Big hub", "theme", "")
        store.add_concept("medium", "Medium hub", "theme", "")
        store.add_concept("small", "Small hub", "theme", "")
        for i in range(8):
            store.add_concept(f"big_p{i}", "peripheral", "topic", "")
            store.add_relationship("big", f"big_p{i}", weight=1.0)
        for i in range(5):
            store.add_concept(f"med_p{i}", "peripheral", "topic", "")
            store.add_relationship("medium", f"med_p{i}", weight=1.0)
        for i in range(3):
            store.add_concept(f"small_p{i}", "peripheral", "topic", "")
            store.add_relationship("small", f"small_p{i}", weight=1.0)

        alg = TopPercentileBridges(percentile=0.25, min_degree=3)
        results = alg.run(AlgorithmContext(kg=store))
        # All 3 hubs should be in results, sorted big > medium > small
        degrees = [r["degree"] for r in results]
        assert degrees == sorted(degrees, reverse=True)


class TestTopPercentileParameters:

    def test_percentile_configurable(self, hub_store):
        """Lower percentile = fewer bridges."""
        strict = TopPercentileBridges(percentile=0.05, min_degree=1)
        loose = TopPercentileBridges(percentile=0.50, min_degree=1)
        strict_results = strict.run(AlgorithmContext(kg=hub_store))
        loose_results = loose.run(AlgorithmContext(kg=hub_store))
        assert len(loose_results) >= len(strict_results)

    def test_min_degree_configurable(self, hub_store):
        """Higher min_degree = fewer bridges."""
        permissive = TopPercentileBridges(percentile=0.5, min_degree=1)
        strict = TopPercentileBridges(percentile=0.5, min_degree=10)
        p_results = permissive.run(AlgorithmContext(kg=hub_store))
        s_results = strict.run(AlgorithmContext(kg=hub_store))
        assert len(p_results) >= len(s_results)


class TestRegistryIntegration:

    def test_registered(self):
        from extended_thinking.algorithms import list_available
        names = {m.name for m in list_available(family="bridges")}
        assert "top_percentile" in names

    def test_graph_overview_uses_plugin(self, hub_store):
        """GraphStore.get_graph_overview() delegates to the plugin."""
        overview = hub_store.get_graph_overview()
        bridge_ids = {b["id"] for b in overview.get("bridges", [])}
        assert "hub" in bridge_ids

    def test_config_swaps_plugin(self):
        """Config can disable bridges entirely."""
        from extended_thinking.algorithms import get_active
        config = {"algorithms": {"bridges": []}}
        algs = get_active("bridges", config)
        assert algs == []
