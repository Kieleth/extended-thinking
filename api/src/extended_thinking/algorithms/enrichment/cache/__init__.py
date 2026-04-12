"""Enrichment cache policy plugins (ADR 011 v2).

MVP (lands in Phase B.4): time_to_refresh with per-source override
(30d default, 90d Wikipedia, 'never' arXiv). Supersession, not delete
— purging a source marks rows t_expired so as-of queries still see
what was there.
"""
