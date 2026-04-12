"""Hypothesis properties for the decay family.

Physarum decay is a read-time transform, not a batch operation. The
algorithm's `run()` method is a no-op; `compute_effective_weight()` does
the real work and is what `recency_weighted` and `weighted_bfs` call in
production.

These properties exercise `compute_effective_weight()` directly so the
assertions prove something. A no-op `run()` can't violate monotonicity, so
testing against it is theater.

Properties:
  - monotonic_decay_with_idle_days: effective weight is non-increasing as
    idle time grows.
  - non_negative: output is always >= 0.
  - zero_base_is_fixed_point: base_weight=0 returns 0.
  - fresh_access_preserves_weight: idle=0 returns base (modulo decay_rate^0).
  - source_age_aware_uses_older: when both access and source ages exist,
    the output matches running with the greater age alone.
  - empty_timestamps_returns_base: no access and no source info => base.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from extended_thinking.algorithms.decay.physarum import PhysarumDecay

pytestmark = pytest.mark.acceptance

FIXED_NOW = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)


def _iso_days_ago(days: float) -> str:
    return (FIXED_NOW - timedelta(days=days)).isoformat()


base_st = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
days_st = st.floats(min_value=0.0, max_value=365.0, allow_nan=False, allow_infinity=False)
rate_st = st.floats(min_value=0.50, max_value=0.999, allow_nan=False, allow_infinity=False)


@given(base=base_st, d1=days_st, d2=days_st, rate=rate_st)
@settings(max_examples=40, deadline=1000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_monotonic_decay_with_idle_days(base, d1, d2, rate):
    """More idle time produces less (or equal) effective weight."""
    decay = PhysarumDecay(decay_rate=rate, source_age_aware=False)
    w1 = decay.compute_effective_weight(base, _iso_days_ago(d1), now=FIXED_NOW)
    w2 = decay.compute_effective_weight(base, _iso_days_ago(d2), now=FIXED_NOW)
    if d1 <= d2:
        assert w1 >= w2 - 1e-9, f"d1={d1} d2={d2} w1={w1} w2={w2}"
    else:
        assert w2 >= w1 - 1e-9, f"d1={d1} d2={d2} w1={w1} w2={w2}"


@given(base=base_st, d=days_st, rate=rate_st)
@settings(max_examples=30, deadline=1000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_effective_weight_is_non_negative(base, d, rate):
    decay = PhysarumDecay(decay_rate=rate, source_age_aware=False)
    w = decay.compute_effective_weight(base, _iso_days_ago(d), now=FIXED_NOW)
    assert w >= 0.0, f"negative weight: {w} (base={base}, d={d}, rate={rate})"


@given(d=days_st, rate=rate_st)
@settings(max_examples=15, deadline=1000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_zero_base_is_fixed_point(d, rate):
    decay = PhysarumDecay(decay_rate=rate, source_age_aware=False)
    w = decay.compute_effective_weight(0.0, _iso_days_ago(d), now=FIXED_NOW)
    assert w == 0.0, f"0-base should stay 0, got {w}"


@given(base=base_st, rate=rate_st)
@settings(max_examples=20, deadline=1000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_fresh_access_preserves_weight(base, rate):
    """idle_days=0 => effective == base (decay_rate ** 0 == 1)."""
    decay = PhysarumDecay(decay_rate=rate, source_age_aware=False)
    w = decay.compute_effective_weight(base, _iso_days_ago(0), now=FIXED_NOW)
    assert w == pytest.approx(base, abs=1e-9), f"fresh access should equal base, got {w} vs {base}"


@given(base=base_st, da=days_st, ds=days_st, rate=rate_st)
@settings(max_examples=30, deadline=1000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_source_age_aware_uses_older(base, da, ds, rate):
    """With source_age_aware=True, the effective weight matches running the
    access-only path against the GREATER of (access_age, source_age)."""
    aware = PhysarumDecay(decay_rate=rate, source_age_aware=True)
    access_only = PhysarumDecay(decay_rate=rate, source_age_aware=False)

    w_aware = aware.compute_effective_weight(
        base, _iso_days_ago(da), now=FIXED_NOW, t_valid_from=_iso_days_ago(ds),
    )
    older = max(da, ds)
    w_via_older = access_only.compute_effective_weight(
        base, _iso_days_ago(older), now=FIXED_NOW,
    )
    assert w_aware == pytest.approx(w_via_older, abs=1e-9), (
        f"source-aware should match access-only on max(da={da}, ds={ds}): "
        f"aware={w_aware} vs via-older={w_via_older}"
    )


@given(base=base_st, rate=rate_st)
@settings(max_examples=15, deadline=1000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_empty_timestamps_returns_base(base, rate):
    """No access info AND source_age_aware but no source info => base weight."""
    decay = PhysarumDecay(decay_rate=rate, source_age_aware=True)
    w = decay.compute_effective_weight(base, last_accessed="", now=FIXED_NOW, t_valid_from="")
    assert w == base, f"no timestamp info should return base, got {w} vs {base}"
