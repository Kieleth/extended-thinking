"""Spreading activation: ranked "find related" over the graph.

Given seed concepts, spreading activation propagates activation scores
through neighbors, decaying with distance. It's strictly better than BFS
for "find related" because it respects edge weights (Physarum-decayed,
so stale edges contribute less) and budgets to sparse active sets.

Plugins in this family:
  - weighted_bfs: iterative neighbor propagation, weighted by edge strength
    (future: random_walk, personalized_pagerank, etc.)

Each plugin takes seed concept IDs in `context.params["seed_ids"]` and
returns a ranked list of (concept_id, activation_score) tuples.

Research:
  Anderson (1983). A spreading activation theory of memory.
    Journal of Verbal Learning and Verbal Behavior 22(3):261-295.
  Collins & Loftus (1975). A spreading-activation theory of semantic processing.
    Psychological Review 82(6):407.
  Hindsight (2026). Temporal spreading activation for memory graphs.
"""

from extended_thinking.algorithms.activation.weighted_bfs import (
    WeightedBFSActivation,
)

__all__ = ["WeightedBFSActivation"]
