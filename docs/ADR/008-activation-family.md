# ADR 008: Spreading Activation Family

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 003 (Pluggable Algorithms), ADR 007 (Resolution Family)
**Relates to:** decay family (Physarum integration)

## Context

Spreading activation is the "find related concepts" primitive. Given one or more seed concepts, propagate activation scores through neighbors, decaying with distance. Better than BFS because it respects edge weights and budgets to the sparse set of genuinely activated nodes (cortical sparse coding — a few percent fire strongly at any time).

The logic was previously a method on `GraphStore.spread_activation()`. Same issues as resolution (ADR 007): wrong layer, hardcoded, not swappable. With the pluggable chassis in place, extract it.

## Decision

Create an `activation/` family. First plugin: `weighted_bfs` (ports existing logic).

The plugin takes seed concept IDs via `context.params["seed_ids"]` and returns `list[tuple[concept_id, score]]` sorted by score descending. Seeds start at 1.0 and are excluded from results (already known).

## Integration with decay

This is the first case where a plugin explicitly consumes another plugin. Spreading activation needs edge weights — but not raw weights, Physarum-decayed weights (stale edges contribute less).

Design options considered:

1. **Algorithm calls the decay plugin directly.** What we chose. The plugin imports `PhysarumDecay` and calls `compute_effective_weight()` per edge. Tight coupling to Physarum specifically.

2. **Algorithm gets decay from context.** Would require exposing other plugins through `AlgorithmContext`. Cleaner abstraction but adds complexity before we need it.

3. **Algorithm uses raw weights; decay lives in `effective_weight` on `GraphStore`.** What the old code did. Moves the decay back into storage, defeating the point of the plugin.

Option 1 is pragmatic. When we have a second decay plugin (linear, step function, etc.), we'll upgrade to option 2. For now, Physarum is the only decay and the coupling is acceptable.

The algorithm includes a fast-path for Kuzu (single `MATCH` query returning all edges + timestamps, then decay computed in Python) and a fallback for non-Kuzu stores (iterate concepts, call `kg.effective_weight()` per edge).

## Why not make activation depend on a decay plugin via config

Future-work candidate. The cleanest version:

```python
# Hypothetical context.algorithms attribute
decayer = context.algorithms.get("decay")
w = decayer.compute_effective_weight(base_w, last_accessed)
```

This lets users disable decay for activation (run on raw weights), swap to a different decay function, or run activation against a static snapshot of weights. Worth doing when we add the second decay plugin.

## Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `depth` | 3 | Max BFS rounds. More = wider reach. |
| `decay_per_hop` | 0.7 | Each hop multiplies spread by this. Lower = more localized. |
| `budget` | 100 | Max nodes scored. Prevents runaway in dense graphs. |
| `min_spread` | 0.01 | Below this threshold, don't propagate further. |

Defaults match the pre-plugin `GraphStore.spread_activation()` signature so users don't see behavior changes.

## Consequences

**Positive:**
- Single authoritative implementation. Tests cover both the algorithm and its integration with Physarum decay.
- MCP `et_explore` now routes through the plugin. `GraphStore.spread_activation()` stays for API compat (other consumers) but the pipeline goes through the registry.
- Easy to add sibling plugins: `random_walk_activation` (stochastic), `personalized_pagerank` (iterative to convergence), `temporal_activation` (biased to recent edges).
- Direct test of the decay integration: the `test_stale_edge_contributes_less` test confirms stale edges genuinely activate less.

**Negative:**
- Tight coupling to Physarum (option 1 above). If someone writes a new decay plugin, activation won't automatically use it.
- `GraphStore.spread_activation()` is now effectively dead code (the pipeline doesn't call it). Left in place for backward compat. Future cleanup.
- The Kuzu fast-path duplicates the adjacency-building logic from `GraphStore.spread_activation`. Could dedupe but the algorithm + store have different concerns.

## MCP surface

No new tool. `et_explore` now uses the plugin. Users won't notice a change in behavior — activation output is identical since Physarum parameters match.

Future: an `et_activate "<seed>" depth=N` tool would expose spreading activation directly. Not in scope for this ADR.

## What we don't do

- Mutate edge weights on traversal (Hebbian reinforcement). That happens in `GraphStore.record_edge_access()` when MCP tools traverse paths — separate concern from activation.
- Multi-layer activation (different edge types propagate differently). Could be a future plugin (`typed_activation`).

## References

- Anderson, J. R. (1983). A spreading activation theory of memory. Journal of Verbal Learning and Verbal Behavior 22(3):261-295.
- Collins, A. & Loftus, E. (1975). A spreading-activation theory of semantic processing. Psychological Review 82(6):407-428.
- Crestani, F. (1997). Application of spreading activation techniques in information retrieval. Artificial Intelligence Review 11:453-482.
- ADR 003 (pluggable algorithms), ADR 007 (resolution family as precedent).
