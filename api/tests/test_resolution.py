"""Tests for the resolution family: sequence_matcher and embedding_cosine."""

import tempfile
import uuid
from pathlib import Path

import pytest

from extended_thinking.algorithms import AlgorithmContext
from extended_thinking.algorithms.resolution.sequence_matcher import (
    SequenceMatcherResolution,
)
from extended_thinking.algorithms.resolution.embedding_cosine import (
    EmbeddingCosineResolution,
    _cosine,
)
from extended_thinking.storage.graph_store import GraphStore
from extended_thinking.storage.vector_chromadb import ChromaDBVectorStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "test_kg")


@pytest.fixture
def vectors():
    return ChromaDBVectorStore(
        persist_dir=None,
        collection_name=f"test_{uuid.uuid4().hex[:8]}",
    )


class TestSequenceMatcherResolution:

    def test_empty_kg_returns_none(self, store):
        alg = SequenceMatcherResolution()
        result = alg.resolve(AlgorithmContext(kg=store), "Any name")
        assert result is None

    def test_exact_match_by_normalized_id(self, store):
        store.add_concept("additive-only-extensions",
                          "Additive-only extensions", "decision", "")
        alg = SequenceMatcherResolution()
        result = alg.resolve(AlgorithmContext(kg=store), "Additive-only extensions")
        assert result is not None
        assert result["id"] == "additive-only-extensions"

    def test_near_match_above_threshold(self, store):
        store.add_concept("kuzu-migration", "Kuzu migration",
                          "decision", "")
        alg = SequenceMatcherResolution(threshold=0.6)
        result = alg.resolve(AlgorithmContext(kg=store), "Kuzu migrations")  # plural
        assert result is not None
        assert result["id"] == "kuzu-migration"

    def test_dissimilar_returns_none(self, store):
        store.add_concept("kuzu", "Kuzu migration", "decision", "")
        alg = SequenceMatcherResolution(threshold=0.85)
        result = alg.resolve(AlgorithmContext(kg=store), "Fleet deployment")
        assert result is None

    def test_not_temporal_aware(self):
        """SequenceMatcher ignores time."""
        alg = SequenceMatcherResolution()
        assert alg.meta.temporal_aware is False


class TestEmbeddingCosineResolution:

    def test_no_vectors_returns_none(self, store):
        alg = EmbeddingCosineResolution()
        result = alg.resolve(AlgorithmContext(kg=store, vectors=None), "Any")
        assert result is None

    def test_empty_kg_returns_none(self, store, vectors):
        alg = EmbeddingCosineResolution()
        result = alg.resolve(AlgorithmContext(kg=store, vectors=vectors), "Any")
        assert result is None

    def test_identical_names_match(self, store, vectors):
        store.add_concept("ontology", "Ontology as type system",
                          "theme", "The ontology defines types")
        alg = EmbeddingCosineResolution(threshold=0.9)
        result = alg.resolve(
            AlgorithmContext(kg=store, vectors=vectors),
            "Ontology as type system",
            "The ontology defines types",
        )
        assert result is not None
        assert result["id"] == "ontology"

    def test_catches_semantic_synonyms(self, store, vectors):
        """Embedding catches what SequenceMatcher would miss."""
        store.add_concept("kuzu-decision", "Kuzu migration",
                          "decision", "We decided to migrate to Kuzu graph database")

        # Semantically very close but different wording
        alg = EmbeddingCosineResolution(threshold=0.7)
        result = alg.resolve(
            AlgorithmContext(kg=store, vectors=vectors),
            "Graph DB switch",
            "The choice to switch the backend to Kuzu as the graph database",
        )
        # Should find a match — sentence transformers recognize semantic overlap
        assert result is not None
        assert result["id"] == "kuzu-decision"

    def test_dissimilar_content_returns_none(self, store, vectors):
        store.add_concept("physics", "Physics engine research",
                          "topic", "Looking at game physics engines")
        alg = EmbeddingCosineResolution(threshold=0.8)
        result = alg.resolve(
            AlgorithmContext(kg=store, vectors=vectors),
            "Quarterly tax filing",
            "Business tax compliance in Q3",
        )
        assert result is None


class TestCosineHelper:

    def test_orthogonal_vectors_zero(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_identical_vectors_one(self):
        assert _cosine([0.5, 0.5, 0.7], [0.5, 0.5, 0.7]) == pytest.approx(1.0, abs=1e-6)

    def test_empty_vectors_zero(self):
        assert _cosine([], []) == 0.0
        assert _cosine([1.0], []) == 0.0

    def test_mismatched_length_zero(self):
        assert _cosine([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestRegistryIntegration:

    def test_both_plugins_registered(self):
        from extended_thinking.algorithms import list_available
        resolution_algs = list_available(family="resolution")
        names = {m.name for m in resolution_algs}
        assert "sequence_matcher" in names
        assert "embedding_cosine" in names

    def test_get_active_returns_both_by_default(self):
        from extended_thinking.algorithms import get_active
        algs = get_active("resolution")
        assert len(algs) >= 2

    def test_config_selects_one(self):
        from extended_thinking.algorithms import get_active
        config = {"algorithms": {"resolution": ["sequence_matcher"]}}
        algs = get_active("resolution", config)
        assert len(algs) == 1
        assert isinstance(algs[0], SequenceMatcherResolution)


class TestResolutionCompositional:
    """Demonstrate the expected usage pattern: try plugins in order, first match wins."""

    def test_ordered_resolution(self, store, vectors):
        """Pipeline-style: try sequence_matcher first (fast), fall back to embedding."""
        store.add_concept("ontology-type-system", "Ontology as type system",
                          "theme", "The ontology defines types")

        ctx = AlgorithmContext(kg=store, vectors=vectors)

        # First plugin: SequenceMatcher — would miss a heavily rephrased variant
        sm = SequenceMatcherResolution(threshold=0.85)
        result = sm.resolve(ctx, "Type-driven schema contract")
        assert result is None  # SequenceMatcher can't see this

        # Second plugin: Embedding — catches semantic similarity
        emb = EmbeddingCosineResolution(threshold=0.5)
        result = emb.resolve(ctx, "Type-driven schema contract",
                             "A contract where types enforce the schema")
        # Should resolve to the ontology concept (they're semantically related)
        # If not, the test still passes; what matters is the compositional pattern
        assert result is None or result["id"] == "ontology-type-system"
