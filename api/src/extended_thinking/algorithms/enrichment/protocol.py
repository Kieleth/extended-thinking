"""Enrichment plugin protocols (ADR 011 v2).

Four contracts. Each descends in spirit from the Algorithm protocol of
ADR 003 (meta + run shape) but the `run` signature varies per family
because the interactions differ (sources fetch, triggers decide, gates
judge, cache times).

Registration flows through the same `extended_thinking.algorithms.registry`
so every plugin is discoverable via `get_by_name` / `get_active` and
toggleable via `[algorithms.enrichment.<family>.<name>]` in TOML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta


# ── Shared value objects ─────────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    """A single fetched item a source returns per concept.

    Sources translate their native payload (Wikipedia JSON, arXiv XML,
    Fowler markdown) into this shape so triggers, gates, and the runner
    don't have to know about protocol specifics.
    """

    external_id: str
    """Source-native id (Wikidata QID, arXiv paper id, Fowler slug)."""

    title: str

    abstract: str
    """Text indexed for semantic recall; also fed to LLM gates."""

    url: str = ""
    """Deep link back to the source."""

    themes: list[str] = field(default_factory=list)
    """Theme tags from the source taxonomy (or LLM classifier). See
    ADR 011 v2 — multi-theme membership allowed."""

    source_kind: str = ""
    """The source plugin's canonical name ('wikipedia', 'arxiv', ...).
    Filled in by the runner if the source plugin leaves it blank."""

    raw: dict = field(default_factory=dict)
    """Opaque source-specific extras for debugging. Not persisted in
    the KnowledgeNode; only the structured fields above are."""


@dataclass(frozen=True)
class GateVerdict:
    """A relevance gate's decision about a candidate.

    Gates run cheapest-first. The runner short-circuits on `reject` or
    `auto_accept`; otherwise it passes through to the next gate carrying
    the score forward.
    """

    outcome: Literal["accept", "auto_accept", "reject"]
    score: float
    """0..1 similarity / relevance score. Carried between gates."""

    reason: str = ""
    """Human-readable (and LLM-readable) explanation. Persisted when the
    gate is LLM-based and wants a Rationale trail (ADR 011 v2 uses
    et_write_rationale for that)."""

    plugin_name: str = ""
    """Filled by the runner."""


# ── Family protocols ─────────────────────────────────────────────────

@runtime_checkable
class EnrichmentSourcePlugin(Protocol):
    """External fetcher. Given a user concept, return candidate items.

    Rate limiting, retries, and auth live inside the source — the
    runner treats source.search() as the only external dependency.
    """

    meta: AlgorithmMeta  # name, family="enrichment.sources", description, paper_citation

    def source_kind(self) -> str:
        """Canonical tag used for namespace (`enrichment:<source_kind>`)
        and for the KnowledgeNode.source_kind column. Usually equal to
        `meta.name` but separated so plugin names and source tags can
        diverge if needed."""
        ...

    def search(
        self,
        *,
        concept_id: str,
        concept_name: str,
        concept_description: str,
        context: AlgorithmContext,
    ) -> list[Candidate]:
        """Fetch candidates. Return [] on transient failures — the
        runner records an EnrichmentRun with the error string so retry
        logic can query and act."""
        ...


@runtime_checkable
class EnrichmentTriggerPlugin(Protocol):
    """Decides which concepts, if any, deserve enrichment this cycle.

    Triggers don't fetch; they only surface candidate concept ids + a
    reason string. The runner takes it from there.
    """

    meta: AlgorithmMeta  # name, family="enrichment.triggers", ...

    def fired_concepts(
        self,
        context: AlgorithmContext,
    ) -> list[tuple[str, str]]:
        """Return (concept_id, reason) pairs for concepts that should
        trigger enrichment this cycle. Empty list means "don't fire."
        """
        ...


@runtime_checkable
class EnrichmentGatePlugin(Protocol):
    """Decides whether a candidate belongs attached to a concept.

    Multiple gates run in order; first decisive verdict wins. Gates
    never fetch — they only filter. Separation of concerns: sources
    know where to look, gates know what's relevant.
    """

    meta: AlgorithmMeta  # name, family="enrichment.relevance_gates", ...

    def judge(
        self,
        *,
        concept: dict,
        candidate: Candidate,
        context: AlgorithmContext,
    ) -> GateVerdict:
        """Return an accept / auto_accept / reject verdict with a score
        and optional reason string. The runner short-circuits on reject
        or auto_accept."""
        ...


@runtime_checkable
class EnrichmentCachePlugin(Protocol):
    """Decides if a candidate needs refetching.

    The cache plugin never fetches either — it just answers "is this
    stored record still fresh?" based on timestamps and source-specific
    policy.
    """

    meta: AlgorithmMeta  # name, family="enrichment.cache", ...

    def is_stale(
        self,
        *,
        external_id: str,
        last_fetched: datetime,
        source_kind: str,
        context: AlgorithmContext,
    ) -> bool:
        """Return True if this record should be refetched (or if it
        was never fetched)."""
        ...


# ── Helpers the runner uses ──────────────────────────────────────────

def assert_family(plugin: Any, expected_family: str) -> None:
    """Guard: the runner only picks up plugins whose meta.family
    matches. A plugin registered under the wrong family would cause
    cryptic downstream errors; we fail loudly here instead."""
    meta = getattr(plugin, "meta", None)
    family = getattr(meta, "family", None) if meta else None
    if family != expected_family:
        raise TypeError(
            f"{plugin.__class__.__name__} has meta.family={family!r}; "
            f"runner expected {expected_family!r}. Fix the plugin's meta."
        )
