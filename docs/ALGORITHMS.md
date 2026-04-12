# Algorithm Catalog

Every algorithm in extended-thinking ships with a research citation. Nothing is magic. This document auto-mirrors the plugin registry — see `et_catalog` MCP tool for live state.

## Families

| Family | Purpose | Consumed by |
|--------|---------|-------------|
| `decay` | Edge weight decay over time | Activation, path finding, active-set scoring |
| `activation` | Spreading activation for "find related" | et_explore, semantic queries |
| `resolution` | Entity matching and merging | Extraction pipeline |
| `bridges` | Rich-club hub detection | et_graph overview |
| `bow_tie` | Core-periphery structural analysis | et_core, wisdom prompts |
| `recombination` | DMN-inspired serendipity (expensive, LLM-grounded) | et_recombine |
| `link_prediction` | Suggest missing edges (cheap filter) | et_suggest, feeds et_recombine |

## Built-in algorithms

### decay/physarum — `PhysarumDecay`

Exponential decay by idle days, read-time transform. Based on Physarum polycephalum tube conductance model: unused tubes fade, used tubes persist.

```
W_effective = W_base * decay_rate ^ days_since_last_access
```

**Parameters:**
- `decay_rate`: default 0.95 (5% decay per day). Lower = faster forgetting.

**Reference:** Tero, A. et al. (2010). Rules for biologically inspired adaptive network design. Science 327:439-442. Scientific Reports 2025 (Physarum-inspired mesh networks).

**Temporal-aware:** yes (implicitly — time is its axis).

---

### bridges/top_percentile — `TopPercentileBridges`

Rich-club hub detection by raw degree. Returns concepts whose degree is in the top-N percentile AND above a minimum-degree floor. Used by `et_graph` to surface the structural anchors of the graph.

**Parameters:**
- `percentile`: default 0.10, top-10% by degree.
- `min_degree`: default 5, floor to exclude trivial hubs in sparse graphs.

**Reference:** van den Heuvel & Sporns 2011, J Neurosci 31(44). Colizza et al. 2006, Nature Physics 2.

**Temporal-aware:** yes. Respects `as_of` for point-in-time bridge detection (who was a bridge last month?).

**Future siblings:** `betweenness_centrality` (Freeman 1977) for more rigorous centrality; `rich_club_coefficient` (Colizza 2006) for ordering quality.

---

### activation/weighted_bfs — `WeightedBFSActivation`

Spreading activation for "find related concepts." From seed(s) with initial score 1.0, propagate through neighbors: `score[n] += score[node] * edge_weight * decay_per_hop`. Edge weights are Physarum-decayed (stale edges contribute less).

Strictly better than BFS for ranking related concepts: respects edge strength, decays with hop distance, budgets to sparse active set.

**Parameters:**
- `depth`: default 3, max BFS rounds.
- `decay_per_hop`: default 0.7, multiplier applied each hop.
- `budget`: default 100, max nodes scored.
- `min_spread`: default 0.01, cutoff for propagation.

**Reference:** Anderson 1983 JVLVB 22(3). Collins & Loftus 1975 Psych Review 82(6). Crestani 1997 AI Review 11.

**Temporal-aware:** yes. Honors `as_of` and consumes Physarum decay for edge weights (stale edges fade).

**Uses:** `et_explore` consumes activation to surface related concepts for any explored node.

---

### resolution/sequence_matcher — `SequenceMatcherResolution`

Character-level entity resolution using Ratcliff-Obershelp (Python `difflib.SequenceMatcher`). Fast, deterministic, no dependencies. Catches exact duplicates, minor typos, small rewordings.

**Parameters:**
- `threshold`: default 0.85, minimum similarity ratio.

**Reference:** Ratcliff & Metzener 1988, Dr. Dobb's Journal (Gestalt pattern matching).

**Temporal-aware:** no (string matching is time-independent).

**Tradeoff:** Misses semantic synonyms without string overlap. Use `embedding_cosine` as fallback.

---

### resolution/embedding_cosine — `EmbeddingCosineResolution`

Semantic entity resolution via embedding cosine similarity. Uses `VectorStore.embed()` to batch-embed concept signatures, computes cosine in pure Python. Catches synonyms that SequenceMatcher misses ("Kuzu migration" ↔ "Graph DB switch").

**Parameters:**
- `threshold`: default 0.82 (semantic scores compressed higher than string).
- `use_description`: default True, include description in signature.

**Reference:** Reimers & Gurevych 2019 EMNLP (Sentence-BERT). Manning IR ch.6 (cosine).

**Temporal-aware:** no.

**Requires:** VectorStore with an embedding function (ChromaDB default).

**Pipeline usage:** The pipeline tries `sequence_matcher` first (cheap) and falls back to `embedding_cosine` on no match. Configure via `algorithms.resolution` in `config.yaml`.

---

### link_prediction/embedding_similarity — `EmbeddingSimilarityLinkPrediction`

Semantic link prediction via sentence-level embedding cosine similarity. Catches synonym pairs that `textual_similarity` misses ("Kuzu migration" ↔ "Graph DB switch"). Batches embeddings in one call, then O(n²) pure-Python cosine.

**Parameters:**
- `top_k`: default 10, max candidates returned.
- `threshold`: default 0.75, minimum cosine similarity (semantic scores compressed higher than string).
- `use_description`: default True, include description in signature.

**Reference:** Reimers & Gurevych 2019 EMNLP (Sentence-BERT). Manning IR ch.6 (cosine).

**Temporal-aware:** yes.

**Requires:** VectorStore with an embedding function (ChromaDB default). Falls back to empty results if unavailable.

**Cost:** One batched embedding call (~300ms at 1000 concepts) + O(n²) cosine computations (~100ms). Sub-second at our scale.

**Usage:** `et_suggest algorithm=embedding_similarity` in MCP, or configure as default via `config.yaml`.

---

### link_prediction/textual_similarity — `TextualSimilarityLinkPrediction`

Cheap missing-link finder. For every pair of concepts NOT currently linked, computes SequenceMatcher similarity on their signature (name + description + source quote). Returns top-k above threshold.

Designed to feed `et_recombine`: cheap filter produces candidate pairs, expensive LLM-grounded verdicts decide which are real.

**Parameters:**
- `top_k`: default 10, max candidates returned.
- `threshold`: default 0.5, minimum similarity (0-1).
- `max_pairs`: default 5000, caps O(n²) evaluation cost.

**Reference:** Liben-Nowell & Kleinberg 2007, JASIST 58(7). Ratcliff-Obershelp via Python difflib.

**Temporal-aware:** yes. Honors `as_of` when filtering existing edges.

**Tradeoff vs embedding-based link prediction:** Misses synonym pairs without string overlap ("Kuzu migration" vs "Graph DB switch"). Sibling plugin `embedding_similarity` will handle those via ChromaDB cosine on concept signatures.

---

### recombination/cross_cluster_grounded — `CrossClusterGroundedRecombination`

DMN-inspired serendipity. Samples cross-cluster concept pairs (concepts with no existing graph path) and asks a strong reasoning model whether a real bridge exists. Returns grounded, speculative, or no-connection verdicts — never commits edges automatically.

**Three verdicts:**
- `grounded` — real mechanism exists in reality (specific, not wished)
- `speculative` — would require a named missing piece to become real
- `no_connection` — the distance is principled; don't force it

**Parameters:**
- `candidates_per_run`: default 3 (each = 1 LLM call, expensive on Opus-tier)
- `min_in_degree`: default 2, bias toward well-attested concepts
- `max_neighbors_shown`: default 5, context size per concept
- `random_seed`: None, for reproducible tests

**Reference:** Beaty et al. 2024, Brain 147(10). Comm Bio 2025 (DMN-ECN switching). Granovetter 1973, AJS 78(6). RecSys 2024 (serendipity).

**Temporal-aware:** yes. Pass `as_of` for historical recombination ("what cross-bridges were possible as of March?").

**Requires:** an `llm_caller` in `context.params` (injected by the MCP layer using the configured `ET_WISDOM_MODEL`). Tests can inject a mock.

---

### bow_tie/in_out_degree — `InOutDegreeBowTie`

Identifies "metabolic core" concepts: those with high in-degree (many source chunks attest to them) AND high out-degree (they fan out into many downstream concepts). Core = broad grounding + productive synthesis.

```
bow_tie_score = sqrt(in_degree * out_degree)
```

**Parameters:**
- `top_k`: default 10, how many core concepts to return.
- `min_in_degree`: default 2, exclude concepts with too few source chunks.
- `min_out_degree`: default 2, exclude concepts that don't fan out.

**Reference:** Csete, M.E. & Doyle, J.C. (2004). Bow ties, metabolism and disease. Trends in Biotechnology 22(9):446-450. Ma & Zeng (2003), Bioinformatics 19(11).

**Temporal-aware:** yes. Pass `as_of` to context for point-in-time bow-tie ("what were your core themes in March?").

## Writing your own algorithm

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full guide.

Minimal example:

```python
from extended_thinking.algorithms.protocol import (
    AlgorithmContext, AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register


class MyCustomDecay:
    meta = AlgorithmMeta(
        name="linear_decay",
        family="decay",
        description="Linear decay: w(t) = max(0, w0 - alpha * days)",
        paper_citation="Your reference here, year",
        parameters={"alpha": 0.1},
        temporal_aware=True,
    )

    def __init__(self, alpha: float = 0.1):
        self.alpha = alpha

    def run(self, context: AlgorithmContext):
        return None  # or whatever your family expects


register(MyCustomDecay)
```

Drop that file in your plugin package and `pip install` — or drop it anywhere on the Python path and import it. The registry picks it up.

## User config

`~/.extended-thinking/config.yaml`:

```yaml
algorithms:
  decay: [physarum]
  bow_tie: [in_out_degree]
  # explicitly disable:
  # recombination: []

parameters:
  physarum:
    decay_rate: 0.98
  in_out_degree:
    top_k: 5
    min_in_degree: 3
```

Leave a family out of `algorithms:` to enable all registered plugins for that family. Set it to `[]` to disable the family entirely.

## Principles

Every algorithm must:

1. **Cite research.** No heuristics without references. If you're inventing, cite your own work or clearly mark as experimental.
2. **Be temporal-aware or explicit that it isn't.** Set `temporal_aware: True` when `as_of` changes behavior.
3. **Be deterministic given fixed inputs** (no hidden RNG without a seed parameter).
4. **Be testable in isolation** (run with a fresh GraphStore, no global state).
5. **Return a predictable shape** matching its family's contract.
