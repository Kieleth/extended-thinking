"""Tests for DMN recombination plugin.

The LLM call is mocked — tests focus on sampling logic, context gathering,
and verdict parsing. Integration with real LLMs happens via MCP tools.
"""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.algorithms import AlgorithmContext
from extended_thinking.algorithms.recombination.cross_cluster_grounded import (
    CrossClusterGroundedRecombination,
    _parse_verdict,
)
from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


@pytest.fixture
def two_cluster_store(store):
    """Build a store with two disconnected clusters (A-B-C and X-Y-Z)."""
    # Cluster 1
    store.add_concept("a", "Alpha", "topic", "first cluster concept")
    store.add_concept("b", "Beta", "theme", "second in first cluster")
    store.add_concept("c", "Gamma", "decision", "third in first cluster")
    store.add_relationship("a", "b", weight=1.0)
    store.add_relationship("b", "c", weight=1.0)

    # Cluster 2 (disconnected)
    store.add_concept("x", "Omega", "topic", "first in second cluster")
    store.add_concept("y", "Psi", "theme", "second in second cluster")
    store.add_concept("z", "Phi", "decision", "third in second cluster")
    store.add_relationship("x", "y", weight=1.0)
    store.add_relationship("y", "z", weight=1.0)

    # Give some concepts provenance so the weighted picker has data
    for cid in ["a", "x"]:
        store.add_provenance(cid, "auto", source_chunk_id=f"chunk-{cid}-1",
                             source=f"/doc-{cid}.md", source_type="note")
        store.add_provenance(cid, "auto", source_chunk_id=f"chunk-{cid}-2",
                             source=f"/doc-{cid}-2.md", source_type="note")

    return store


class TestVerdictParsing:

    def test_parses_grounded_verdict(self):
        response = '''{"verdict": "grounded", "bridge": "A real link",
          "mechanism": "Specific mechanism", "requires": "", "confidence": 0.8}'''
        result = _parse_verdict(response)
        assert result["verdict"] == "grounded"
        assert result["confidence"] == 0.8

    def test_parses_speculative_verdict(self):
        response = '''{"verdict": "speculative", "bridge": "Could connect",
          "mechanism": "", "requires": "A missing layer", "confidence": 0.4}'''
        result = _parse_verdict(response)
        assert result["verdict"] == "speculative"
        assert result["requires"] == "A missing layer"

    def test_parses_no_connection_verdict(self):
        response = '''{"verdict": "no_connection", "bridge": "Distance is principled",
          "mechanism": "", "requires": "", "confidence": 0.9}'''
        result = _parse_verdict(response)
        assert result["verdict"] == "no_connection"

    def test_invalid_verdict_returns_none(self):
        response = '''{"verdict": "totally_made_up", "bridge": "x"}'''
        assert _parse_verdict(response) is None

    def test_handles_markdown_fence(self):
        response = '''```json
        {"verdict": "grounded", "bridge": "x", "mechanism": "y", "requires": "", "confidence": 0.5}
        ```'''
        result = _parse_verdict(response)
        assert result["verdict"] == "grounded"

    def test_garbage_returns_none(self):
        assert _parse_verdict("not JSON at all") is None


class TestCrossClusterSampling:

    def test_empty_graph_returns_empty(self, store):
        alg = CrossClusterGroundedRecombination(random_seed=42)
        result = alg.run(AlgorithmContext(kg=store))
        assert result == []

    def test_single_cluster_returns_empty(self, store):
        """Need at least 2 clusters to recombine across."""
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=1.0)
        # Only one cluster (A-B connected)
        alg = CrossClusterGroundedRecombination(random_seed=42)
        result = alg.run(AlgorithmContext(kg=store))
        assert result == []

    def test_samples_from_different_clusters(self, two_cluster_store):
        """Pairs must come from genuinely different clusters."""
        alg = CrossClusterGroundedRecombination(
            candidates_per_run=5, min_in_degree=1, random_seed=42,
        )
        pairs = alg._sample_cross_cluster_pairs(
            two_cluster_store,
            two_cluster_store.get_graph_overview()["clusters"],
        )
        cluster_1_ids = {"a", "b", "c"}
        cluster_2_ids = {"x", "y", "z"}

        for a, b in pairs:
            in_c1 = a["id"] in cluster_1_ids
            in_c2 = b["id"] in cluster_2_ids
            # Either a∈C1 and b∈C2, or a∈C2 and b∈C1
            assert (in_c1 and in_c2) or (a["id"] in cluster_2_ids and b["id"] in cluster_1_ids)

    def test_pairs_are_unique(self, two_cluster_store):
        alg = CrossClusterGroundedRecombination(
            candidates_per_run=10, random_seed=42,
        )
        pairs = alg._sample_cross_cluster_pairs(
            two_cluster_store,
            two_cluster_store.get_graph_overview()["clusters"],
        )
        pair_keys = [tuple(sorted([a["id"], b["id"]])) for a, b in pairs]
        assert len(pair_keys) == len(set(pair_keys))


class TestRunWithMockLLM:

    def test_runs_with_mock_llm_returning_grounded(self, two_cluster_store):
        """With a mock LLM returning 'grounded', we get a result entry."""
        def mock_llm(prompt: str) -> str:
            return '''{"verdict": "grounded", "bridge": "mocked bridge",
              "mechanism": "mocked mechanism", "requires": "", "confidence": 0.7}'''

        alg = CrossClusterGroundedRecombination(
            candidates_per_run=2, min_in_degree=1, random_seed=42,
        )
        ctx = AlgorithmContext(
            kg=two_cluster_store,
            params={"llm_caller": mock_llm},
        )
        results = alg.run(ctx)
        assert len(results) <= 2
        for r in results:
            assert r["verdict"] == "grounded"
            assert r["bridge"] == "mocked bridge"
            assert "from" in r and "to" in r

    def test_ranks_grounded_above_speculative(self, two_cluster_store):
        """Verdicts are sorted: grounded > speculative > no_connection."""
        call_count = [0]

        def mock_llm(prompt: str) -> str:
            call_count[0] += 1
            if call_count[0] == 1:
                return '''{"verdict": "speculative", "bridge": "s",
                  "mechanism": "", "requires": "x", "confidence": 0.5}'''
            return '''{"verdict": "grounded", "bridge": "g",
              "mechanism": "y", "requires": "", "confidence": 0.5}'''

        alg = CrossClusterGroundedRecombination(
            candidates_per_run=2, min_in_degree=1, random_seed=42,
        )
        ctx = AlgorithmContext(
            kg=two_cluster_store,
            params={"llm_caller": mock_llm},
        )
        results = alg.run(ctx)
        # First result should be grounded (higher priority)
        assert len(results) == 2
        assert results[0]["verdict"] == "grounded"
        assert results[1]["verdict"] == "speculative"

    def test_no_llm_caller_returns_unevaluated(self, two_cluster_store):
        """Without an LLM, the algorithm returns candidate pairs without verdicts."""
        alg = CrossClusterGroundedRecombination(
            candidates_per_run=2, min_in_degree=1, random_seed=42,
        )
        ctx = AlgorithmContext(kg=two_cluster_store)  # no llm_caller
        results = alg.run(ctx)
        assert len(results) > 0
        for r in results:
            assert r["verdict"] == "unevaluated"


class TestContextGathering:

    def test_gather_context_includes_sources(self, two_cluster_store):
        alg = CrossClusterGroundedRecombination(random_seed=42)
        concept = two_cluster_store.get_concept("a")
        ctx = alg._gather_context(two_cluster_store, concept)
        assert len(ctx["sources"]) > 0
        # Should include the source path we added
        assert any("/doc-a" in s for s in ctx["sources"])

    def test_gather_context_includes_neighbors(self, two_cluster_store):
        alg = CrossClusterGroundedRecombination(random_seed=42)
        concept = two_cluster_store.get_concept("b")
        ctx = alg._gather_context(two_cluster_store, concept)
        # Beta connects to Alpha and Gamma
        neighbors = set(ctx["neighbors"])
        assert "Alpha" in neighbors or "Gamma" in neighbors
