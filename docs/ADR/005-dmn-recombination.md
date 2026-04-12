# ADR 005: DMN Recombination Plugin

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 002 (Bitemporal), ADR 003 (Pluggable Algorithms), ADR 004 (Configurable Models)

## Context

The research documented in `docs/research-kg-nature-inspiration.md` identifies the Default Mode Network (DMN) as the brain's background recombination engine: during idle states, the DMN freely associates concepts from different cortical regions without executive filtering, and creativity correlates with dynamic switches between DMN and the executive control network.

Our knowledge graph has structure — clusters, bridges, active sets — but nothing that performs the DMN's role. All current algorithms operate on existing edges. None generate new candidate connections from the space of concepts that *aren't* currently linked.

A previous attempt at wisdom generation produced hallucinated bridges across concepts from different systems (the "Kuzu-backed CRDT GraphStore" incident — inventing a system that doesn't exist). That episode revealed the real design constraint: **unconstrained cross-pollination hallucinates; grounded cross-pollination is the differentiator.**

## Decision

Implement DMN recombination as the first algorithm in the `recombination/` family. Default implementation: `cross_cluster_grounded`.

Algorithm shape:

1. Enumerate clusters (connected components in the KG).
2. Sample N pairs of (concept_a, concept_b) from **different** clusters. Bias sampling toward high-in-degree concepts (well-attested, less likely to be noise).
3. For each pair, gather full context: source paths, source types, cluster neighbors, source quotes.
4. Call a strong reasoning model (default: wisdom-tier, Opus-class) with a structured prompt that demands one of three verdicts:
   - **grounded** — specific mechanism exists in reality
   - **speculative** — connection would require a named missing piece
   - **no_connection** — distance is principled
5. Return candidates ranked grounded > speculative > no_connection, with confidence scores.

Output is **candidates**, not committed edges. This is deliberate: we learned from the contradiction-detection episode that silent commitment of AI-generated graph changes is dangerous.

## Why cross-cluster, not cross-domain embedding

Two concepts embedded close in vector space might be the same concept (entity resolution). Two concepts from different clusters (no graph path between them) are known to be semantically distant in the user's *own* thinking — they haven't crossed in practice. That's where creative recombination is worth trying.

Granovetter's "strength of weak ties" (AJS 1973) confirms: useful surprise comes from connections that span structural gaps, not from adjacent-neighbor reinforcement.

## Why grounded verdicts with three tiers

Hallucinated bridges are catastrophic to trust. The system must either:
- Describe a real mechanism (grounded)
- Explicitly flag the missing piece (speculative)
- Acknowledge that no real bridge exists (no_connection)

The speculative verdict is load-bearing. It's honest about what's missing and produces useful thought exercises: "To connect X and Y, you'd need Z — is building Z worth it?" That's a concrete roadmap question, not a speculative claim.

## Why temporal-aware

Cluster structure changes over time. Pairs that were cross-cluster in April may be connected in June. Recombination as-of-March answers "given your March-era thinking, what recombinations were possible?" — historical reconstruction of the decision-space you had.

## Why not auto-commit accepted recombinations

The system proposes; the user decides. Two reasons:
1. Even a grounded verdict is an LLM judgment; false positives occur.
2. Accepting a cross-cluster edge changes cluster topology, which changes future recombination. The user should explicitly choose to reshape the graph.

A future `et_recombine_accept <from> <to>` tool (not in scope here) would be the manual commit path.

## Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `candidates_per_run` | 3 | Each candidate = 1 LLM call. Default balances cost and coverage. |
| `min_in_degree` | 2 | Bias toward well-attested concepts (noise filter). |
| `max_neighbors_shown` | 5 | Context size limit for the LLM prompt. |
| `random_seed` | None | Reproducibility for tests. |

Each is overridable via user config (`parameters.cross_cluster_grounded` in config.yaml).

## LLM interaction

The algorithm takes an `llm_caller: Callable[[str], str]` in `context.params`. This:

- Decouples the algorithm from any specific LLM client.
- Lets tests inject a mock.
- Lets the MCP layer wire in the configured wisdom-tier model (ADR 004).
- Allows third-party plugins to use their own LLM provider without forking.

If no `llm_caller` is provided, the algorithm returns candidate pairs with verdict `"unevaluated"` — useful for graph-only sampling or testing.

## MCP surface

New tool: `et_recombine`.

- Default: 3 candidates
- Uses the configured `ET_WISDOM_MODEL` (Opus by default)
- Temporal-aware via `as_of`
- Renders verdicts with icons (✓ grounded, ? speculative, ✗ no connection)
- Explicitly explains that grounded = real bridge, speculative = missing piece, no_connection = principled distance

## Cost consideration

Each candidate is one call to the wisdom model. At 3 candidates, that's 3 Opus-class calls per `et_recombine`. For users on cheaper tiers, `ET_WISDOM_MODEL=gpt-4o-mini` or similar trades quality for cost. The algorithm doesn't care — it delegates via the llm_caller abstraction.

## Consequences

**Positive:**
- Delivers on the product differentiator from the research doc: structured serendipity.
- Grounding requirement prevents the hallucination failure mode.
- Explicit "speculative" verdict produces useful roadmap questions ("what would need to exist for this bridge to be real?").
- Temporal-aware: can reconstruct past recombination spaces.
- Cost-controllable: fewer candidates = fewer calls.

**Negative:**
- Each call is expensive (Opus-tier).
- The algorithm can't verify its own verdicts — grounded claims are trusted as grounded. A future "recombination auditor" algorithm could spot-check.
- Cluster sampling assumes clusters are meaningful. In a low-data regime (few concepts, few edges), clusters are artifacts of sampling, not thinking structure.

## What we don't do

- Embedding-based sampling (different from graph-based cluster sampling). Could be a second `recombination/` plugin later.
- Automatic edge commitment on "grounded" verdicts. User decides.
- Chained recombination (use the output of one recombination as input to another). Would amplify LLM errors.

## References

- Beaty, R.E. et al. (2024). Brain networks underlying novel metaphor production. Brain 147(10):3409-3425.
- Dynamic DMN-ECN switching study, Communications Biology (2025). Creativity predicted by switch frequency across 2,433 participants.
- Granovetter, M. (1973). The strength of weak ties. American Journal of Sociology 78(6):1360-1380.
- RecSys 2024: Serendipity in recommender systems. Definitions: relevant + novel + unexpected.
- ADR 002 (Bitemporal), ADR 003 (Pluggable Algorithms), ADR 004 (Configurable Models)
