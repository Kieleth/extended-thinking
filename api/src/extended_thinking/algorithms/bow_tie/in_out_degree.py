"""In/out-degree bow-tie core detection.

Metabolic networks have a bow-tie structure: thousands of inputs (nutrients,
substrates) funnel through a small core (~12 precursor metabolites) and fan
out to thousands of outputs (macromolecules, cellular functions). The core
is conserved across species; the periphery varies wildly.

Cognitive thinking graphs show the same structure. A few core concepts
("Ontology as type system", "Additive extension semantics") attract many
source observations and radiate into many downstream ideas. These are the
user's actual recurring themes — distinct from high-frequency concepts
(which may be mentioned often but not structurally central).

This algorithm identifies bow-tie core concepts by:

    in_degree = # of distinct chunks referencing this concept (breadth of input)
    out_degree = # of distinct concepts this concept points to (fan-out)
    bow_tie_score = sqrt(in_degree * out_degree)

Concepts with high geometric-mean score are simultaneously well-attested
AND productively connected. High in, low out: common observation, no synthesis.
Low in, high out: speculative, thin grounding. High both: the core.

Reference:
    Csete, M.E. & Doyle, J.C. (2004). Bow ties, metabolism and disease.
    Trends in Biotechnology 22(9):446-450.
    Ma, H-W. & Zeng, A-P. (2003). The connectivity structure, giant strong
    component and centrality of metabolic networks. Bioinformatics 19(11).
    BMC Bioinformatics: nested bow-tie architecture in metabolic networks.
"""

from __future__ import annotations

import math

from extended_thinking.algorithms.protocol import (
    Algorithm,
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register


class InOutDegreeBowTie:
    """Identify bow-tie core concepts by geometric mean of in/out degree.

    Result: list of dicts, one per core concept, with score + justification.
    Temporal-aware: respects `context.as_of` if provided.
    """

    meta = AlgorithmMeta(
        name="in_out_degree",
        family="bow_tie",
        description="Bow-tie core concepts via geometric mean of chunk-in-degree and concept-out-degree",
        paper_citation="Csete & Doyle 2004, Trends in Biotechnology 22(9). Ma & Zeng 2003, Bioinformatics 19(11).",
        parameters={
            "top_k": 10,               # how many core concepts to return
            "min_in_degree": 2,        # must be attested by >= N distinct chunks
            "min_out_degree": 2,       # must connect to >= N downstream concepts
        },
        temporal_aware=True,
    )

    def __init__(self, top_k: int = 10, min_in_degree: int = 2, min_out_degree: int = 2):
        self.top_k = top_k
        self.min_in_degree = min_in_degree
        self.min_out_degree = min_out_degree

    def run(self, context: AlgorithmContext) -> list[dict]:
        """Compute bow-tie scores for all concepts, return top-k.

        Each result dict contains:
          concept: the full concept dict
          in_degree: # chunks this concept has provenance edges to
          out_degree: # RelatesTo edges outgoing from this concept
          bow_tie_score: sqrt(in * out)
          justification: human-readable reason for being core
        """
        kg = context.kg
        as_of = context.as_of

        # Concepts at the target point in time (if as_of provided)
        if as_of and hasattr(kg, "list_concepts"):
            try:
                concepts = kg.list_concepts(limit=1000, as_of=as_of)
            except TypeError:
                concepts = kg.list_concepts(limit=1000)
        else:
            concepts = kg.list_concepts(limit=1000) if hasattr(kg, "list_concepts") else []

        if not concepts:
            return []

        # Use raw Cypher for fast in/out-degree — works on Kuzu GraphStore,
        # falls back gracefully otherwise.
        in_degree_map = self._compute_in_degree(kg, concepts, as_of)
        out_degree_map = self._compute_out_degree(kg, concepts, as_of)

        scored = []
        for c in concepts:
            cid = c["id"]
            in_d = in_degree_map.get(cid, 0)
            out_d = out_degree_map.get(cid, 0)
            if in_d < self.min_in_degree or out_d < self.min_out_degree:
                continue
            score = math.sqrt(in_d * out_d)
            scored.append({
                "concept": c,
                "in_degree": in_d,
                "out_degree": out_d,
                "bow_tie_score": round(score, 3),
                "justification": self._justify(c, in_d, out_d),
            })

        scored.sort(key=lambda x: x["bow_tie_score"], reverse=True)
        return scored[:self.top_k]

    def _compute_in_degree(self, kg, concepts, as_of: str | None) -> dict[str, int]:
        """For each concept, count distinct chunks that produced it (via HasProvenance)."""
        result: dict[str, int] = {}
        if not hasattr(kg, "_query_all"):
            # Fallback for stores without Cypher: use provenance-per-concept
            for c in concepts:
                if hasattr(kg, "get_provenance"):
                    provs = kg.get_provenance(c["id"])
                    result[c["id"]] = len({p.get("source_chunk_id", "") for p in provs if p.get("source_chunk_id")})
                else:
                    result[c["id"]] = 0
            return result

        # Kuzu path: single aggregated query
        if as_of:
            rows = kg._query_all(
                "MATCH (c:Concept)-[p:HasProvenance]->(ch:Chunk) "
                "WHERE p.t_valid_from <= $as_of "
                "AND (p.t_valid_to = '' OR p.t_valid_to > $as_of) "
                "RETURN c.id, count(DISTINCT ch.id)",
                {"as_of": as_of},
            )
        else:
            rows = kg._query_all(
                "MATCH (c:Concept)-[p:HasProvenance]->(ch:Chunk) "
                "WHERE p.t_expired IS NULL OR p.t_expired = '' "
                "RETURN c.id, count(DISTINCT ch.id)"
            )
        for r in rows:
            result[r[0]] = r[1]
        return result

    def _compute_out_degree(self, kg, concepts, as_of: str | None) -> dict[str, int]:
        """For each concept, count outgoing RelatesTo edges."""
        result: dict[str, int] = {}
        if not hasattr(kg, "_query_all"):
            for c in concepts:
                if hasattr(kg, "get_relationships"):
                    rels = kg.get_relationships(c["id"])
                    # Only count edges where this concept is the source
                    result[c["id"]] = sum(1 for r in rels if r.get("source_id") == c["id"])
                else:
                    result[c["id"]] = 0
            return result

        if as_of:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) "
                "WHERE r.t_valid_from <= $as_of "
                "AND (r.t_valid_to = '' OR r.t_valid_to > $as_of) "
                "RETURN a.id, count(b)",
                {"as_of": as_of},
            )
        else:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) "
                "WHERE r.t_expired IS NULL OR r.t_expired = '' "
                "RETURN a.id, count(b)"
            )
        for r in rows:
            result[r[0]] = r[1]
        return result

    def _justify(self, concept: dict, in_d: int, out_d: int) -> str:
        name = concept.get("name", concept.get("id", "?"))
        category = concept.get("category", "concept")
        return (f"'{name}' ({category}) is attested by {in_d} distinct sources "
                f"and feeds into {out_d} downstream concepts — a convergence point.")


register(InOutDegreeBowTie)
