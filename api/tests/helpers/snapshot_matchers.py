"""Custom syrupy matchers for approximate float comparisons.

Algorithm outputs contain floats (scores, weights, similarities) that drift
slightly across runs for reasons that do not matter to correctness (iteration
order in hash sets, last-bit rounding). Straight equality against a committed
snapshot fails for the wrong reasons.

`round_floats` pre-processes a result tree, rounding every float to a fixed
precision. Pair with syrupy's default serializer.

Usage in a test:

    from tests.helpers.snapshot_matchers import round_floats

    def test_algorithm(snapshot):
        result = algo.run(ctx)
        assert round_floats(result, ndigits=4) == snapshot

This approach is deliberately simpler than a numpy.allclose matcher. Rounding
is easy to read in a failing diff; allclose tolerance is not.
"""

from __future__ import annotations

from typing import Any


VOLATILE_KEYS = frozenset({
    "first_seen",
    "last_seen",
    "t_first_observed",
    "t_last_observed",
    "t_created",
    "t_expired",
    "t_valid_from",  # stamped at add_concept if not overridden; concept records inherit now()
    "t_valid_to",
    "t_superseded_by",
    "last_accessed",
    "extracted_at",
    "created_at",
    "updated_at",
    # Config-driven fields whose value depends on settings state outside
    # the algorithm under test. Scrubbed for snapshot stability.
    "namespace",
})


def stabilize(obj: Any, ndigits: int = 4) -> Any:
    """Round floats AND scrub timestamp-shaped keys to stable placeholders.

    Use this for snapshot tests that run against a GraphStore or ConceptStore,
    since inserts stamp concepts with wall-clock `first_seen` / `t_last_observed`
    fields that would defeat snapshot replay otherwise.
    """
    return _stabilize(obj, ndigits)


def _stabilize(obj: Any, ndigits: int) -> Any:
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if k in VOLATILE_KEYS:
                out[k] = "<scrubbed>"
            else:
                out[k] = _stabilize(v, ndigits)
        return out
    if isinstance(obj, list):
        return [_stabilize(x, ndigits) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_stabilize(x, ndigits) for x in obj)
    if isinstance(obj, set):
        return sorted((_stabilize(x, ndigits) for x in obj), key=_stable_key)
    return obj


def round_floats(obj: Any, ndigits: int = 4) -> Any:
    """Recursively round all floats in a (nested) structure.

    Handles dicts, lists, tuples, and sets. Tuples are preserved as tuples.
    Sets are converted to sorted lists so the output is stable under syrupy.
    Everything else passes through unchanged.
    """
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats(x, ndigits) for x in obj]
    if isinstance(obj, tuple):
        return tuple(round_floats(x, ndigits) for x in obj)
    if isinstance(obj, set):
        return sorted((round_floats(x, ndigits) for x in obj), key=_stable_key)
    return obj


def _stable_key(x: Any) -> tuple:
    """Stable sort key that works across heterogeneous types."""
    return (type(x).__name__, str(x))
