"""Semantic link prediction via embedding cosine similarity.

For every pair of concepts (A, B) NOT currently linked, compute cosine
similarity between their signature embeddings. Return top-k above threshold.

Sibling to `textual_similarity`:
  - `textual_similarity`: SequenceMatcher on strings. Catches exact + typo + rewording.
  - `embedding_similarity`: cosine on embeddings. Catches synonyms without string overlap.

Both live in the `link_prediction/` family. Users run either (or both) depending
on tradeoff: embedding is slower but catches more. Textual is instant but misses
"Kuzu migration" ↔ "Graph DB switch" — the exact case embedding handles.

Cost: one embedding call (batched across all concepts), then O(n²) pure-Python
cosine computations. At ~1000 concepts and default params: ~300ms embedding +
~100ms cosine = sub-second.

Reference:
  Reimers, N. & Gurevych, I. (2019). Sentence-BERT: Sentence Embeddings
    using Siamese BERT-Networks. EMNLP 2019.
  Manning, C. et al. (2008). Introduction to Information Retrieval,
    Cambridge, ch. 6 (cosine similarity, tf-idf vectors).
  Mikolov, T. et al. (2013). Efficient Estimation of Word Representations
    in Vector Space. arXiv:1301.3781 (word2vec).
"""

from __future__ import annotations

import logging
import math

from extended_thinking.algorithms.protocol import (
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


class EmbeddingSimilarityLinkPrediction:
    """Semantic link prediction via sentence-level embedding cosine similarity."""

    meta = AlgorithmMeta(
        name="embedding_similarity",
        family="link_prediction",
        description="Unlinked concept pairs ranked by embedding cosine similarity (catches synonyms)",
        paper_citation="Reimers & Gurevych 2019 EMNLP (Sentence-BERT). Manning IR ch.6 (cosine).",
        parameters={
            "top_k": 10,          # how many candidates to return
            "threshold": 0.75,    # minimum cosine similarity (0-1)
            "use_description": True,  # include description in signature
        },
        temporal_aware=True,
    )

    def __init__(self, top_k: int = 10, threshold: float = 0.75,
                 use_description: bool = True):
        self.top_k = top_k
        self.threshold = threshold
        self.use_description = use_description

    def run(self, context: AlgorithmContext) -> list[dict]:
        """Return candidate unlinked concept pairs ranked by cosine similarity.

        Each result:
          {"from": {...}, "to": {...}, "similarity": float,
           "signature_a": str, "signature_b": str}
        """
        kg = context.kg
        vectors = context.vectors
        as_of = context.as_of

        if vectors is None or not hasattr(vectors, "embed"):
            logger.debug("embedding_similarity skipped: no VectorStore")
            return []

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

        # Batch embed all concept signatures in one call (critical for cost)
        signatures = [self._signature(c) for c in concepts]
        try:
            embeddings = vectors.embed(signatures)
        except Exception as e:
            logger.warning("Embedding failed in link prediction: %s", e)
            return []

        if not embeddings or len(embeddings) != len(concepts):
            return []

        # Precompute norms once (cosine denominator)
        norms = [_norm(v) for v in embeddings]

        # O(n²) cosine comparisons
        candidates = []
        for i in range(len(concepts)):
            for j in range(i + 1, len(concepts)):
                a, b = concepts[i], concepts[j]
                pair_key = tuple(sorted([a["id"], b["id"]]))
                if pair_key in existing_edges:
                    continue
                sim = _cosine_precomputed(embeddings[i], embeddings[j], norms[i], norms[j])
                if sim >= self.threshold:
                    candidates.append({
                        "from": {"id": a["id"], "name": a["name"]},
                        "to": {"id": b["id"], "name": b["name"]},
                        "similarity": round(sim, 3),
                        "signature_a": signatures[i][:100],
                        "signature_b": signatures[j][:100],
                    })

        candidates.sort(key=lambda c: c["similarity"], reverse=True)
        return candidates[:self.top_k]

    def _signature(self, concept: dict) -> str:
        parts = [concept.get("name", "")]
        if self.use_description and concept.get("description"):
            parts.append(concept["description"])
        if concept.get("source_quote"):
            parts.append(concept["source_quote"])
        return ". ".join(p for p in parts if p)

    def _build_edge_set(self, kg, as_of: str | None) -> set[tuple[str, str]]:
        edges: set[tuple[str, str]] = set()
        if not hasattr(kg, "_query_all"):
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


def _norm(v) -> float:
    """Pure-Python L2 norm. Accepts lists or numpy arrays."""
    total = 0.0
    for x in v:
        xf = float(x)
        total += xf * xf
    return math.sqrt(total)


def _cosine_precomputed(a, b, na: float, nb: float) -> float:
    """Cosine similarity with precomputed norms. Avoids redundant sqrt work."""
    if na == 0.0 or nb == 0.0:
        return 0.0
    la = len(a) if hasattr(a, "__len__") else 0
    lb = len(b) if hasattr(b, "__len__") else 0
    if la == 0 or lb == 0 or la != lb:
        return 0.0
    dot = 0.0
    for i in range(la):
        dot += float(a[i]) * float(b[i])
    return dot / (na * nb)


register(EmbeddingSimilarityLinkPrediction)
