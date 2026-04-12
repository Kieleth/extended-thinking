"""Recency-weighted activity score.

Surfaces the top-k active concepts by combining three signals:

    score = frequency * recency * sqrt(degree)

Where:
    frequency  = times the concept has been extracted (attestation volume)
    recency    = 1 / (1 + days_since_last_access)  (hyperbolic decay)
    degree     = number of RelatesTo edges incident to the concept

Rationale: frequent + recent + well-connected concepts are the user's
current thinking. Pure frequency overweights old stable topics; pure
recency is noisy on a single chunk; degree grounds the signal in the
graph's semantic structure.

This matches the biological intuition behind Physarum (recent high-flow
tubes thicken) and connectome rich-clubs (hubs stabilize activity), just
applied to the node level rather than edge or substrate.

Reference:
    Logan, G.D. (1988). Toward an instance theory of automatization.
    Psychological Review 95(4):492-527 (frequency * recency in memory).
    van den Heuvel, M.P. & Sporns, O. (2011). Rich-club organization of
    the human connectome. Journal of Neuroscience 31(44):15775-15786.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from extended_thinking.algorithms.protocol import (
    Algorithm,
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register


class RecencyWeightedActivity:
    """Top-k active concepts by frequency * recency * sqrt(degree)."""

    meta = AlgorithmMeta(
        name="recency_weighted",
        family="activity_score",
        description="Top-k active concepts by frequency * recency * sqrt(degree)",
        paper_citation="Logan 1988, Psych Review 95(4). van den Heuvel & Sporns 2011, J Neurosci 31(44).",
        parameters={"top_k": 10},
        temporal_aware=True,
    )

    def __init__(self, top_k: int = 10):
        self.top_k = top_k

    def run(self, context: AlgorithmContext) -> list[dict]:
        kg = context.kg
        k = int(context.params.get("top_k", self.top_k))
        now = context.now or datetime.now(timezone.utc)
        ns = context.namespace  # ADR 013 C2

        if hasattr(kg, "list_concepts"):
            import inspect
            if "namespace" in inspect.signature(kg.list_concepts).parameters:
                concepts = kg.list_concepts(limit=500, namespace=ns)
            else:
                concepts = kg.list_concepts(limit=500)
        else:
            concepts = []
        if not concepts:
            return []

        # Effective degree: sum of Physarum-decayed incident edge weights.
        # A concept connected only to old evidence gets a near-zero degree
        # contribution; a concept with fresh edges stays strong. This is
        # where source-age awareness enters node-level scoring without
        # hardcoding source dates on concepts themselves.
        eff_degree = self._effective_degree_map(kg, now)

        scored: list[tuple[dict, float]] = []
        for c in concepts:
            freq = c.get("frequency", 1) or 1
            # Prefer explicit access time, fall back to last_seen.
            ts = c.get("last_accessed") or c.get("last_seen") or ""
            recency = self._recency(ts, now)
            eff = eff_degree.get(c["id"], 0.0)
            score = freq * recency * math.sqrt(max(eff, 1.0))
            scored.append((c, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:k]]

    @staticmethod
    def _recency(last_accessed: str, now: datetime) -> float:
        if not last_accessed:
            return 0.1
        try:
            last_dt = datetime.fromisoformat(last_accessed)
        except (ValueError, TypeError):
            return 0.1
        days = (now - last_dt).total_seconds() / 86400.0
        return 1.0 / (1.0 + max(0.0, days))

    @staticmethod
    def _degree_map(kg) -> dict[str, int]:
        result: dict[str, int] = {}
        if not hasattr(kg, "_query_all"):
            return result
        rows = kg._query_all(
            "MATCH (a:Concept)-[:RelatesTo]-(b:Concept) RETURN a.id, count(b)"
        )
        for r in rows:
            result[r[0]] = r[1]
        return result

    @staticmethod
    def _effective_degree_map(kg, now: datetime) -> dict[str, float]:
        """Per-concept sum of Physarum-decayed incident edge weights.

        Uses edge t_valid_from so stale-source edges barely count, fresh
        ones count fully. Undirected: each edge contributes to both ends.
        """
        result: dict[str, float] = {}
        if not hasattr(kg, "_query_all"):
            return result
        from extended_thinking.algorithms.decay.physarum import PhysarumDecay
        decay = PhysarumDecay(source_age_aware=True)
        rows = kg._query_all(
            "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) "
            "RETURN a.id, b.id, r.weight, r.last_accessed, r.t_valid_from"
        )
        for a, b, w, la, vf in rows:
            eff = decay.compute_effective_weight(
                base_weight=w or 1.0,
                last_accessed=la or "",
                t_valid_from=vf or "",
                now=now,
            )
            result[a] = result.get(a, 0.0) + eff
            result[b] = result.get(b, 0.0) + eff
        return result


register(RecencyWeightedActivity)
