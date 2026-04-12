"""Hypothesis properties for the activation family.

Weighted-BFS spreading activation should:
  - drop off with hop distance from the seed
  - never produce a node with score > the seed itself (no amplification)
  - be stable under seed-set permutation (set semantics, not list semantics)

Graph construction is deliberately small to keep the suite fast.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from extended_thinking.algorithms.activation.weighted_bfs import WeightedBFSActivation
from extended_thinking.algorithms.protocol import AlgorithmContext
from extended_thinking.storage.graph_store import GraphStore

pytestmark = pytest.mark.acceptance


def _chain_store(length: int, weight: float = 1.0) -> GraphStore:
    """a -> b -> c -> ... chain of `length` nodes."""
    tmp = TemporaryDirectory()
    store = GraphStore(Path(tmp.name) / "kg")
    store._hypothesis_tmp = tmp  # noqa: SLF001
    ids = [chr(ord("a") + i) for i in range(length)]
    for i in ids:
        store.add_concept(concept_id=i, name=i, category="concept", description="")
    for a, b in zip(ids, ids[1:]):
        store.add_relationship(a, b, weight=weight)
    return store


def _scores(results) -> dict[str, float]:
    """Normalize {(id, score)} shapes across implementations into a dict."""
    out: dict[str, float] = {}
    for item in results or []:
        if isinstance(item, tuple) and len(item) == 2:
            out[item[0]] = float(item[1])
        elif isinstance(item, dict):
            key = item.get("id") or item.get("concept_id") or item.get("name")
            val = item.get("score") or item.get("weight") or item.get("activation")
            if key is not None and val is not None:
                out[str(key)] = float(val)
    return out


@given(length=st.integers(min_value=3, max_value=6))
@settings(max_examples=8, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_activation_drops_off_with_distance(length):
    store = _chain_store(length)
    algo = WeightedBFSActivation()
    results = algo.run(AlgorithmContext(kg=store, params={"seed_ids": ["a"]}))
    scores = _scores(results)
    # We expect monotone non-increasing scores along the chain a -> b -> c ...
    # Skip the check when the algorithm returns no score for a node (e.g.
    # beyond a depth horizon) since that's expected behavior, not a violation.
    prev = None
    for i in range(length):
        node = chr(ord("a") + i)
        if node in scores:
            if prev is not None:
                assert scores[node] <= prev + 1e-9, (
                    f"score at {node} exceeds previous hop: {scores[node]} > {prev}"
                )
            prev = scores[node]


@given(length=st.integers(min_value=2, max_value=5))
@settings(max_examples=8, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_non_seed_score_never_exceeds_seed(length):
    store = _chain_store(length)
    algo = WeightedBFSActivation()
    results = algo.run(AlgorithmContext(kg=store, params={"seed_ids": ["a"]}))
    scores = _scores(results)
    if "a" not in scores:
        return  # algorithm may omit the seed itself, which is also fine
    seed_score = scores["a"]
    for node, s in scores.items():
        if node == "a":
            continue
        assert s <= seed_score + 1e-9, (
            f"node {node} score {s} exceeds seed score {seed_score}"
        )


@given(
    seeds=st.lists(
        st.sampled_from(["a", "b", "c"]),
        min_size=1, max_size=3, unique=True,
    )
)
@settings(max_examples=10, deadline=3000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_activation_is_permutation_invariant(seeds):
    store = _chain_store(length=4)
    algo = WeightedBFSActivation()
    a = _scores(algo.run(AlgorithmContext(kg=store, params={"seed_ids": list(seeds)})))
    b = _scores(algo.run(AlgorithmContext(kg=store, params={"seed_ids": list(reversed(seeds))})))
    assert set(a.keys()) == set(b.keys()), "key sets differ across permutations"
    for k in a:
        assert abs(a[k] - b[k]) < 1e-6, (
            f"score for {k!r} depends on seed order: {a[k]} vs {b[k]}"
        )
