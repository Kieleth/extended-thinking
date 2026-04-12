# ADR 009: Rich-Club Bridges Family

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 003 (Pluggable Algorithms)
**Completes:** extraction of inline heuristics from GraphStore into plugins

## Context

Bridge detection identifies rich-club hubs — concepts with disproportionately high degree that span multiple semantic clusters. In brain connectomes, ~12 hub regions route ~89% of inter-region shortest paths (van den Heuvel & Sporns 2011). In thinking graphs, the pattern recurs: a few concepts anchor most of the structure.

Until this ADR, bridge detection was inline in `GraphStore.get_graph_overview()`:
```python
# Bridges: top 10% by degree, capped at min threshold
sorted_degrees = sorted(degree.values(), reverse=True)
top_10_percent = max(1, len(sorted_degrees) // 10)
threshold = max(sorted_degrees[top_10_percent - 1], 5)
bridges = [concept_map[nid] for nid, d in degree.items()
           if d >= threshold and nid in concept_map]
```

Hardcoded threshold, hardcoded floor, no way to swap for a better algorithm (e.g., betweenness centrality, which is more rigorous but expensive). With all other algorithm families extracted (ADRs 007, 008), this is the last remaining inline heuristic.

## Decision

Extract bridge detection into a `bridges/` family plugin. First implementation: `top_percentile` (ports existing logic, parameterizes the two magic numbers).

`GraphStore.get_graph_overview()` delegates to the registered plugin. If no plugin is registered (e.g., the `algorithms/` module isn't importable), falls back to an empty bridges list without crashing.

## Why `top_percentile` first

- **Preserves existing behavior.** Same algorithm, same defaults (10% percentile, min degree 5). No user-visible change.
- **Zero new dependencies.** Pure stdlib.
- **Fast.** O(V + E) aggregation via Cypher, O(V log V) sort.
- **Good enough at our scale.** Below ~1000 concepts, raw degree is a perfectly adequate proxy for "bridge-ness." Betweenness centrality matters at larger scales.

## Room for siblings

The family is sized for multiple implementations:

- **`betweenness_centrality`** — Freeman 1977, the rigorous approach. Counts the fraction of shortest paths passing through each node. More expensive (O(V·E) with Brandes' algorithm) but catches brokers that low-degree ground-truths would miss.
- **`rich_club_coefficient`** — Colizza et al. 2006. Measures the density of edges among high-degree nodes, distinguishing genuine rich-clubs from random degree concentration.
- **`eigenvector_centrality`** / **`pagerank`** — weighted by connection quality, not just count.

Each is a plugin, each cites its research, each respects the protocol.

## Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `percentile` | 0.10 | Top-N percentile by degree. Lower = stricter. |
| `min_degree` | 5 | Floor: nothing below this is a bridge, even in sparse graphs. |

The floor matters: in a tiny graph where max degree is 3, "top 10%" could still label degree-3 nodes as bridges. The floor prevents that trivial case.

## Delegation pattern

The new `_compute_bridges_via_plugin()` on `GraphStore` is the pattern for future storage-layer delegations. Key choices:

1. **Late import** of `algorithms` to avoid circular imports (algorithms import storage, storage may import algorithms).
2. **Graceful fallback** when no plugin is registered — returns empty list, doesn't crash.
3. **First-plugin-wins** semantics. If multiple bridge plugins are enabled, the first takes effect. Users who want to compose results should write a composite plugin.
4. **Preserves return shape** for `get_graph_overview()`: returns bare concept dicts, not the richer `{"concept": ..., "degree": ...}` shape from the plugin. Avoids breaking existing MCP tools.

## Consequences

**Positive:**
- Last inline heuristic extracted. The K+W layer is now fully pluggable.
- `get_graph_overview()` stays backward-compatible; users see no behavior change unless they configure a different bridge algorithm.
- Easy to contribute new bridge algorithms (betweenness, rich-club coefficient) without touching storage.
- Tests cover the full chain: plugin in isolation, plugin via registry, delegation via `get_graph_overview`.

**Negative:**
- Every `get_graph_overview()` call now pays the plugin-resolution overhead (import, registry lookup). Negligible, but non-zero.
- The delegation point is in `GraphStore`, which means the storage layer is slightly aware of the algorithms layer. This is a controlled leak: the import is late, the dependency is optional (fallback handles missing), and it's the only such leak.

**Non-consequences:**
- No new MCP tool. The bridge list continues to appear in `et_graph`'s overview section.

## What we don't do

- Cache bridge results across `get_graph_overview()` calls. Bridges change as edges change; caching would stale. Future optimization if needed.
- Surface per-concept "bridge score" on explore. Could come later if useful.
- Auto-warn when a bridge concept is also a bow-tie core (interesting overlap but separate analysis).

## References

- van den Heuvel, M. P. & Sporns, O. (2011). Rich-club organization of the human connectome. Journal of Neuroscience 31(44):15775-15786.
- Colizza, V., Flammini, A., Serrano, M. A. & Vespignani, A. (2006). Detecting rich-club ordering in complex networks. Nature Physics 2:110-115.
- Bassett, D. S. & Bullmore, E. T. (2017). Small-world brain networks revisited. The Neuroscientist 23(5):499-516.
- Freeman, L. C. (1977). A set of measures of centrality based on betweenness. Sociometry 40(1):35-41. (For the future `betweenness_centrality` sibling.)
- ADR 003 (pluggable algorithms), ADR 007 (resolution), ADR 008 (activation) — same extraction pattern.
