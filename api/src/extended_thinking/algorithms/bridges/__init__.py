"""Rich-club hub detection.

In brain connectomes, a small number of highly-connected "rich-club" hubs
route most inter-region traffic (van den Heuvel & Sporns 2011: 12 bihemispheric
hub regions, ~89% of inter-region shortest paths pass through them).

The same pattern appears in thinking graphs. A few concepts span multiple
semantic clusters and serve as transit points. Identifying them surfaces
the user's structural anchors — the concepts their thinking rests on.

Distinction from bow-tie (ADR 003):
  - bow_tie: core metabolic pattern (high in-degree + high out-degree)
  - bridges: raw connectivity (high overall degree, regardless of direction)
  Same idea manifested differently. A concept can be both.

Plugins:
  - top_percentile: top-N percent by raw degree (simplest, fast)
  - (future) betweenness_centrality: Freeman 1977, more rigorous but O(V*E)
"""

from extended_thinking.algorithms.bridges.top_percentile import TopPercentileBridges

__all__ = ["TopPercentileBridges"]
