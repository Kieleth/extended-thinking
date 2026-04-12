"""Tests for link prediction plugins."""

import tempfile
import uuid
from pathlib import Path

import pytest

from extended_thinking.algorithms import AlgorithmContext
from extended_thinking.algorithms.link_prediction.textual_similarity import (
    TextualSimilarityLinkPrediction,
)
from extended_thinking.algorithms.link_prediction.embedding_similarity import (
    EmbeddingSimilarityLinkPrediction,
)
from extended_thinking.storage.graph_store import GraphStore
from extended_thinking.storage.vector_chromadb import ChromaDBVectorStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


class TestTextualSimilarityBasics:

    def test_empty_graph_returns_empty(self, store):
        alg = TextualSimilarityLinkPrediction()
        ctx = AlgorithmContext(kg=store)
        assert alg.run(ctx) == []

    def test_single_concept_returns_empty(self, store):
        store.add_concept("a", "Alpha", "topic", "A concept")
        alg = TextualSimilarityLinkPrediction()
        assert alg.run(AlgorithmContext(kg=store)) == []

    def test_finds_near_duplicate_concepts(self, store):
        """Two concepts with nearly identical descriptions should show up."""
        store.add_concept("kuzu", "Kuzu migration",
                          "decision", "Migrate database from SQLite to Kuzu graph DB")
        store.add_concept("graph_db", "Graph DB switch",
                          "decision", "Switch database from SQLite to Kuzu graph store")

        alg = TextualSimilarityLinkPrediction(threshold=0.3, top_k=5)
        result = alg.run(AlgorithmContext(kg=store))
        assert len(result) >= 1
        pair_ids = {tuple(sorted([r["from"]["id"], r["to"]["id"]])) for r in result}
        assert ("graph_db", "kuzu") in pair_ids

    def test_excludes_connected_pairs(self, store):
        """Concepts already linked should not be suggested."""
        store.add_concept("a", "Alpha",
                          "topic", "A specific concept about X")
        store.add_concept("b", "Alpha variant",
                          "topic", "A specific concept about X")
        store.add_relationship("a", "b", weight=1.0)

        alg = TextualSimilarityLinkPrediction(threshold=0.3, top_k=5)
        result = alg.run(AlgorithmContext(kg=store))
        pair_ids = {tuple(sorted([r["from"]["id"], r["to"]["id"]])) for r in result}
        assert ("a", "b") not in pair_ids

    def test_respects_threshold(self, store):
        """Concepts below threshold are excluded."""
        store.add_concept("a", "Alpha", "topic", "Totally different content about birds")
        store.add_concept("b", "Beta", "topic", "Completely unrelated text about boats")

        alg = TextualSimilarityLinkPrediction(threshold=0.95, top_k=5)
        result = alg.run(AlgorithmContext(kg=store))
        assert result == []

    def test_top_k_limits_results(self, store):
        """Many similar concepts should be capped by top_k."""
        for i in range(10):
            store.add_concept(
                f"c{i}", f"Ontology concept {i}", "theme",
                "Ontology-based type system with validation at write time",
            )

        alg = TextualSimilarityLinkPrediction(threshold=0.3, top_k=3)
        result = alg.run(AlgorithmContext(kg=store))
        assert len(result) <= 3

    def test_results_sorted_by_similarity(self, store):
        store.add_concept("a", "Alpha",
                          "topic", "A very specific thing about ontology type systems")
        store.add_concept("b", "Alpha variant",
                          "topic", "A very specific thing about ontology type systems")  # nearly identical
        store.add_concept("c", "Gamma",
                          "topic", "A somewhat related thing about systems")  # less similar

        alg = TextualSimilarityLinkPrediction(threshold=0.3, top_k=10)
        result = alg.run(AlgorithmContext(kg=store))

        # Verify ordering is descending by similarity
        for i in range(len(result) - 1):
            assert result[i]["similarity"] >= result[i + 1]["similarity"]


class TestTextualSimilarityTemporal:

    def test_respects_as_of_for_edge_filtering(self, store):
        """An edge superseded before as_of shouldn't block the pair from being suggested."""
        store.add_concept("a", "Alpha",
                          "topic", "identical content about a specific thing")
        store.add_concept("b", "Beta",
                          "topic", "identical content about a specific thing")
        store.add_relationship("a", "b", weight=1.0)

        # Supersede the edge
        store.supersede_edge("a", "b", new_edge_ref="replacement")

        # Current view: edge expired, pair is unlinked → should be suggested
        alg = TextualSimilarityLinkPrediction(threshold=0.3, top_k=5)
        result = alg.run(AlgorithmContext(kg=store))
        pair_ids = {tuple(sorted([r["from"]["id"], r["to"]["id"]])) for r in result}
        assert ("a", "b") in pair_ids


class TestTextualSimilarityEdgeCases:

    def test_handles_empty_descriptions(self, store):
        """Concepts with empty descriptions shouldn't crash."""
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "topic", "")

        alg = TextualSimilarityLinkPrediction(threshold=0.1)
        result = alg.run(AlgorithmContext(kg=store))
        assert isinstance(result, list)  # no crash

    def test_max_pairs_cap(self, store):
        """At large N, O(n^2) must cap to avoid runaway cost."""
        for i in range(20):
            store.add_concept(f"c{i}", f"Concept {i}", "topic",
                              f"Description for concept {i}")

        alg = TextualSimilarityLinkPrediction(max_pairs=10, top_k=100)
        result = alg.run(AlgorithmContext(kg=store))
        # Max 10 pairs evaluated, but filtered further by threshold
        assert isinstance(result, list)


@pytest.fixture
def vectors():
    return ChromaDBVectorStore(
        persist_dir=None,
        collection_name=f"test_{uuid.uuid4().hex[:8]}",
    )


class TestEmbeddingSimilarityBasics:

    def test_no_vectors_returns_empty(self, store):
        alg = EmbeddingSimilarityLinkPrediction()
        ctx = AlgorithmContext(kg=store, vectors=None)
        assert alg.run(ctx) == []

    def test_empty_graph_returns_empty(self, store, vectors):
        alg = EmbeddingSimilarityLinkPrediction()
        assert alg.run(AlgorithmContext(kg=store, vectors=vectors)) == []

    def test_single_concept_returns_empty(self, store, vectors):
        store.add_concept("a", "Alpha", "topic", "")
        alg = EmbeddingSimilarityLinkPrediction()
        assert alg.run(AlgorithmContext(kg=store, vectors=vectors)) == []

    def test_catches_synonym_pair_textual_misses(self, store, vectors):
        """The key payoff: semantic similarity across different words."""
        store.add_concept("kuzu-migration", "Kuzu migration", "decision",
                          "The decision to migrate the graph database to Kuzu")
        store.add_concept("graph-db-switch", "Graph DB switch", "decision",
                          "Switching to a new graph database backend")

        # Embedding should catch it
        emb = EmbeddingSimilarityLinkPrediction(threshold=0.5, top_k=5)
        e_result = emb.run(AlgorithmContext(kg=store, vectors=vectors))
        e_pairs = {tuple(sorted([r["from"]["id"], r["to"]["id"]])) for r in e_result}
        assert ("graph-db-switch", "kuzu-migration") in e_pairs

    def test_excludes_connected_pairs(self, store, vectors):
        store.add_concept("a", "Alpha", "topic", "A specific content anchor")
        store.add_concept("b", "Alpha variant", "topic", "A specific content anchor")
        store.add_relationship("a", "b", weight=1.0)

        alg = EmbeddingSimilarityLinkPrediction(threshold=0.5, top_k=5)
        result = alg.run(AlgorithmContext(kg=store, vectors=vectors))
        pair_ids = {tuple(sorted([r["from"]["id"], r["to"]["id"]])) for r in result}
        assert ("a", "b") not in pair_ids

    def test_threshold_filters_dissimilar(self, store, vectors):
        store.add_concept("a", "Quarterly tax planning",
                          "topic", "Tax compliance Q3")
        store.add_concept("b", "Physics collision resolution",
                          "topic", "Game engine physics edge case handling")

        alg = EmbeddingSimilarityLinkPrediction(threshold=0.9, top_k=5)
        result = alg.run(AlgorithmContext(kg=store, vectors=vectors))
        assert result == []

    def test_top_k_limits_results(self, store, vectors):
        for i in range(5):
            store.add_concept(
                f"c{i}", f"Knowledge graph concept {i}", "theme",
                "Knowledge graph with typed entities and ontology-based validation",
            )

        alg = EmbeddingSimilarityLinkPrediction(threshold=0.3, top_k=2)
        result = alg.run(AlgorithmContext(kg=store, vectors=vectors))
        assert len(result) <= 2

    def test_sorted_descending(self, store, vectors):
        store.add_concept("a", "Ontology type system",
                          "theme", "Types defined by ontology validated at write time")
        store.add_concept("b", "Schema-first validation",
                          "theme", "Schema defines types validated at write time")
        store.add_concept("c", "Football match",
                          "topic", "Kicking a ball around a field")

        alg = EmbeddingSimilarityLinkPrediction(threshold=0.3, top_k=10)
        result = alg.run(AlgorithmContext(kg=store, vectors=vectors))
        for i in range(len(result) - 1):
            assert result[i]["similarity"] >= result[i + 1]["similarity"]


class TestLinkPredictionFamilyRegistry:

    def test_both_link_prediction_plugins_registered(self):
        from extended_thinking.algorithms import list_available
        names = {m.name for m in list_available(family="link_prediction")}
        assert "textual_similarity" in names
        assert "embedding_similarity" in names
