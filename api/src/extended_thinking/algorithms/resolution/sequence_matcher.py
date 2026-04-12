"""Character-level entity resolution via SequenceMatcher.

Ratcliff-Obershelp algorithm: finds the longest contiguous matching
subsequence recursively. Returns ratio in [0, 1]. Fast, deterministic,
no ML dependencies.

Catches:
  - Exact duplicates after normalization ("Ontology" vs "ontology  ")
  - Minor typos or spacing differences
  - Concepts where user rephrased but kept most of the wording

Misses:
  - Semantic synonyms without string overlap ("Kuzu switch" vs "Graph DB choice")
  - Abbreviations vs expansions ("KG" vs "Knowledge Graph")

For those, see EmbeddingCosineResolution (sibling plugin).

Reference:
  Ratcliff, J. W. & Metzener, D. E. (1988). Pattern Matching: the Gestalt
    Approach. Dr. Dobb's Journal. (Implementation: Python difflib.)
"""

from __future__ import annotations

from difflib import SequenceMatcher

from extended_thinking.algorithms.protocol import (
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register


class SequenceMatcherResolution:
    """Entity resolution via character-level similarity."""

    meta = AlgorithmMeta(
        name="sequence_matcher",
        family="resolution",
        description="Character-level entity resolution via Ratcliff-Obershelp matching",
        paper_citation="Ratcliff & Metzener 1988, Dr. Dobb's Journal (Gestalt pattern matching).",
        parameters={"threshold": 0.85},
        temporal_aware=False,  # string matching doesn't depend on time
    )

    def __init__(self, threshold: float = 0.85):
        self.threshold = threshold

    def run(self, context: AlgorithmContext) -> None:
        """Resolution runs per-concept via resolve(), not batch. No-op here."""
        return None

    def resolve(self, context: AlgorithmContext, query_name: str) -> dict | None:
        """Find the best matching existing concept, or None.

        Args:
            context: AlgorithmContext with kg access.
            query_name: the name of the candidate concept.

        Returns:
            The matched concept dict, or None if nothing above threshold.
        """
        kg = context.kg
        normalized = query_name.lower().strip()
        norm_id = normalized.replace(" ", "-").replace("/", "-")[:60]

        if not hasattr(kg, "list_concepts"):
            return None

        best_match = None
        best_score = 0.0

        for c in kg.list_concepts(limit=1000):
            if norm_id == c["id"]:
                return c  # exact ID match (already normalized)
            existing = c["name"].lower().strip()
            score = SequenceMatcher(None, normalized, existing).ratio()
            if score > best_score and score >= self.threshold:
                best_score = score
                best_match = c

        return best_match


register(SequenceMatcherResolution)
