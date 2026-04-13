"""Time-to-refresh cache policy (ADR 011 v2).

Given an external_id + its last-fetched timestamp, decide whether to
refetch. Per-source overrides let users keep arXiv abstracts forever
(they're immutable) while refreshing Wikipedia every 90 days.

Design note (ADR 011 v2 user call): Wikipedia refresh is 90 days, not
7. Most article content relevant to insight-making doesn't move that
fast; save the rate limit. On-demand refresh via `et_extend_force`
covers the cases where the user needs a specific article refreshed now.
"""

from __future__ import annotations

from datetime import datetime, timezone

from extended_thinking.algorithms.enrichment.protocol import EnrichmentCachePlugin
from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta
from extended_thinking.algorithms.registry import register


_DEFAULT_REFRESH_DAYS = 30
_PER_SOURCE_DEFAULTS: dict[str, int | None] = {
    "wikipedia": 90,
    "arxiv": None,        # None = 'never' — arXiv ids are immutable
    "fowler": None,        # Curated catalog; changes only via plugin update
    "semantic_scholar": 180,
}


class TimeToRefreshCache:
    """Refetch records older than N days, with per-source overrides."""

    meta = AlgorithmMeta(
        name="time_to_refresh",
        family="enrichment.cache",
        description="Refetch when last_fetched is older than refresh_after_days.",
        paper_citation="n/a (caching policy)",
        parameters={
            "refresh_after_days": _DEFAULT_REFRESH_DAYS,
            "per_source_days": {},
        },
        temporal_aware=True,
    )

    def __init__(
        self,
        refresh_after_days: int = _DEFAULT_REFRESH_DAYS,
        per_source_days: dict | None = None,
    ):
        self.refresh_after_days = refresh_after_days
        # User-supplied overrides merge on top of built-in defaults.
        self.per_source_days: dict[str, int | None] = dict(_PER_SOURCE_DEFAULTS)
        if per_source_days:
            self.per_source_days.update(per_source_days)

    def is_stale(
        self,
        *,
        external_id: str,
        last_fetched: datetime,
        source_kind: str,
        context: AlgorithmContext,
    ) -> bool:
        policy = self.per_source_days.get(source_kind, self.refresh_after_days)
        if policy is None:
            # 'never' — arXiv abstracts, curated canons, anything
            # the user considers immutable at the ID level.
            return False
        now = context.now or datetime.now(timezone.utc)
        # Both sides must be tz-aware; ADR 013 stores ISO strings with tz.
        age = (now - last_fetched).total_seconds() / 86400.0
        return age >= policy


register(TimeToRefreshCache)
