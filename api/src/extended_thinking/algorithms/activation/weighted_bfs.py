"""Weighted BFS spreading activation.

For each seed concept with initial score 1.0, propagate through neighbors:

    score[neighbor] += score[node] * edge_weight * decay_per_hop

Where edge_weight is the Physarum-decayed effective weight (recently-used
edges contribute more; stale edges fade). Each hop multiplies by
decay_per_hop (0.7 default), so contribution falls off with distance.

Budget cap prevents runaway expansion into dense graphs. Depth cap bounds
the number of BFS rounds.

Why this beats pure BFS:
  - BFS treats all neighbors equally; weighted activation respects edge strength
  - BFS has no decay; activation prefers shorter paths via the hop multiplier
  - BFS has no budget; activation limits to the most activated ~100 nodes

Reference:
  Anderson, J. R. (1983). A spreading activation theory of memory.
    JVLVB 22(3):261-295.
  Collins, A. & Loftus, E. (1975). A spreading-activation theory of
    semantic processing. Psychological Review 82(6):407.
  Crestani (1997). Application of spreading activation techniques in
    information retrieval. AI Review 11.
"""

from __future__ import annotations

import logging

from extended_thinking.algorithms.protocol import (
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


class WeightedBFSActivation:
    """Spreading activation via weighted BFS with decay-per-hop and budget cap."""

    meta = AlgorithmMeta(
        name="weighted_bfs",
        family="activation",
        description="Weighted BFS spreading activation with Physarum-decayed edge weights",
        paper_citation="Anderson 1983, JVLVB 22(3). Collins & Loftus 1975, Psych Review 82(6). Crestani 1997, AI Review 11.",
        parameters={
            "depth": 3,              # max BFS rounds
            "decay_per_hop": 0.7,    # multiplier applied each hop
            "budget": 100,           # max nodes scored
            "min_spread": 0.01,      # below this, don't propagate further
        },
        temporal_aware=True,
    )

    def __init__(self, depth: int = 3, decay_per_hop: float = 0.7,
                 budget: int = 100, min_spread: float = 0.01):
        self.depth = depth
        self.decay_per_hop = decay_per_hop
        self.budget = budget
        self.min_spread = min_spread

    def run(self, context: AlgorithmContext) -> list[tuple[str, float]]:
        """Spread activation from seeds. Seeds come from context.params["seed_ids"].

        Returns [(concept_id, score), ...] sorted by score descending.
        Seeds are excluded from results (they start at 1.0 by definition).
        """
        kg = context.kg
        seed_ids = context.params.get("seed_ids") or []
        if not seed_ids:
            return []

        as_of = context.as_of

        # Build adjacency: for each node, a list of (neighbor_id, edge_weight).
        # Edge weight is the Physarum-decayed effective weight (recent > stale).
        adj = self._build_adjacency(kg, as_of)

        # Initialize scores with seeds
        scores: dict[str, float] = {sid: 1.0 for sid in seed_ids}

        # BFS propagation
        frontier = list(seed_ids)
        for _ in range(self.depth):
            next_frontier = []
            for node in frontier:
                node_score = scores.get(node, 0.0)
                if node_score < self.min_spread:
                    continue
                for neighbor, weight in adj.get(node, []):
                    spread = node_score * weight * self.decay_per_hop
                    if spread > self.min_spread:
                        old = scores.get(neighbor, 0.0)
                        scores[neighbor] = min(1.0, old + spread)
                        if neighbor not in frontier:
                            next_frontier.append(neighbor)
                    if len(scores) >= self.budget:
                        break
                if len(scores) >= self.budget:
                    break
            frontier = next_frontier
            if not frontier or len(scores) >= self.budget:
                break

        # Exclude seeds from results (already 1.0 by definition)
        seed_set = set(seed_ids)
        results = [(cid, s) for cid, s in scores.items() if cid not in seed_set]
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def _build_adjacency(self, kg, as_of: str | None) -> dict[str, list[tuple[str, float]]]:
        """Build undirected adjacency list with decayed edge weights.

        Uses kg.effective_weight() to get Physarum-decayed weights, which
        means stale edges contribute less to activation spread.
        """
        adj: dict[str, list[tuple[str, float]]] = {}

        if not hasattr(kg, "_query_all"):
            # Fallback: iterate relationships per concept (slow but correct)
            if hasattr(kg, "list_concepts") and hasattr(kg, "get_relationships"):
                for c in kg.list_concepts(limit=1000):
                    for r in kg.get_relationships(c["id"]):
                        src, tgt = r["source_id"], r["target_id"]
                        w = kg.effective_weight(src, tgt) if hasattr(kg, "effective_weight") else (r.get("weight") or 1.0)
                        adj.setdefault(src, []).append((tgt, w))
                        adj.setdefault(tgt, []).append((src, w))
            return adj

        # Kuzu fast path
        if as_of:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]-(b:Concept) "
                "WHERE r.t_valid_from <= $as_of "
                "AND (r.t_valid_to = '' OR r.t_valid_to > $as_of) "
                "RETURN a.id, b.id, r.weight, r.last_accessed",
                {"as_of": as_of},
            )
        else:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]-(b:Concept) "
                "WHERE r.t_expired IS NULL OR r.t_expired = '' "
                "RETURN a.id, b.id, r.weight, r.last_accessed"
            )
        # Compute decayed weights once (use decay plugin directly)
        from extended_thinking.algorithms.decay.physarum import PhysarumDecay
        decayer = PhysarumDecay()
        for r in rows:
            src, tgt, base_w, last_acc = r[0], r[1], r[2] or 1.0, r[3] or ""
            w = decayer.compute_effective_weight(base_w, last_acc)
            adj.setdefault(src, []).append((tgt, w))
            adj.setdefault(tgt, []).append((src, w))
        return adj


register(WeightedBFSActivation)
