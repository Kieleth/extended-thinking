# ADR 007: Entity Resolution Family

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 003 (Pluggable Algorithms)
**Related:** ADR 006 (Link Prediction)

## Context

Concept extraction produces duplicates. The same idea appears in two conversations with slightly different wording ("Kuzu migration" vs "Graph DB switch"), and without deduplication the graph accumulates redundant nodes that fragment semantic clusters and dilute frequency signals.

Before this ADR, entity resolution was implemented as a single method on `GraphStore.find_similar_concept()` using `SequenceMatcher`. That:
- Lives in the wrong layer (storage shouldn't own resolution heuristics).
- Can't be swapped for a better algorithm.
- Can't combine multiple strategies (e.g., cheap string match → expensive embedding check).
- Misses semantic synonyms by design.

## Decision

Create a `resolution/` family under the pluggable algorithm architecture (ADR 003). Two initial plugins:

**`sequence_matcher`** — character-level Ratcliff-Obershelp (unchanged logic, extracted from `GraphStore`). Fast, deterministic, no dependencies. Catches exact duplicates, typos, minor rewordings.

**`embedding_cosine`** — semantic similarity via VectorStore embeddings. Uses `VectorStore.embed()` (new method on the protocol) to batch-embed concept signatures, computes cosine similarity in pure Python. Catches synonyms that SequenceMatcher misses.

Pipeline tries plugins in configured order, **first match wins**. Default order: `[sequence_matcher, embedding_cosine]`. Reasoning: cheap filter first, expensive fallback only if the cheap one says "no match."

## Why "first match wins," not "merge across plugins"

Resolution is a classification decision, not a scoring operation. Once any plugin says "this is a duplicate of X," we commit to that. Running multiple plugins and merging their answers would:

- Create ambiguity when plugins disagree (different threshold semantics).
- Multiply false positives.
- Make the pipeline slower without proportional benefit.

"First match wins" keeps the semantics simple: the ordered plugin list defines a cascade.

## Why extend VectorStore with `embed()`

The alternative was to have each embedding-based algorithm compute embeddings directly (e.g., import sentence-transformers). But:

- Forces every algorithm to pick its own embedding model — no consistency.
- Duplicates the embedding function across plugins.
- Couples algorithms to specific ML libraries.

`VectorStore.embed()` gives algorithms raw vectors using the same embedding function as the chunk store, so consistency is automatic. Swap the VectorStore backend, all embedding-based algorithms inherit the change.

The method is on `VectorStore` (not a separate "EmbeddingService") because embedding is fundamentally what a vector store knows how to do — exposing it avoids a needless abstraction.

## Plugin interface

Resolution plugins use a non-standard `resolve()` method in addition to the generic `run()`:

```python
def resolve(self, context: AlgorithmContext, query_name: str,
            query_description: str = "") -> dict | None:
    ...
```

`run()` is a no-op because resolution is called per-concept during extraction, not batch. The registry still instantiates resolution plugins the same way; the pipeline uses `resolve()` directly.

This is a small protocol asymmetry but justified: batch resolution would require calling LLM/embeddings on the whole concept corpus, which is exactly what we want to avoid.

## Parameters

### sequence_matcher
- `threshold`: default 0.85. Below this, no match.

### embedding_cosine
- `threshold`: default 0.82. Semantic embeddings need slightly lower threshold than string matching because semantic similarity scores are compressed toward the top.
- `use_description`: default True. Include concept description in the signature.

## Consequences

**Positive:**
- Plugins composable by config. Users can pick SequenceMatcher only (dev/test), or prepend a custom domain-specific resolver (e.g., WikidataEntityResolver).
- Embedding-based resolution genuinely catches synonyms. Tests confirm it matches "Kuzu migration" ↔ "Graph DB switch" that SequenceMatcher rejects.
- `GraphStore.find_similar_concept()` can be deprecated (it still exists for API compatibility but the pipeline no longer calls it).
- The `VectorStore.embed()` extension is reusable — future algorithms (semantic bridge prediction, concept clustering) get it for free.

**Negative:**
- Embedding resolution is slower than string matching (one embedding call per concept, batched per sync). At ~150 concepts: ~100-200ms overhead per new concept. Noticeable in large syncs.
- First-match-wins means later plugins never get a chance to disagree. If the cheap plugin returns a false positive, the expensive plugin never catches the error. This is the intended behavior, but it's worth flagging.
- Plugin instances don't cache embeddings across calls within a sync. Re-embedding existing concepts on every new-concept check is wasteful. Future work: embedding cache.

## What we don't do

- Cross-plugin voting or averaging (keeps semantics clean).
- LLM-based resolution as a third plugin (possible, expensive, can be added later as `resolution/llm_arbitration.py`).
- Auto-merge on suggestion (resolution already commits — it's how the pipeline works today, and this matches the pre-ADR behavior). Link prediction (ADR 006) is the non-committing cousin.

## MCP surface

No new tool. Resolution is invoked implicitly during `et_sync`. Users see the effect via `et_stats` (fewer duplicate concepts after sync) and logging.

If debugging, `et_catalog family=resolution` shows which plugins are registered. Future work: an `et_resolve` tool that runs resolution on a named concept interactively.

## References

- Ratcliff, J. W. & Metzener, D. E. (1988). Pattern Matching: the Gestalt Approach. Dr. Dobb's Journal.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks. EMNLP 2019.
- Manning, C. et al. (2008). Introduction to Information Retrieval, Cambridge, ch. 6 (cosine similarity).
- ADR 003 (pluggable algorithms), ADR 006 (link prediction as cousin non-committing pattern).
