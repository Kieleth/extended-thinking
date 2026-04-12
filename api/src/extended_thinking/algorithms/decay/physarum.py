"""Physarum-inspired edge decay.

Based on the Physarum polycephalum (slime mold) conductance equation:
    dD/dt = f(|Q|) - rD

Where D is tube conductance, Q is flow, r is decay rate. High-flow tubes
thicken (positive feedback). Idle tubes fade. In our adaptation:

    effective_weight = base_weight * decay_rate ^ days_since_last_access

This is a discrete, read-time version of the continuous Physarum process.
The original base_weight stays in storage; decay is computed on demand.

Reference:
    Tero, A. et al. (2010). Rules for biologically inspired adaptive
    network design. Science, 327(5964), 439-442.
    Scientific Reports 2025: Physarum-inspired decentralized mesh networks.
"""

from __future__ import annotations

from datetime import datetime

from extended_thinking.algorithms.protocol import (
    Algorithm,
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register


class PhysarumDecay:
    """Read-time edge decay by exponential function of idle time.

    Given an edge with base weight W, last-accessed timestamp T_a, and
    source-time T_s (when the evidence was written), the effective weight
    at time t is:

        idle_days = (t - T_a) / day                        (access-only)
        idle_days = max((t - T_a), (t - T_s)) / day        (source-age-aware)
        W_eff = W * decay_rate ^ max(0, idle_days)

    Why source-age-aware matters: a sync re-touches `last_accessed` even
    for edges whose evidence is years old. Without source awareness, old
    conversations look "fresh" right after ingest. With it, the edge
    starts decayed to reflect the real age of its evidence.

    Does not mutate storage — decay is pure read-time computation.
    """

    meta = AlgorithmMeta(
        name="physarum",
        family="decay",
        description="Exponential edge decay by idle days (slime-mold conductance model)",
        paper_citation="Tero et al. 2010, Science 327:439. Scientific Reports 2025.",
        parameters={
            "decay_rate": 0.95,           # 5% decay per day
            "source_age_aware": True,     # honor t_valid_from, not just last_accessed
        },
        temporal_aware=True,
    )

    def __init__(self, decay_rate: float = 0.95, source_age_aware: bool = True):
        self.decay_rate = decay_rate
        self.source_age_aware = source_age_aware

    def run(self, context: AlgorithmContext) -> None:
        """Physarum decay is a read-time transform, not a batch operation.

        The run() method is a no-op. Use compute_effective_weight() directly.
        (Keeping this interface for registry uniformity.)
        """
        return None

    def compute_effective_weight(
        self,
        base_weight: float,
        last_accessed: str,
        now: datetime | None = None,
        t_valid_from: str = "",
    ) -> float:
        """Apply Physarum decay formula.

        Args:
            base_weight: the stored edge weight (unchanged by decay).
            last_accessed: ISO timestamp of last access ("" if never).
            now: evaluation time (defaults to current UTC).
            t_valid_from: ISO timestamp of when the edge's evidence was
                written. When source_age_aware is True, the effective
                age is max(now - last_accessed, now - t_valid_from).

        Returns:
            Decayed weight (>=0). If never accessed and no source-age info,
            returns base_weight.
        """
        from datetime import timezone as _tz
        now = now or datetime.now(_tz.utc)

        access_days = _days_since(last_accessed, now)
        if not self.source_age_aware:
            if access_days is None:
                return base_weight
            return base_weight * (self.decay_rate ** max(0.0, access_days))

        source_days = _days_since(t_valid_from, now)
        # No usable timestamps at all: return base.
        if access_days is None and source_days is None:
            return base_weight
        # Source-age-aware: age is the older of the two signals.
        a = access_days if access_days is not None else 0.0
        s = source_days if source_days is not None else 0.0
        idle = max(0.0, a, s)
        return base_weight * (self.decay_rate ** idle)


def _days_since(iso_ts: str, now: datetime) -> float | None:
    """Return days between `iso_ts` and `now`, or None if unparseable/empty."""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return None
    return (now - dt).total_seconds() / 86400.0


register(PhysarumDecay)
