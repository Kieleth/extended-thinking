"""Enrichment plugin families (ADR 011 v2).

Four kinds of plugins compose the enrichment pipeline:

  sources/         — who we fetch from (Wikipedia, arXiv, Fowler, ...)
  triggers/        — when we fire (frequency threshold, cluster formed, ...)
  relevance_gates/ — does this candidate belong (embedding cosine, LLM judge, ...)
  cache/           — freshness policy (time-to-refresh, never, on-access)

Each follows the ADR 003 Algorithm protocol family pattern. The runner
(enrichment/runner.py) composes them into the full pipeline.

MVP ships with one default in each family — every other slot is a
plugin waiting for its need to materialize. Consumers add strategies
via the same entry-points mechanism.
"""

from extended_thinking.algorithms.enrichment.protocol import (  # noqa: F401
    Candidate,
    EnrichmentCachePlugin,
    EnrichmentGatePlugin,
    EnrichmentSourcePlugin,
    EnrichmentTriggerPlugin,
    GateVerdict,
)
