"""Enrichment source plugins (ADR 011 v2).

MVP source (lands in Phase B.1): wikipedia. arxiv, fowler, semantic_scholar,
custom_canon follow once the plugin boundary is vetted.

Each source plugin registers via the algorithms.registry module so it
becomes discoverable via `get_active("enrichment.sources", config)`.
"""
