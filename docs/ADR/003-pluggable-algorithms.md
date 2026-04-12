# ADR 003: Pluggable Algorithms at the K+W Layer

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 001 (Pluggable Memory), ADR 002 (Bitemporal Foundation)

## Context

Extended-thinking is accumulating algorithms at the Knowledge + Wisdom layer: edge decay, spreading activation, entity resolution, bridge detection, bow-tie identification, DMN recombination, semantic bridge suggestions, change detection, and more. Each has research foundations and tradeoffs. Each could have multiple valid implementations (e.g., Physarum decay vs linear decay vs none; SequenceMatcher resolution vs embedding cosine vs LLM arbitration).

Three problems with the current state:

1. **Hardcoded.** Decay formula, active-set scoring, bridge threshold — all baked into code. Users can't tune parameters without editing source.
2. **Not extendable.** Third parties can't add their own algorithms without forking.
3. **No registry.** No way to ask "what algorithms are available and active?" No way to ship an "advanced" algorithm alongside a "simple" default.

We already use this pattern successfully for data sources (MemoryProvider protocol, swappable providers) and vectors (VectorStore protocol, ChromaDB impl). The precedent works. Algorithms at the K+W layer deserve the same treatment.

## Decision

Every K+W algorithm is a plugin implementing a protocol. The built-in algorithms live in `algorithms/` organized by family. Users configure which ones are active via `config.yaml`. Third parties can register their own.

Each plugin carries metadata: name, description, parameters, research citation. The registry surfaces this via `et_catalog` (for discovery) and `et_settings` (for current config).

## Plugin families

Six families at launch, each with a protocol and at least one built-in implementation:

```
algorithms/
├── protocol.py              # shared Algorithm base protocol
├── registry.py              # load + dispatch by config
│
├── decay/                   # edge weight decay over time
│   └── physarum.py          # built-in: weight * decay_rate^days_since_access
│
├── activation/              # spreading activation for "find related"
│   └── weighted_bfs.py      # built-in: score[neighbor] += score[node] * weight
│
├── resolution/              # entity matching and merging
│   ├── sequence_matcher.py  # built-in: difflib.SequenceMatcher, fast, no LLM
│   └── embedding_cosine.py  # (future) VectorStore-backed
│
├── bridges/                 # rich-club hub detection
│   └── top_percentile.py    # built-in: top 10% by degree
│
├── bow_tie/                 # core-periphery structural analysis
│   └── in_out_degree.py     # built-in: high in-degree from chunks + high out-degree to wisdom
│
└── recombination/           # DMN-inspired serendipity
    └── cross_cluster_grounded.py  # built-in: sample from different clusters, require grounding
```

Each family has room for multiple implementations. Users pick by name in config.

## Protocol design

Every algorithm follows a minimal contract:

```python
class Algorithm(Protocol):
    name: str                  # unique identifier, e.g. "physarum"
    family: str                # "decay", "activation", etc.
    description: str           # one-line purpose
    paper_citation: str        # research reference, required
    parameters: dict           # tunable params with defaults + types

    def run(self, context: AlgorithmContext) -> AlgorithmResult: ...
```

`AlgorithmContext` carries:
- `kg`: the GraphStore
- `vectors`: VectorStore (nullable)
- `as_of`: temporal parameter (ADR 002 integration — all algorithms are temporal-aware by default)
- `params`: merged config + overrides
- `now`: injectable clock for testing

`AlgorithmResult` is family-specific. Decay returns nothing (mutates edge weights). Activation returns scored concept IDs. Bow-tie returns core concepts with justification. Recombination returns candidate cross-cluster connections.

This keeps the protocol minimal (every algorithm reads context, produces a result) while letting each family define its own result shape.

## Configuration

User config at `~/.extended-thinking/config.yaml`:

```yaml
algorithms:
  decay: [physarum]
  activation: [weighted_bfs]
  resolution: [sequence_matcher]
  bridges: [top_percentile]
  bow_tie: [in_out_degree]
  recombination: [cross_cluster_grounded]

parameters:
  physarum:
    decay_rate: 0.95
  weighted_bfs:
    budget: 100
    decay_per_hop: 0.7
  top_percentile:
    threshold: 0.1  # top 10%
  cross_cluster_grounded:
    candidates_per_run: 3
    min_cluster_distance: 2
```

Disabling is explicit: `algorithms.bridges: []` turns off bridge detection entirely. The system degrades gracefully — tools that rely on bridges simply skip that section.

Multiple implementations in the same family are allowed (e.g., run both Physarum and a linear decay for comparison) but uncommon. The default is one per family.

## Registry

`registry.py` provides:

```python
def list_available(family: str | None = None) -> list[AlgorithmMeta]:
    """All registered algorithms, with metadata for discovery."""

def get_active(family: str, config: dict) -> list[Algorithm]:
    """Resolved, instantiated algorithms per user config."""

def register(alg: type[Algorithm]) -> None:
    """Third-party registration at import time or via plugins."""
```

Third-party packages register their algorithms via:

1. **Direct import**: `from et_algorithm_yourname import YourAlgo; register(YourAlgo)` — fine for internal/custom use.
2. **Entry points**: `pip install et-algorithm-yourname` auto-registers via `pyproject.toml` entry point `extended_thinking.algorithms`.

Both work. Entry points are the publishing path for OSS contributors.

## MCP surface

Two new tools:

- `et_catalog` — lists available algorithms, grouped by family, with descriptions and citations. Third parties see their plugin appear after install.
- `et_settings` — shows which algorithms are currently active and their parameters. Lets the user see the current configuration at a glance.

Existing tools continue to work, now calling through the registry instead of hardcoded paths.

## Temporal integration (inherits from ADR 002)

Every algorithm accepts `as_of` in its context. Implementations are free to ignore it (an entity resolver may not care about time) or honor it (activation at point-in-time, bow-tie core as of date X, decay based on valid_time vs access_time).

Temporal-aware algorithms declare support via a `temporal_aware: bool` field. The registry tracks this. Tools that need temporal reasoning (`et_shift`, change detection) surface only temporal-aware algorithms.

## Implementation order

1. Define `Algorithm` protocol + `AlgorithmContext` + registry (infrastructure).
2. Extract existing hardcoded algorithms into plugins:
   - `decay/physarum.py` (from `effective_weight` in GraphStore)
   - `activation/weighted_bfs.py` (from `spread_activation` in GraphStore)
   - `resolution/sequence_matcher.py` (from `find_similar_concept` in GraphStore)
   - `bridges/top_percentile.py` (from `get_graph_overview` bridge logic)
3. Wire registry into pipeline and MCP tools. No behavior change yet.
4. Add `et_catalog` and `et_settings` MCP tools.
5. Implement NEW plugins using the new architecture:
   - `bow_tie/in_out_degree.py`
   - `recombination/cross_cluster_grounded.py`
6. Document each algorithm in `docs/ALGORITHMS.md` with research citation.
7. Write `CONTRIBUTING.md` explaining how to add a plugin.

## Consequences

**Positive:**
- Users can tune, swap, disable algorithms without forking.
- Third parties can ship plugins via pip.
- Every algorithm carries its research citation — no magic, no unexplained heuristics.
- Forces discipline: adding an algorithm means writing an ADR + protocol impl + tests + catalog entry. Raises the bar for what ships.
- Makes the system teachable: the algorithm catalog doubles as documentation of the design philosophy.

**Negative:**
- One more indirection. Every algorithm call goes through the registry.
- Config surface grows. Users who never touch config are fine (sensible defaults), but the full config spec is now larger.
- Third-party plugins can misbehave (bad performance, incorrect output). Mitigation: per-plugin enable/disable, logging of plugin identity in outputs so bad actors are traceable.

**Non-consequences (things this doesn't change):**
- Storage layer (GraphStore, VectorStore) stays as-is. Plugins read from it, don't replace it.
- Provider layer (MemoryProvider) stays as-is.
- MCP tool names and response shapes stay stable.

## What this doesn't include

Explicitly deferred to future ADRs:

- Plugin sandboxing / permissions (right now plugins run with full Python access; this is fine for self-hosted, may need tightening for hosted deployments).
- Cross-plugin composition (e.g., "run plugin A, feed output to plugin B"). Current design runs each plugin independently, pipeline orchestrates.
- Plugin versioning / compatibility matrix (handled informally via semver on the protocol; formal API stability contract later).

## References

- ADR 001 (Pluggable Memory): same pattern, applied to D+I inputs
- ADR 002 (Bitemporal Foundation): `as_of` parameter is part of every algorithm's context
- Python Entry Points spec (PEP 517/518)
- Protocol pattern (PEP 544, structural subtyping)
- Good examples in the wild: pytest plugins, scrapy middleware, django apps
