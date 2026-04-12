"""Activity score family — compute top-k active concepts.

Replaces the inline freq * recency * sqrt(degree) formula in GraphStore.
Swappable so users can try alternative scoring without touching storage.
"""

from extended_thinking.algorithms.activity_score import recency_weighted  # noqa: F401
