# ADR 006: Link Prediction Family

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 002 (Bitemporal), ADR 003 (Pluggable Algorithms)
**Related:** ADR 005 (DMN Recombination)

## Context

Two kinds of hidden connections live in a knowledge graph:

1. **Structurally distant, semantically close:** Two concepts from different clusters that are actually talking about the same thing. The user hasn't made the connection explicit because the concepts emerged in different sessions, projects, or time periods.

2. **Novel cross-domain bridges:** Two concepts that don't map to the same thing but genuinely relate via an underlying mechanism. DMN recombination (ADR 005) handles these.

These are distinct problems with distinct cost profiles. Recombination asks an LLM per pair (expensive, strong reasoning). Link prediction compares signatures algorithmically (cheap, mechanical). Treating them as one family would conflate them.

The canonical "link prediction" framing in KG literature (Liben-Nowell & Kleinberg 2007) covers both topological and semantic approaches. We adopt the term for this family.

## Decision

Add a `link_prediction/` family. First implementation: `textual_similarity` using SequenceMatcher on concept signatures.

Each algorithm in this family:
- Takes a KG, returns candidate unlinked concept pairs ranked by some similarity metric
- Excludes pairs that already have an edge (respecting temporal filters)
- Returns candidates, does NOT commit edges
- Is temporal-aware

The family leaves room for sibling implementations:
- `embedding_similarity`: ChromaDB cosine on concept signatures (semantic, needs VectorStore)
- `graph_structure`: Adamic-Adar / common-neighbors (topological only)
- `transe` / `rotate`: embedding-based KG completion (deep learning, if someone wants to wire it)

## Why start with SequenceMatcher

- Zero dependencies (stdlib only).
- Deterministic (easy to test, reproducible).
- Catches the easy wins: near-duplicate concepts entity resolution missed, minor rewordings across sources.
- Fast on small graphs (O(n²) is fine at <1000 concepts).

Tradeoff: misses synonym pairs ("Kuzu migration" vs "Graph DB switch") and semantic equivalents without string overlap. That's the job of `embedding_similarity` — sibling plugin, same family.

## Why candidates, not commits

Same reasoning as ADR 005 (DMN recombination): the system proposes, the user decides. Auto-committing suggested links:

- Amplifies sampling errors (a false positive becomes graph noise).
- Destroys the "missing edge" signal for future runs.
- Reshapes clusters silently, changing what future recombination sees.

A future `et_suggest_accept <from> <to>` tool commits a specific suggestion. Not in scope here.

## Pairing with DMN recombination

The two algorithms compose naturally:

1. `et_suggest` (cheap): surfaces N candidates by textual similarity.
2. User picks interesting ones.
3. `et_recombine` (expensive) with those pair IDs: asks Opus for grounded verdicts.

This is the economic pattern from link prediction literature: cheap candidate generation feeding expensive ranking or verification. We just use LLM-grounded verdicts instead of ranking scores as the expensive step.

The two tools stay independent (neither requires the other), but the workflow is natural.

## Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `top_k` | 10 | How many candidates to return |
| `threshold` | 0.5 | Minimum similarity score (0-1) |
| `max_pairs` | 5000 | Cap on O(n²) evaluation |

`max_pairs` matters because 1000 concepts produces ~500K pairs. At that scale we need a better signature comparison than SequenceMatcher, but the cap prevents runaway cost in the meantime.

## What signature to compare

```
signature(c) = c.name + ". " + c.description + ". " + c.source_quote
```

All three sources of text, joined. Names alone miss descriptions that match. Descriptions alone miss exact name duplicates. Source quotes add verbatim user phrasing that often captures the real semantic anchor.

## Consequences

**Positive:**
- Immediate value: surfaces near-duplicate concepts for review.
- Cheap compared to DMN recombination. Users can run frequently.
- Deterministic — great for tests and reproducibility.
- Extensible: family has room for embedding-based and topological variants.

**Negative:**
- SequenceMatcher misses semantic synonyms without string overlap.
- O(n²) scales badly past ~1000 concepts (the max_pairs cap helps but isn't a real solution at scale).
- No "confidence" — similarity score is not calibrated to probability of being a real link.

**Mitigations:**
- `embedding_similarity` plugin handles synonyms (future work).
- Users with large graphs can tune `top_k` and `threshold` higher.
- For calibrated confidence, feed candidates to DMN recombination (which provides confidence per verdict).

## MCP surface

New tool: `et_suggest`.

- Default: top 10 candidates, threshold 0.5
- Temporal-aware via `as_of`
- Renders as `A ⟷ B (similarity=0.72)` with both signatures shown

## References

- Liben-Nowell, D. & Kleinberg, J. (2007). The link-prediction problem for social networks. JASIST 58(7):1019-1031.
- Adamic, L. & Adar, E. (2003). Friends and neighbors on the web. Social Networks 25(3):211-230.
- Python difflib.SequenceMatcher (Ratcliff-Obershelp)
- Modern embedding-based KG completion: TransE (Bordes et al. 2013), RotatE (Sun et al. 2019)
- ADR 003 (pluggable algorithms) and ADR 005 (DMN recombination)
