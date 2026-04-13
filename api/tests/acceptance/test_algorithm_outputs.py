"""Snapshot tests for every registered algorithm on the small fixture graph.

Pattern: walk the algorithm registry, run each algorithm against
`loaded_graph_small`, snapshot the result (rounded for float stability).

When a prompt or weight changes the output in a way we didn't intend, the
snapshot diff points at the regression. When we DO intend the change,
`make at-update-snapshots` regenerates.

Some algorithms need external deps (LLM for recombination, embeddings for
embedding_similarity). Those are skipped on the fast path and land in the
cassette suite later.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from extended_thinking.algorithms.protocol import AlgorithmContext
from extended_thinking.algorithms.registry import list_available
from extended_thinking.algorithms.registry import _registry as _alg_registry
from tests.helpers.snapshot_matchers import stabilize

# Fixed `now` so algorithms that use recency (activity_score, decay) produce
# stable output against the committed snapshot. Day after the fixture's
# t_valid_from so recency windows are non-trivial but deterministic.
FIXED_NOW = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.acceptance

# Algorithms that need external services (LLM, real embeddings) and therefore
# should not run on the fast path. They are covered by cassette + live suites.
_EXTERNAL_DEP = {
    "embedding_similarity",
    "embedding_cosine",
    "cross_cluster_grounded",
}

# Algorithms whose run() is intentionally a no-op. Snapshotting their output
# via run() is theater. Physarum decay is read-time; its real behavior is
# snapshotted in `test_physarum_compute_effective_weight_snapshot` below.
_NO_OP_RUN = {"physarum"}


def _all_fast_path_algorithms():
    """Yield (name, cls) for every registered algorithm that runs offline AND
    has a meaningful `run()` to snapshot. No-op-run algorithms get dedicated
    tests (see test_physarum_compute_effective_weight_snapshot).

    Enrichment-family plugins (sources, triggers, relevance_gates, cache)
    implement specialized interfaces from ADR 011 v2 and do not expose a
    generic `run()`; they are exercised by dedicated enrichment AT."""
    for meta in list_available():
        if meta.name in _EXTERNAL_DEP or meta.name in _NO_OP_RUN:
            continue
        if meta.family.startswith("enrichment"):
            continue
        yield meta.name, _alg_registry[meta.name]


@pytest.mark.parametrize(
    "algo_name,algo_cls",
    list(_all_fast_path_algorithms()),
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_algorithm_snapshot(loaded_graph_small, snapshot, algo_name, algo_cls):
    """Each algorithm's output on the small fixture is committed to a snapshot.

    Run order and snapshot key are stable because we round floats to 4 digits
    and the fixture ID set is deterministic.
    """
    # Instantiate with declared defaults.
    default_params = dict(algo_cls.meta.parameters)
    instance = algo_cls(**default_params) if default_params else algo_cls()

    ctx = AlgorithmContext(kg=loaded_graph_small, vectors=None, now=FIXED_NOW)
    result = instance.run(ctx)
    assert stabilize(result, ndigits=4) == snapshot(name=algo_name)


def test_physarum_compute_effective_weight_snapshot(loaded_graph_small, snapshot):
    """Dedicated test for physarum's real behavior.

    `PhysarumDecay.run()` is a no-op by design (decay is read-time, not
    batch). Production callers invoke `compute_effective_weight()` directly
    from recency_weighted, weighted_bfs, and graph_store's utility path.

    This test snapshots decayed weights over the fixture's edges at a fixed
    idle age, exercising the real decay formula. Diffs catch regressions
    in the math that the parametrized run()-based snapshot would miss.
    """
    from extended_thinking.algorithms.decay.physarum import PhysarumDecay

    decay = PhysarumDecay(decay_rate=0.95, source_age_aware=True)
    rows = loaded_graph_small._query_all(
        "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) "
        "RETURN a.id, b.id, r.weight, r.last_accessed, r.t_valid_from "
        "ORDER BY a.id, b.id",
        {},
    )

    decayed = []
    for src, tgt, base_w, last_acc, vf in rows:
        eff = decay.compute_effective_weight(
            base_weight=base_w,
            last_accessed=last_acc or "",
            now=FIXED_NOW,
            t_valid_from=vf or "",
        )
        decayed.append({
            "source": src,
            "target": tgt,
            "base_weight": base_w,
            "effective_weight": eff,
        })

    assert stabilize(decayed, ndigits=4) == snapshot
