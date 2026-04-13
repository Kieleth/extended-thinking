"""Embedding-cosine relevance gate (ADR 011 v2).

Cheapest useful gate: compute cosine similarity between the user
concept's text signature and the candidate's abstract. Below the floor,
reject. Above the ceiling, auto-accept (short-circuits remaining
gates). Between, pass-through with score for the next gate.

Thresholds are starting points (ADR 011 v2 user call: configurable,
track statistics, adjust from real data). Dogfood run telemetry
(`EnrichmentRun.gate_trace`) tells the user where the distribution
actually lands.

Requires a VectorStore on the context (via `context.vectors`). If
none is attached, the gate degrades gracefully — returns a soft-accept
with score 0.5 so the runner falls through to later gates rather than
dropping everything.
"""

from __future__ import annotations

import logging
import math

from extended_thinking.algorithms.enrichment.protocol import (
    Candidate,
    GateVerdict,
)
from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


class EmbeddingCosineGate:
    """Filter candidates by cosine similarity vs. the user concept."""

    meta = AlgorithmMeta(
        # `embedding_cosine_gate` (not plain `embedding_cosine`) — the
        # registry is keyed by name alone and `resolution/embedding_cosine`
        # already owns that name for entity-resolution duties.
        name="embedding_cosine_gate",
        family="enrichment.relevance_gates",
        description="Reject below min_similarity, auto-accept above auto_accept.",
        paper_citation="Sentence-BERT (Reimers & Gurevych 2019) — cosine between text embeddings.",
        parameters={
            "min_similarity": 0.72,
            "auto_accept": 0.90,
        },
        temporal_aware=False,
    )

    def __init__(self, min_similarity: float = 0.72, auto_accept: float = 0.90):
        self.min_similarity = min_similarity
        self.auto_accept = auto_accept

    def judge(
        self,
        *,
        concept: dict,
        candidate: Candidate,
        context: AlgorithmContext,
    ) -> GateVerdict:
        vectors = context.vectors
        if vectors is None or not hasattr(vectors, "embed"):
            logger.debug(
                "embedding_cosine: no VectorStore on context; soft-accept"
            )
            return GateVerdict(
                outcome="accept", score=0.5,
                reason="no vector store — deferred to next gate",
            )

        concept_text = _concept_text(concept)
        candidate_text = _candidate_text(candidate)
        if not concept_text or not candidate_text:
            return GateVerdict(
                outcome="reject", score=0.0,
                reason="empty text on one side",
            )

        try:
            embeddings = vectors.embed([concept_text, candidate_text])
        except Exception as e:
            logger.warning("embed() failed, soft-accepting: %s", e)
            return GateVerdict(
                outcome="accept", score=0.5,
                reason=f"embed failed: {e}",
            )
        if len(embeddings) != 2:
            return GateVerdict(outcome="reject", score=0.0,
                               reason="embed returned wrong shape")

        score = _cosine(embeddings[0], embeddings[1])
        if score >= self.auto_accept:
            return GateVerdict(
                outcome="auto_accept", score=score,
                reason=f"cosine={score:.3f} >= auto_accept {self.auto_accept}",
            )
        if score < self.min_similarity:
            return GateVerdict(
                outcome="reject", score=score,
                reason=f"cosine={score:.3f} < min {self.min_similarity}",
            )
        return GateVerdict(
            outcome="accept", score=score,
            reason=f"cosine={score:.3f} in [{self.min_similarity}, {self.auto_accept})",
        )


# ── Helpers ──────────────────────────────────────────────────────────

def _concept_text(concept: dict) -> str:
    """Build a text signature for the concept, name + description."""
    parts: list[str] = []
    for k in ("name", "description", "source_quote"):
        v = concept.get(k)
        if v:
            parts.append(str(v))
    return ". ".join(parts)


def _candidate_text(cand: Candidate) -> str:
    parts = [cand.title] if cand.title else []
    if cand.abstract:
        parts.append(cand.abstract)
    return ". ".join(parts)


def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    dot = sum(ax * bx for ax, bx in zip(a, b))
    na = math.sqrt(sum(ax * ax for ax in a))
    nb = math.sqrt(sum(bx * bx for bx in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


register(EmbeddingCosineGate)
