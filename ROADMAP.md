# Roadmap

ET serves **two audiences**:

1. **Memory synthesis** (original). Ingest conversations and notes, extract concepts, generate wisdom with evidence trails. Single-user, async pipeline.
2. **Research-backbone** (ADR 013). Programmatic consumers using ET as a typed, bitemporal, queryable state store with algorithm hooks. Canonical consumer: [autoresearch-ET](../autoresearch-ET).

Both audiences share the same Kuzu graph, namespace-isolated. Malleus is the root ontology for both.

## Done (619 tests passing, 15 skipped, 1 xfailed)

### Pluggable Memory (ADR 001)
- MemoryProvider protocol (duck-typed, frozen dataclasses: MemoryChunk, Entity, Fact)
- KnowledgeGraphView protocol (optional, for providers with structured KG)
- AutoProvider: aggregates all detected sources into one stream
- Content filter: code ephemeral, only .md/conversations/comments indexed. Provider metadata tag overrides extension (Copilot .json files still treated as conversations)

### Batteries-Included Providers (ADR 010)
- `claude_code` — ~/.claude/projects/*.jsonl
- `chatgpt_export` — conversations.json (zip, folder, or file)
- `copilot_chat` — VSCode workspaceStorage sessions
- `cursor` — state.vscdb or manually exported folder
- `generic_openai_chat` — catch-all folder of OpenAI-format JSON
- `folder` — any directory of .md / .txt
- `mempalace` — optional, installed separately

### Bitemporal Storage (ADR 002)
- Kuzu GraphStore replaces SQLite ConceptStore
- Every edge carries t_valid_from, t_valid_to, t_created, t_expired, t_superseded_by
- `as_of(ts)` point-in-time queries, `diff(from, to)` change queries
- Contradiction detection via extractor `supersedes` + edge supersession

### Pluggable Algorithms (ADR 003)
8 families, 9 built-in plugins, all toggleable:
- `decay/physarum` — Tero et al. 2010 slime-mold conductance, source-age-aware
- `activity_score/recency_weighted` — freq * recency * sqrt(effective_degree)
- `bow_tie/in_out_degree` — Csete & Doyle 2004 metabolic pattern
- `bridges/top_percentile` — rich-club hubs
- `activation/weighted_bfs` — Anderson 1983 spreading activation
- `resolution/sequence_matcher` + `resolution/embedding_cosine` — entity merging
- `link_prediction/textual_similarity` + `link_prediction/embedding_similarity`
- `recombination/cross_cluster_grounded` — DMN-inspired serendipity (Beaty 2024)

Third parties register via entry points or direct `register(YourAlgo)` call.

### Temporal Weighting (most recent)
Old evidence decays, fresh evidence surfaces:
- Pipeline threads `chunk.timestamp` → edge `t_valid_from` (not sync time)
- Physarum decay uses `max(days_since_access, days_since_valid_from)` when `source_age_aware=True` (default)
- `active_nodes()` uses effective degree (sum of Physarum-decayed incident weights), so a concept connected only to 2025 evidence ranks below one connected to today's edges
- Extractor emits optional `source_created_at` when the text self-dates (dated journals, frontmatter); pipeline prefers it over chunk timestamp

### Configurable Models (ADR 004)
- `ET_EXTRACTION_MODEL` / `ET_WISDOM_MODEL` env vars
- `ET_EXTRACTION_PROVIDER` / `ET_WISDOM_PROVIDER` (anthropic, openai, etc.)

### MCP Tools (13)
- `et_insight` — sync + Opus wisdom generation, mind map rendering
- `et_concepts` — list concepts by frequency
- `et_explore` — concept detail with connections, provenance, spreading activation
- `et_graph` — unified graph overview (clusters, bridges, isolated)
- `et_path` — BFS shortest path with access tracking
- `et_sync` — pull from provider, content filter, extract concepts
- `et_stats` — provider info, concept count, filter breakdown
- `et_recall` — semantic search over indexed thinking chunks
- `et_suggest` — link prediction (which concepts probably relate)
- `et_recombine` — DMN cross-cluster recombination with grounded verdicts
- `et_core` — bow-tie core concepts
- `et_catalog` — list available algorithms and their metadata
- `et_shift` — temporal diff between two points in time

### Research-Backbone (ADR 013) — Phase 0 + C1 + C2
Malleus as root ontology, codegen-driven typed node framework, namespace isolation.
- `schema/imports/malleus.yaml` — symlink to the malleus ontology
- `schema/extended_thinking.yaml` — every class descends from a malleus root (Entity / Event / Signal / Relation)
- `make schema-kuzu` — LinkML → Kuzu DDL + typed Python accessors; `make schema-check` for drift
- `GraphStore(db_path, ontology=...)` — ontology-driven, constitutive (Architecture A)
- `GraphStore.insert(instance)` — typed writes; Kuzu's binder enforces domain/range
- `Ontology.merged_with(...)` — consumer schemas compose onto ET's base
- Namespace column on every node/edge: `"memory"` (memory pipeline) vs `"default"` or custom (programmatic)
- `AlgorithmContext.namespace` threads through; plugins scope accordingly

### Open-Source Prep
- FSL-1.1-Apache-2.0 license (anti-big-cloud, Apache after 2 years)
- README, CHANGELOG, CONTRIBUTING
- 13 ADRs in `docs/ADR/` (latest: 013 v2 research-backbone-audience)
- `/private` gitignored for WIP not yet public

## ADR 013 — research-backbone capabilities: SHIPPED

All nine capabilities (C1-C9) delivered. autoresearch-ET fully unblocked.

| Cap | What | Status |
|---|---|---|
| C1 | Typed node framework, ontology-driven | ✅ shipped (Phase 0) |
| C2 | Namespace isolation + `AlgorithmContext.namespace` | ✅ shipped |
| C3 | Synchronous HTTP write path (`POST /api/v2/graph/node`/`/edge`, `vectors_pending` marker) | ✅ shipped |
| C4 | Write-side MCP tools (`et_add_node`, `et_add_edge`, `et_write_rationale` with grounded-citation guard) | ✅ shipped |
| C5 | Filtered bitemporal queries (`et_shift`/`diff` with `node_types`/`edge_types`/`namespace`) | ✅ shipped |
| C6 | Typed vector similarity (`et_find_similar` over arbitrary typed nodes) | ✅ shipped |
| C7 | Algorithm write-back (`ProposalBy` edges, `et_run_algorithm(write_back=True)`) | ✅ shipped |
| C8 | Non-extraction ingest mode (`MemoryProvider.extract_concepts: bool`) | ✅ shipped |
| C9 | Public docs ([research-backbone.md](docs/research-backbone.md)) + autoresearch-ET canonical reference | ✅ shipped |

Consumer primer: [`docs/research-backbone.md`](docs/research-backbone.md).

### Integration acceptance-test suite (in flight — separate session)
Preload fixtures (claude-code JSONL + .md files), run AT harness that validates
not just "code runs" but "algorithms produce expected rankings / decay / paths".
Stops the MCP-roundtrip iteration loop.

### Dogfooding temporal weighting
Fresh sync with real data, verify source-age-aware decay surfaces current
thinking over stale topics. Evidence so far: 2025-era edges decay to ~0,
today's edges stay ~2.0; concept ranking reflects this.

## Next

### Wikidata linking (kieleth pattern)
- On concept extraction, search Wikidata API for QID matches
- LLM judges relevance (not just keyword)
- Store QID + Wikipedia URL + Wikidata description on concept
- `et_related <concept>` shows Wikipedia context

### arXiv / Semantic Scholar
- Search by concept name + description
- Store paper references (title, authors, abstract, URL)
- `et_papers <concept>` shows relevant academic work

### Social knowledge layer
- Opt-in concept sharing (privacy-first: embeddings only, not raw data)
- Embedding-based similarity across users
- `et_similar <concept>`: "3 others are exploring this space"

### Deploy
- Push to GitHub
- Docker (api + web + nginx)
- `pip install extended-thinking`
- `et init` / `et insight` / `et sync` CLI
