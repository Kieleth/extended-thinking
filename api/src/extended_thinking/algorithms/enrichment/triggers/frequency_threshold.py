"""Frequency-threshold trigger (ADR 011 v2).

Fires enrichment for concepts whose frequency (number of chunks the
concept has appeared in) has crossed a threshold. The simplest useful
trigger — don't enrich every passing mention, wait until something
is clearly on the user's mind.

Tuning guide (see EnrichmentRun telemetry for real numbers):
  - min_frequency=1 → very noisy, enriches on first sight
  - min_frequency=3 → MVP default, empirical "this is recurring"
  - min_frequency=5 → strict, most concepts never get enriched

This is one strategy among many. See ADR 011 v2 for the sketch of
future triggers (cluster_formed, rising_activation, on_wisdom, etc.).
"""

from __future__ import annotations

import logging

from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


class FrequencyThresholdTrigger:
    """Fire for concepts where frequency >= min_frequency."""

    meta = AlgorithmMeta(
        name="frequency_threshold",
        family="enrichment.triggers",
        description="Fire when concept.frequency crosses a threshold.",
        paper_citation="n/a (heuristic; empirically tuned via EnrichmentRun telemetry)",
        parameters={"min_frequency": 3, "max_concepts_per_run": 20},
        temporal_aware=False,
    )

    def __init__(self, min_frequency: int = 3, max_concepts_per_run: int = 20):
        self.min_frequency = min_frequency
        self.max_concepts_per_run = max_concepts_per_run

    def fired_concepts(
        self, context: AlgorithmContext,
    ) -> list[tuple[str, str]]:
        kg = context.kg
        ns = context.namespace
        if not hasattr(kg, "list_concepts"):
            return []

        # Pull enough to catch the frequent ones; cap on output.
        concepts = kg.list_concepts(
            order_by="frequency", limit=500, namespace=ns,
        )
        fired: list[tuple[str, str]] = []
        for c in concepts:
            freq = c.get("frequency", 0) or 0
            if freq < self.min_frequency:
                continue
            fired.append((
                c["id"],
                f"frequency={freq} >= {self.min_frequency}",
            ))
            if len(fired) >= self.max_concepts_per_run:
                break
        logger.info(
            "frequency_threshold trigger: %d concepts fired "
            "(threshold=%d, namespace=%s)",
            len(fired), self.min_frequency, ns,
        )
        return fired


register(FrequencyThresholdTrigger)
