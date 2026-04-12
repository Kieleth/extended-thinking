"""Edge weight decay algorithms.

Each implementation computes a time-decayed effective weight for an edge.
The original (base) weight stays in storage. Decay is a READ-TIME
transform — computed when needed, not stored.

Decay algorithms accept a context with an edge reference and return a
float. Most traversal algorithms (spreading activation, path finding)
consume decay results to rank edges.
"""

from extended_thinking.algorithms.decay.physarum import PhysarumDecay

__all__ = ["PhysarumDecay"]
