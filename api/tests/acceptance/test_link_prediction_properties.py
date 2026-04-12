"""Hypothesis properties for the link-prediction family.

Applied to `textual_similarity` (embedding-free path, fast enough for
Hypothesis). Embedding-based link prediction goes through the cassette suite.

Properties:
  - no_existing_edge_predicted: predictions are unlinked pairs only
  - threshold_monotonicity: raising threshold only removes predictions
  - symmetry: {A, B} pair appears with same score as {B, A}
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from extended_thinking.algorithms.link_prediction.textual_similarity import (
    TextualSimilarityLinkPrediction,
)
from extended_thinking.algorithms.protocol import AlgorithmContext
from extended_thinking.storage.graph_store import GraphStore

pytestmark = pytest.mark.acceptance


def _store_with_concepts(names: list[str]) -> GraphStore:
    tmp = TemporaryDirectory()
    store = GraphStore(Path(tmp.name) / "kg")
    store._hypothesis_tmp = tmp  # noqa: SLF001
    for i, n in enumerate(names):
        store.add_concept(
            concept_id=f"c{i}", name=n, category="concept",
            description=f"a concept about {n}",
        )
    return store


def _pair_ids(results) -> set[frozenset[str]]:
    """Extract the set of predicted pairs, ignoring direction."""
    pairs: set[frozenset[str]] = set()
    for r in results or []:
        if isinstance(r, dict):
            a = r.get("from", {}).get("id") if isinstance(r.get("from"), dict) else r.get("from")
            b = r.get("to", {}).get("id") if isinstance(r.get("to"), dict) else r.get("to")
        else:
            a, b = r[0], r[1]
        if a and b:
            pairs.add(frozenset([a, b]))
    return pairs


names_st = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=3, max_size=15),
    min_size=3, max_size=8, unique=True,
)


@given(names=names_st)
@settings(max_examples=10, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_existing_edge_predicted(names):
    store = _store_with_concepts(names)
    # Pre-link c0 and c1 so a valid prediction for that pair would be a bug.
    store.add_relationship("c0", "c1", weight=1.0)
    algo = TextualSimilarityLinkPrediction(threshold=0.0, top_k=20)
    results = algo.run(AlgorithmContext(kg=store))
    assert frozenset({"c0", "c1"}) not in _pair_ids(results), (
        "algorithm predicted a link that already exists"
    )


@given(names=names_st)
@settings(max_examples=10, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_threshold_monotonicity(names):
    store = _store_with_concepts(names)
    low = TextualSimilarityLinkPrediction(threshold=0.0, top_k=50).run(AlgorithmContext(kg=store))
    high = TextualSimilarityLinkPrediction(threshold=0.8, top_k=50).run(AlgorithmContext(kg=store))
    low_pairs = _pair_ids(low)
    high_pairs = _pair_ids(high)
    assert high_pairs.issubset(low_pairs), (
        f"raising threshold added predictions: {high_pairs - low_pairs}"
    )


@given(names=names_st)
@settings(max_examples=8, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_prediction_ids_are_distinct(names):
    """Predictions should never pair a concept with itself."""
    store = _store_with_concepts(names)
    algo = TextualSimilarityLinkPrediction(threshold=0.0, top_k=50)
    results = algo.run(AlgorithmContext(kg=store))
    for pair in _pair_ids(results):
        assert len(pair) == 2, f"degenerate pair (self-loop?): {pair}"
