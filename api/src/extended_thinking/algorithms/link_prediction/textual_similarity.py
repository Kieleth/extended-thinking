"""Textual similarity link prediction.

For every pair of concepts (A, B) not currently connected in the graph,
compute text similarity on the combined signature:
    signature(c) = c.name + ". " + c.description + ". " + c.source_quote

Rank pairs by similarity. Return top-k candidates above threshold.

This is the simplest possible link predictor: no embeddings, no deep
learning, no external model calls. It catches duplicate concepts that
entity resolution missed, and near-duplicate concepts that genuinely
belong to the same semantic neighborhood but haven't been linked.

Tradeoffs vs embedding-based similarity:
  - Pros: zero dependencies, deterministic, fast on small graphs.
  - Cons: misses synonym pairs ("Kuzu migration" vs "Graph DB switch"),
    surface-level (no semantic understanding).

For semantic similarity at scale, add `embedding_similarity` as a sibling
plugin that uses VectorStore.

Reference:
  Liben-Nowell, D. & Kleinberg, J. (2007). The link-prediction problem
    for social networks. JASIST 58(7):1019-1031.
  Python's difflib.SequenceMatcher — Ratcliff-Obershelp algorithm.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from extended_thinking.algorithms.protocol import (
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


class TextualSimilarityLinkPrediction:
    """Suggest unlinked concept pairs ranked by textual similarity."""

    meta = AlgorithmMeta(
        name="textual_similarity",
        family="link_prediction",
        description="Unlinked concept pairs ranked by SequenceMatcher similarity on name+description",
        paper_citation="Liben-Nowell & Kleinberg 2007, JASIST 58(7). Ratcliff-Obershelp difflib.",
        parameters={
            "top_k": 10,            # how many candidates to return
            "threshold": 0.5,       # minimum similarity to include
            "max_pairs": 5000,      # cap O(n^2) work
        },
        temporal_aware=True,
    )

    def __init__(self, top_k: int = 10, threshold: float = 0.5,
                 max_pairs: int = 5000):
        self.top_k = top_k
        self.threshold = threshold
        self.max_pairs = max_pairs

    def run(self, context: AlgorithmContext) -> list[dict]:
        """Return candidate unlinked pairs with similarity scores.

        Each result:
          {"from": {...}, "to": {...}, "similarity": float,
           "signature_a": str, "signature_b": str}
        """
        kg = context.kg
        as_of = context.as_of

        if as_of and hasattr(kg, "list_concepts"):
            try:
                concepts = kg.list_concepts(limit=1000, as_of=as_of)
            except TypeError:
                concepts = kg.list_concepts(limit=1000)
        else:
            concepts = kg.list_concepts(limit=1000) if hasattr(kg, "list_concepts") else []

        if len(concepts) < 2:
            return []

        existing_edges = self._build_edge_set(kg, as_of)

        candidates = []
        n_pairs = 0
        for i, a in enumerate(concepts):
            for b in concepts[i + 1:]:
                if n_pairs >= self.max_pairs:
                    break
                n_pairs += 1
                pair_key = tuple(sorted([a["id"], b["id"]]))
                if pair_key in existing_edges:
                    continue  # already connected
                sig_a = self._signature(a)
                sig_b = self._signature(b)
                if not sig_a or not sig_b:
                    continue
                sim = SequenceMatcher(None, sig_a.lower(), sig_b.lower()).ratio()
                if sim >= self.threshold:
                    candidates.append({
                        "from": {"id": a["id"], "name": a["name"]},
                        "to": {"id": b["id"], "name": b["name"]},
                        "similarity": round(sim, 3),
                        "signature_a": sig_a[:100],
                        "signature_b": sig_b[:100],
                    })
            if n_pairs >= self.max_pairs:
                logger.info("Link prediction: hit max_pairs=%d cap", self.max_pairs)
                break

        candidates.sort(key=lambda c: c["similarity"], reverse=True)
        return candidates[:self.top_k]

    def _signature(self, concept: dict) -> str:
        """Build the text signature used for similarity comparison."""
        parts = [concept.get("name", "")]
        if concept.get("description"):
            parts.append(concept["description"])
        if concept.get("source_quote"):
            parts.append(concept["source_quote"])
        return ". ".join(p for p in parts if p)

    def _build_edge_set(self, kg, as_of: str | None) -> set[tuple[str, str]]:
        """Return {(source_id, target_id) sorted tuples} of existing edges."""
        edges: set[tuple[str, str]] = set()
        if not hasattr(kg, "_query_all"):
            # Fallback: iterate concepts + relationships
            if hasattr(kg, "list_concepts") and hasattr(kg, "get_relationships"):
                for c in kg.list_concepts(limit=1000):
                    for r in kg.get_relationships(c["id"]):
                        edges.add(tuple(sorted([r["source_id"], r["target_id"]])))
            return edges

        if as_of:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]-(b:Concept) "
                "WHERE r.t_valid_from <= $as_of "
                "AND (r.t_valid_to = '' OR r.t_valid_to > $as_of) "
                "RETURN a.id, b.id",
                {"as_of": as_of},
            )
        else:
            rows = kg._query_all(
                "MATCH (a:Concept)-[r:RelatesTo]-(b:Concept) "
                "WHERE r.t_expired IS NULL OR r.t_expired = '' "
                "RETURN a.id, b.id"
            )
        for r in rows:
            edges.add(tuple(sorted([r[0], r[1]])))
        return edges


register(TextualSimilarityLinkPrediction)
