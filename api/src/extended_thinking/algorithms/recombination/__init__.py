"""Default Mode Network-inspired recombination.

The brain's Default Mode Network (DMN) activates during mind-wandering,
running background recombination of stored concepts without executive
filtering. Creativity correlates with dynamic switching between DMN
(free association) and executive control (grounding).

Recombination algorithms pick concepts from different clusters and ask
"is there a real bridge here?" — not by retrieval (we already know the
existing edges) but by creative synthesis. The LLM plays the DMN role;
our job is to feed it structurally distant concepts with enough context
for a grounded verdict.

Unlike wisdom generation, recombination outputs candidate connections
for user review — not committed edges. Grounding failures surface as
explicit speculation ("would require X to become real") instead of
hallucinated bridges.
"""

from extended_thinking.algorithms.recombination.cross_cluster_grounded import (
    CrossClusterGroundedRecombination,
)

__all__ = ["CrossClusterGroundedRecombination"]
