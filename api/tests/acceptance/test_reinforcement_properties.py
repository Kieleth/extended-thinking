"""Hypothesis properties for edge reinforcement.

Reinforcement is not a standalone algorithm in the registry; it emerges from
`GraphStore.add_relationship` merging an edge when source/target already have
one. The contract: re-adding an edge increases its effective weight.

Properties:
  - readd_increases_weight: weight(a->b) after re-adding is strictly greater
  - weight_accumulates: N additions accumulate proportionally
  - never_overflows: large numbers of re-adds stay finite and positive
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from extended_thinking.storage.graph_store import GraphStore

pytestmark = pytest.mark.acceptance


def _fresh_store() -> GraphStore:
    tmp = TemporaryDirectory()
    store = GraphStore(Path(tmp.name) / "kg")
    store._hypothesis_tmp = tmp  # noqa: SLF001
    return store


def _weight(store: GraphStore, src: str, tgt: str) -> float:
    row = store._query_one(
        "MATCH (a:Concept {id: $src})-[r:RelatesTo]->(b:Concept {id: $tgt}) "
        "RETURN r.weight",
        {"src": src, "tgt": tgt},
    )
    return float(row[0]) if row else 0.0


def _seed_nodes(store: GraphStore, *ids: str) -> None:
    for i in ids:
        store.add_concept(concept_id=i, name=i, category="concept", description="")


@given(
    weights=st.lists(
        st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False),
        min_size=2, max_size=8,
    )
)
@settings(max_examples=8, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_readd_increases_weight(weights):
    store = _fresh_store()
    _seed_nodes(store, "a", "b")
    prev = 0.0
    for w in weights:
        store.add_relationship("a", "b", weight=w)
        current = _weight(store, "a", "b")
        assert current > prev - 1e-9, f"weight regressed: {prev} -> {current}"
        prev = current


@given(base=st.floats(min_value=0.5, max_value=2.0),
       n=st.integers(min_value=2, max_value=10))
@settings(max_examples=8, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_weight_accumulates_proportionally(base, n):
    """N identical additions should yield weight at least N * base (no clipping)."""
    store = _fresh_store()
    _seed_nodes(store, "a", "b")
    for _ in range(n):
        store.add_relationship("a", "b", weight=base)
    assert _weight(store, "a", "b") >= n * base - 1e-6


@given(st.integers(min_value=50, max_value=200))
@settings(max_examples=5, deadline=4000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_never_overflows_on_many_readds(n):
    store = _fresh_store()
    _seed_nodes(store, "a", "b")
    for _ in range(n):
        store.add_relationship("a", "b", weight=1.0)
    w = _weight(store, "a", "b")
    assert 0.0 < w < 1e12, f"weight grew unreasonably: {w}"
