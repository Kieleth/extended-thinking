"""Top-percentile bridge detection.

Bridges are concepts with degree in the top-N percentile of the graph,
subject to a floor (don't label a concept as a "bridge" if its degree
is only 2, even if the graph is very sparse).

This is the simplest possible rich-club heuristic: fast, deterministic,
no graph library required. For rigorous betweenness centrality, a sibling
plugin will eventually exist.

Reference:
  van den Heuvel, M. P. & Sporns, O. (2011). Rich-club organization of
    the human connectome. J Neurosci 31(44):15775-15786.
  Colizza, V. et al. (2006). Detecting rich-club ordering in complex
    networks. Nature Physics 2:110-115.
  Bassett, D. S. & Bullmore, E. T. (2017). Small-world brain networks
    revisited. The Neuroscientist 23(5):499-516.
"""

from __future__ import annotations

import logging

from extended_thinking.algorithms.protocol import (
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


class TopPercentileBridges:
    """Identify bridge concepts: top-N percentile by degree, floor enforced."""

    meta = AlgorithmMeta(
        name="top_percentile",
        family="bridges",
        description="Rich-club bridges: top-N percentile by degree, with minimum degree floor",
        paper_citation="van den Heuvel & Sporns 2011, J Neurosci 31(44). Colizza et al. 2006, Nature Physics 2.",
        parameters={
            "percentile": 0.10,    # top 10% by degree
            "min_degree": 5,       # floor: don't label as bridge below this
        },
        temporal_aware=True,
    )

    def __init__(self, percentile: float = 0.10, min_degree: int = 5):
        self.percentile = percentile
        self.min_degree = min_degree

    def run(self, context: AlgorithmContext) -> list[dict]:
        """Return concepts ranked as bridges with their degrees.

        Each result: {"concept": {...}, "degree": int}
        Empty list if the graph has no edges or no concepts meet threshold.
        """
        kg = context.kg
        as_of = context.as_of

        # Collect concepts and build degree map
        if as_of and hasattr(kg, "list_concepts"):
            try:
                concepts = kg.list_concepts(limit=1000, as_of=as_of)
            except TypeError:
                concepts = kg.list_concepts(limit=1000)
        else:
            concepts = kg.list_concepts(limit=1000) if hasattr(kg, "list_concepts") else []

        if not concepts:
            return []

        concept_map = {c["id"]: c for c in concepts}
        degree = self._compute_degree(kg, as_of)

        if not degree:
            return []

        # Determine threshold: top-N percentile, but not below min_degree floor
        sorted_degrees = sorted(degree.values(), reverse=True)
        cutoff_index = max(1, int(len(sorted_degrees) * self.percentile))
        percentile_threshold = sorted_degrees[cutoff_index - 1]
        threshold = max(percentile_threshold, self.min_degree)

        results = []
        for nid, d in degree.items():
            if d >= threshold and nid in concept_map:
                results.append({"concept": concept_map[nid], "degree": d})

        results.sort(key=lambda x: x["degree"], reverse=True)
        return results

    def _compute_degree(self, kg, as_of: str | None) -> dict[str, int]:
        """Count distinct neighbors for each concept (undirected)."""
        if not hasattr(kg, "_query_all"):
            # Fallback: iterate concepts
            degree: dict[str, int] = {}
            if hasattr(kg, "list_concepts") and hasattr(kg, "get_relationships"):
                for c in kg.list_concepts(limit=1000):
                    rels = kg.get_relationships(c["id"])
                    # Count distinct neighbors (not edges, to match overview logic)
                    neighbors = set()
                    for r in rels:
                        other = r["target_id"] if r.get("source_id") == c["id"] else r["source_id"]
                        neighbors.add(other)
                    degree[c["id"]] = len(neighbors)
            return degree

        # Kuzu fast path: aggregate distinct neighbors per node
        if as_of:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]-(b:Concept) "
                "WHERE r.t_valid_from <= $as_of "
                "AND (r.t_valid_to = '' OR r.t_valid_to > $as_of) "
                "RETURN a.id, count(DISTINCT b.id)",
                {"as_of": as_of},
            )
        else:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]-(b:Concept) "
                "WHERE r.t_expired IS NULL OR r.t_expired = '' "
                "RETURN a.id, count(DISTINCT b.id)"
            )
        return {r[0]: r[1] for r in rows}


register(TopPercentileBridges)
