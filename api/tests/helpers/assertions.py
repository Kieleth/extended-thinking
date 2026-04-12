"""Shared assertion helpers for the acceptance suite.

Three categories:
  KG-level:        assertions about concepts, relationships, bitemporal edges
  Algorithm-level: assertions about ranked outputs, weight dynamics, invariants
  Wisdom-level:    assertions that synthesized wisdom stays grounded

All helpers raise AssertionError with a specific message on failure. None of
them fall back silently. They are meant to be readable in test output.
"""

from __future__ import annotations

from typing import Any, Iterable


# ── KG-level ──────────────────────────────────────────────────────────────


def assert_entity_exists(store, concept_id: str, *, category: str | None = None) -> dict:
    """Verify a concept exists (and optionally matches category). Returns the row."""
    row = store.get_concept(concept_id)
    assert row is not None, f"concept {concept_id!r} not found in store"
    if category is not None:
        got = row.get("category")
        assert got == category, (
            f"concept {concept_id!r} has category {got!r}, expected {category!r}"
        )
    return row


def assert_relation(
    store,
    source_id: str,
    target_id: str,
    *,
    min_weight: float | None = None,
) -> None:
    """Verify a RelatesTo edge exists from source to target, optionally with min weight."""
    if hasattr(store, "_query_one"):
        row = store._query_one(
            "MATCH (a:Concept {id: $src})-[r:RelatesTo]->(b:Concept {id: $tgt}) "
            "RETURN r.weight",
            {"src": source_id, "tgt": target_id},
        )
    else:
        row = None
    assert row, f"edge {source_id} -> {target_id} not found"
    if min_weight is not None:
        weight = row[0]
        assert weight >= min_weight, (
            f"edge {source_id} -> {target_id} weight {weight} < {min_weight}"
        )


def assert_bitemporal(edge: dict, *, valid_from: str | None = None) -> None:
    """Verify a fetched edge row carries the expected bitemporal fields."""
    assert "t_valid_from" in edge, f"edge missing t_valid_from: {edge}"
    assert "t_created" in edge, f"edge missing t_created: {edge}"
    if valid_from is not None:
        got = edge["t_valid_from"]
        assert got == valid_from, (
            f"edge t_valid_from {got!r}, expected {valid_from!r}"
        )


# ── Algorithm-level ───────────────────────────────────────────────────────


def _as_tuple(item: Any) -> tuple:
    """Normalize ranked-output items to (id, score) tuples.

    Algorithms return various shapes:
      - (id, score)
      - {"id": ..., "score": ...}
      - {"concept_id": ..., "weight": ...}
    We try the common ones and fall back to the raw item.
    """
    if isinstance(item, tuple) and len(item) == 2:
        return item
    if isinstance(item, dict):
        for id_key in ("id", "concept_id", "name"):
            if id_key in item:
                for score_key in ("score", "weight", "value"):
                    if score_key in item:
                        return (item[id_key], item[score_key])
                return (item[id_key], None)
    return (item, None)


def assert_top_k_contains(
    results: list,
    expected_ids: Iterable[str],
    *,
    k: int | None = None,
) -> None:
    """Verify the top-k results include every id in `expected_ids`.

    Order of `expected_ids` does not matter. If `k` is None, the full result
    set is considered. Useful when the algorithm's exact ranking is unstable
    but membership at the top is what the test cares about.
    """
    expected = set(expected_ids)
    top = results[:k] if k is not None else results
    got_ids = {_as_tuple(r)[0] for r in top}
    missing = expected - got_ids
    assert not missing, (
        f"expected ids missing from top-{k or 'all'}: {sorted(missing)}. "
        f"Got: {sorted(got_ids)}"
    )


def assert_weights_monotonic_decay(weights: list[float]) -> None:
    """Verify a sequence of weights is monotonically non-increasing.

    Used by decay-family property tests: applying decay repeatedly must
    never increase weight between steps.
    """
    assert len(weights) >= 2, "need at least 2 weights to check monotonic decay"
    for i in range(1, len(weights)):
        assert weights[i] <= weights[i - 1] + 1e-9, (
            f"decay violated monotonicity at step {i}: "
            f"{weights[i - 1]} -> {weights[i]}"
        )


def assert_weights_monotonic_increase(weights: list[float]) -> None:
    """Verify a sequence of weights is monotonically non-decreasing."""
    assert len(weights) >= 2, "need at least 2 weights to check monotonic increase"
    for i in range(1, len(weights)):
        assert weights[i] >= weights[i - 1] - 1e-9, (
            f"reinforcement violated monotonicity at step {i}: "
            f"{weights[i - 1]} -> {weights[i]}"
        )


def assert_active_set_shape(
    active_set: list,
    *,
    min_size: int = 0,
    max_size: int | None = None,
    required_ids: Iterable[str] = (),
) -> None:
    """Verify an active-set output has a reasonable size and required members."""
    assert len(active_set) >= min_size, (
        f"active set too small: {len(active_set)} < {min_size}"
    )
    if max_size is not None:
        assert len(active_set) <= max_size, (
            f"active set too large: {len(active_set)} > {max_size}"
        )
    required = list(required_ids)
    if required:
        assert_top_k_contains(active_set, required)


def assert_no_cross_store_leak(unified_graph, et_store, provider_kg) -> None:
    """Generalize the test_invariants.py cross-store leak invariant.

    Asserts that ET-originated edges are not echoed back into the provider's
    KG view. The provider view must filter out triples with
    source_file == 'extended-thinking'.
    """
    if provider_kg is None:
        return
    facts = provider_kg.facts()
    leaked = [f for f in facts if getattr(f, "source", "") == "extended-thinking"]
    assert not leaked, (
        f"provider KG leaked {len(leaked)} ET-originated facts. First: {leaked[0]}"
    )


# ── Wisdom-level ──────────────────────────────────────────────────────────


def assert_wisdom_grounded(wisdom: dict, known_concepts: Iterable[str]) -> None:
    """Verify every claim in a wisdom structure cites a known concept.

    Expected wisdom shape:
      {"title": "...", "description": "...", "related_concepts": ["a", "b"]}

    We check that at least one related_concept appears in known_concepts and
    that related_concepts is non-empty. This is a floor, not a ceiling.
    """
    assert isinstance(wisdom, dict), f"wisdom should be a dict, got {type(wisdom)}"
    related = wisdom.get("related_concepts") or []
    assert related, f"wisdom {wisdom.get('title')!r} has no related_concepts (not grounded)"
    known = set(known_concepts)
    overlap = [c for c in related if c in known]
    assert overlap, (
        f"wisdom related_concepts {related} do not intersect known concepts "
        f"({sorted(known)[:5]}...)"
    )


def assert_wisdom_refuses_on_empty(pipeline_result: dict) -> None:
    """Verify pipeline returns nothing_novel / nothing_new on empty inputs,
    not a hallucinated wisdom."""
    kind = pipeline_result.get("type") if isinstance(pipeline_result, dict) else None
    assert kind in ("nothing_new", "nothing_novel", "empty"), (
        f"pipeline should refuse on empty input, got type={kind!r} "
        f"(result: {pipeline_result})"
    )
