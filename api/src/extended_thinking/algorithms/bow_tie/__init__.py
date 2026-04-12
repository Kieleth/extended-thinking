"""Bow-tie core identification.

Identifies concepts that sit at the convergence point between many inputs
(source chunks) and many outputs (wisdoms, other concepts). These are the
"metabolic center" of the user's thinking — the recurring themes that
many observations flow into and many downstream ideas flow from.

Contrast with:
  - Frequency ranking: a concept can appear often but not be structurally
    central. Bow-tie weight ignores raw frequency.
  - Bridge detection: bridges are high-degree but may have balanced in/out.
    Bow-tie asymmetrically weights concepts with broad convergence + fan-out.
"""

from extended_thinking.algorithms.bow_tie.in_out_degree import InOutDegreeBowTie

__all__ = ["InOutDegreeBowTie"]
