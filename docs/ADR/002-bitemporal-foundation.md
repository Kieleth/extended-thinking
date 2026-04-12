# ADR 002: Bitemporal Foundation

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 001 (Pluggable Memory)
**Informs:** ADR 003 (Pluggable Algorithms), future temporal algorithm ADRs

## Context

Until now, extended-thinking stored timestamps on records (`first_seen`, `last_accessed`, `created_at`) but treated the graph as always-current state. This is timestamped storage, not a temporal knowledge graph.

The distinction matters. A timestamped store answers "when was this recorded?" A temporal graph answers:

- "What did we know on April 10?" (system-time reconstruction)
- "What was true in the world between March and April?" (valid-time window)
- "This fact contradicts an earlier one — which one is current?" (supersession)
- "How has the graph changed between these dates?" (diff)
- "Your thinking about X shifted from Y to Z on this date" (change detection)

Every nature-inspired algorithm we want to run — Physarum decay, Hebbian reinforcement, memory consolidation (sleep replay), bow-tie core identification, DMN recombination — has an implicit temporal dimension. Without real temporal semantics, these algorithms are approximations at best and meaningless at worst.

The MemPalace audit made this concrete. MemPalace has `valid_from`/`valid_to` columns but never auto-invalidates contradictions, never reconstructs point-in-time state, and calls itself a "temporal KG." We don't want to be that.

## Decision

Adopt a **bitemporal model** as the foundation for all graph state. Every edge carries four timestamps:

| Timestamp | Meaning |
|-----------|---------|
| `t_valid_from` | When the fact became true in the world |
| `t_valid_to` | When the fact stopped being true (NULL = still true) |
| `t_created` | When the system learned/recorded the fact |
| `t_expired` | When the system marked it invalid (NULL = still live) |

Plus `t_superseded_by`: optional edge ID pointing to the fact that replaced this one.

## Bitemporal semantics

The two axes separate **world time** from **system time**:

- **Valid time** (`t_valid_from` / `t_valid_to`): when the fact holds in reality. If you decided to use Kuzu on April 11, that edge has `t_valid_from = "2026-04-11"`. If you change your mind on May 1, `t_valid_to = "2026-05-01"` — the fact was true for that interval.

- **Transaction time** (`t_created` / `t_expired`): when the system holds the fact. You decided on Kuzu on April 11 but we extracted the concept on April 12 (when you synced). `t_created = "2026-04-12"`. The fact existed in the world before the system knew it.

This lets us answer four distinct questions:

1. "What did the system know on date X?" → filter by `t_created <= X AND (t_expired > X OR t_expired IS NULL)`
2. "What was true in the world on date X?" → filter by `t_valid_from <= X AND (t_valid_to > X OR t_valid_to IS NULL)`
3. "What did we believe was true on date X?" → both filters combined
4. "What's currently true and known?" → all four current (the default view)

Examples where the axes diverge:

- Retroactive correction: user tells you on May 1 that they actually decided on March 15. `t_valid_from = "2026-03-15"`, `t_created = "2026-05-01"`.
- Stale belief: system recorded a fact that was already wrong when captured. We can mark `t_expired` without touching `t_valid_to`.

## Contradiction detection and supersession

Extraction now includes a supersession check. When a new edge is created, the extractor (via Haiku) identifies whether any existing edge is logically contradicted. If so:

1. Set `t_valid_to` on the old edge to the new edge's `t_valid_from`.
2. Set `t_expired` on the old edge to now.
3. Set `t_superseded_by` on the old edge to the new edge's ID.

The graph carries the history explicitly. Queries filter to current by default but can walk the supersession chain to reconstruct evolution ("how did this decision evolve?").

## Scope

Bitemporal timestamps apply to:

- `RELATES_TO` edges (concept-to-concept relationships)
- `HAS_PROVENANCE` edges (concept-to-chunk provenance)
- `INFORMED_BY` edges (wisdom-to-concept)
- `SAME_AS` bridges (cross-store identity)

Nodes get a subset:

- `Concept`: `t_first_observed`, `t_last_observed`, `t_deprecated` (nullable)
- `Wisdom`: `t_generated`, `t_relevant_from`, `t_relevant_to`
- `Chunk`: `t_source_created` (user wrote it), `t_ingested` (ET saw it)

Chunks don't need the full bitemporal treatment because they're immutable artifacts of the past, not evolving facts.

## Why bitemporal, not event-sourced

Event sourcing (every mutation as an immutable event, current state derived by replay) is stronger: perfect historical reconstruction, no lost information. We explicitly chose not to event-source for three reasons:

1. Storage cost scales linearly with mutation count. At ET's scale (thousands of concepts, tens of thousands of edges, ongoing drift), this compounds fast.
2. Kuzu is a graph database, not an event store. Forcing event sourcing on top adds an architectural layer for minimal benefit.
3. Bitemporal with `t_superseded_by` gives us 90% of the benefit (can reconstruct any past view reachable through supersession chains) at 10% of the complexity.

If someone later wants event sourcing, they can write a plugin that records events alongside bitemporal updates. The bitemporal schema doesn't prevent it.

## Synergies with nature-inspired algorithms

Every research principle benefits from bitemporal semantics:

- **Physarum decay** becomes bitemporal: edge weight decays on both (a) time since traversal and (b) time since `t_valid_to`. An edge that became invalid 30 days ago fades even if accessed today, because its reality has moved on.

- **Hebbian reinforcement**: access tracking resolves which interpretations stay valid. Traversing an edge now strengthens `weight` but doesn't resurrect `t_valid_to`.

- **Memory consolidation (sleep replay)**: "recent" is `t_created` within some window. Recent episodic content consolidates into semantic. "Old" concepts accessed infrequently become candidates for archival.

- **Bow-tie core identification**: compute core concepts as of date X. Core evolves over time. "Your core themes today" vs "6 months ago" is a point-in-time query, not a separate dataset.

- **DMN recombination**: when picking two concepts from different clusters for creative recombination, the system can prefer fresh ones, or deliberately mix fresh+archival for cross-temporal insight.

- **Rich-club hubs**: hubs at time X may not be hubs at time Y. Point-in-time queries give accurate instantaneous structure.

- **Entity resolution merges**: merge events timestamped with `t_valid_from` on the SAME_AS edge. Pre-merge state is reconstructable.

## Query API

Add `as_of` parameter to traversal methods:

```python
kg.find_path(from_id, to_id, as_of=None)  # default: now
kg.find_path(from_id, to_id, as_of="2026-03-15")  # point-in-time
kg.get_neighborhood(node_id, as_of=None)
kg.list_concepts(as_of=None)
```

`as_of` filters by valid-time by default. For transaction-time queries, use `as_of_system`. Both NULL = current state.

New method:

```python
kg.diff(date1, date2) -> ChangeSet
# Returns concepts added/removed, edges added/removed/expired, clusters that shifted
```

## Migration path

Existing Kuzu schema has `valid_from`, `valid_to` but no `t_created`/`t_expired`/`t_superseded_by`. Migration:

1. `ALTER TABLE RELATES_TO ADD t_created STRING`
2. `ALTER TABLE RELATES_TO ADD t_expired STRING`
3. `ALTER TABLE RELATES_TO ADD t_superseded_by STRING`
4. Backfill: for existing edges, `t_created = first_seen_of_source_concept`, `t_expired = NULL`, `t_superseded_by = ""`.
5. Rename existing `valid_from` → `t_valid_from`, `valid_to` → `t_valid_to` (Kuzu may need recreate-and-copy since renames aren't universally supported).

Since we're wiping data anyway for each sync iteration during development, a clean recreate is simplest right now. Real migration logic is needed only for persisted graphs.

## Implementation order

1. Schema update (drop + recreate since we're in early dev).
2. Pipeline writes: every edge creation populates all 4 timestamps. `t_valid_from = t_created = now` by default; the extractor can override `t_valid_from` if a document specifies a different world-time date.
3. Extractor supersession check: Haiku prompt asks "does this new claim contradict any existing edge? If so, return supersedes: {edge_id, reason}".
4. Store `valid_to` / `expired` / `superseded_by` on contradicted edges when extraction returns supersession.
5. `as_of(date)` query support in GraphStore.
6. `diff(date1, date2)` support.
7. Update Physarum decay to use bitemporal axes (valid-time + access-time).
8. MCP tool: `et_shift` ("what changed between these dates?").

## Consequences

**Positive:**
- Honest temporal KG, not a timestamped store pretending to be one.
- Historical queries become trivial (one param).
- Contradictions carry forward as explicit evolution, not silent accumulation.
- Every future algorithm can opt into temporal reasoning through a standard parameter.
- Opens downstream features: change detection, drift analysis, trend surfacing.

**Negative:**
- Schema is more complex (4 timestamps vs 2).
- Extraction gets one more LLM responsibility (supersession check). Adds latency.
- Supersession judgments are probabilistic (Haiku may miss contradictions or invent them). Requires tuning.
- Writers must remember to populate all 4 timestamps. Easy to introduce bugs if a code path skips one.

**Mitigations:**
- `GraphStore.add_edge()` populates timestamps automatically. No caller should pass them in directly.
- Supersession errors are recoverable: edges can be manually re-validated or superseded. False supersessions can be undone by nulling `t_expired`.
- Test coverage includes temporal invariants (no edge has `t_expired < t_created`, no edge has `t_valid_to < t_valid_from`, etc.).

## References

- Graphiti paper (arxiv 2501.13956): bitemporal model for agent memory KGs
- Allen's interval algebra: formal treatment of temporal intervals and their relations
- Jensen & Snodgrass, "Temporal Data Management" (IEEE TKDE 1999): the canonical bitemporal model
- Kimball, "The Data Warehouse Toolkit" — Type-2 SCD (Slowly Changing Dimensions) is bitemporal in disguise
- Zep's open-source Graphiti: production reference implementation
- MemPalace audit (private, in this session): example of why timestamped != temporal

## Related ADRs

- ADR 001: Pluggable Memory (informs the separation between D+I providers and ET's K+W owned graph)
- ADR 003 (pending): Pluggable Algorithms — temporal parameters become part of the algorithm protocol
- ADR 004 (pending): Contradiction Detection Strategy — how Haiku is prompted for supersession claims
