"""Embedding-based entity resolution via cosine similarity.

For each query concept, embed its signature (name + description). Embed
all existing concept signatures. Compute cosine similarity against each.
Return the best match above threshold.

Catches what SequenceMatcher misses:
  - Semantic synonyms: "Kuzu migration" vs "Graph DB switch"
  - Abbreviations: "KG" vs "Knowledge Graph"
  - Rephrased concepts: "Additive-only extensions" vs "Monotonic schema evolution"

Tradeoffs vs SequenceMatcher:
  - Pros: semantic understanding, catches rewordings.
  - Cons: requires VectorStore with an embedding function, slower per query
    (one embedding call batched across all existing concepts).

Reference:
  Sentence-BERT (Reimers & Gurevych 2019, EMNLP) for embedding-based
    semantic similarity. ChromaDB default uses all-MiniLM-L6-v2 (384-dim).
  Manning et al., Introduction to Information Retrieval, ch. 6 (cosine similarity).
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


class EmbeddingCosineResolution:
    """Entity resolution via cosine similarity of concept signature embeddings."""

    meta = AlgorithmMeta(
        name="embedding_cosine",
        family="resolution",
        description="Semantic entity resolution via embedding cosine similarity (catches synonyms)",
        paper_citation="Reimers & Gurevych 2019 EMNLP (Sentence-BERT). Manning IR ch.6 (cosine).",
        parameters={"threshold": 0.82, "use_description": True},
        temporal_aware=False,
    )

    def __init__(self, threshold: float = 0.82, use_description: bool = True):
        self.threshold = threshold
        self.use_description = use_description

    def run(self, context: AlgorithmContext) -> None:
        """Resolution runs per-concept via resolve(), not batch. No-op here."""
        return None

    def resolve(self, context: AlgorithmContext, query_name: str,
                query_description: str = "") -> dict | None:
        """Find semantically similar existing concept via embedding cosine.

        Args:
            context: AlgorithmContext with kg + vectors access.
            query_name: candidate concept name.
            query_description: candidate concept description (optional).

        Returns:
            Matched concept dict, or None if nothing above threshold or if
            VectorStore has no embedding function.
        """
        kg = context.kg
        vectors = context.vectors

        if vectors is None or not hasattr(vectors, "embed"):
            logger.debug("EmbeddingCosineResolution skipped: no VectorStore")
            return None
        if not hasattr(kg, "list_concepts"):
            return None

        # Scope by namespace (ADR 013 C2) so resolution only merges within
        # the same folder-project; fall back unscoped for pre-013 stores.
        ns = getattr(context, "namespace", None)
        try:
            existing = kg.list_concepts(limit=1000, namespace=ns)
        except TypeError:
            existing = kg.list_concepts(limit=1000)
        if not existing:
            return None

        query_sig = self._signature(query_name, query_description)
        existing_sigs = [self._signature(c["name"], c.get("description", "") if self.use_description else "")
                         for c in existing]

        try:
            # Batch embed: query first, then all existing. One call.
            all_embeddings = vectors.embed([query_sig] + existing_sigs)
        except Exception as e:
            logger.warning("Embedding failed in resolution: %s", e)
            return None

        query_vec = all_embeddings[0]
        existing_vecs = all_embeddings[1:]

        best_score = 0.0
        best_match = None
        for concept, vec in zip(existing, existing_vecs):
            score = _cosine(query_vec, vec)
            if score > best_score and score >= self.threshold:
                best_score = score
                best_match = concept

        return best_match

    def _signature(self, name: str, description: str = "") -> str:
        if self.use_description and description:
            return f"{name}. {description}"
        return name


def _cosine(a, b) -> float:
    """Cosine similarity. Accepts lists or numpy arrays.

    Pure-Python arithmetic (iterates elements); avoids numpy truth-value
    ambiguity when array-likes are passed.
    """
    la = len(a) if hasattr(a, "__len__") else 0
    lb = len(b) if hasattr(b, "__len__") else 0
    if la == 0 or lb == 0 or la != lb:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(la):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


register(EmbeddingCosineResolution)
