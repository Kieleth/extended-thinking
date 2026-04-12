# Changelog

Semantic-ish versioning. Pre-1.0 means the API can change between minor versions. Post-1.0 we commit to backward compatibility for MCP tool names + response shapes and algorithm protocol contract.

## [Unreleased]

### Added
- **Research-backbone audience (ADR 013), shipped.** ET accepts a second audience alongside memory synthesis: programmatic consumers using ET as a typed, bitemporal, queryable state store (research loops, workflow engines, typed archives). [autoresearch-ET](../autoresearch-ET) is the canonical reference consumer. All nine capabilities (C1-C9) delivered; see [docs/research-backbone.md](docs/research-backbone.md) for the consumer primer.
- **Write surface: HTTP + MCP.** `POST /api/v2/graph/node` and `/edge` (C3) return after Kuzu commit with `vectors_pending=true` marker. MCP tools `et_add_node`, `et_add_edge`, `et_write_rationale` (C4) mirror the HTTP shape. `et_write_rationale` enforces the grounded-citation guarantee — unresolved `cited_node_ids` reject the write.
- **Filtered bitemporal queries.** `GraphStore.diff(from, to, *, node_types, edge_types, property_match, namespace)` and the `et_shift` MCP tool return only the slice you ask for. Generic `nodes_added`/`nodes_expired`/`edges_added`/`edges_expired` shape with `_type` tags; legacy keys preserved for back-compat (C5).
- **Typed vector similarity.** `GraphStore.find_similar_typed(...)` and `et_find_similar` MCP tool — "have we seen something close to this?" over arbitrary typed nodes, scoped by type and namespace. Typed nodes auto-index on insert with `et_` prefixed metadata; `vectors_pending` tracks indexing state; indexing failure is non-fatal (C6).
- **Algorithm write-back.** New `ProposalBy` ontology edge type. `et_run_algorithm(algorithm, write_back=True)` persists plugin proposals as bitemporal edges with `algorithm`, `parameters_json`, `invoked_at`, `score` metadata. Consumers get an audit trail of "what the algorithm said at time T" without re-running it (C7).
- **Non-extraction ingest mode.** `MemoryProvider.extract_concepts: bool = True`. Structured-data providers set `False` — chunks store with provenance but skip the Haiku extraction loop. Conversation providers unchanged (C8).
- **`Rationale` ontology class** added as `Signal` subclass for grounded LLM justifications (C4).
- **Malleus as root ontology (ADR 013 v2, Phase 0).** Every ET class descends from a [malleus](../malleus) root — `Entity`, `Event`, `Signal`, `Relation` — plus mixins (`Identifiable`, `Temporal`, `Describable`, `Statusable`, `Agent`). `schema/extended_thinking.yaml` imports malleus via a symlink at `schema/imports/malleus.yaml`. Aligns with [malleus's KG Protocol](../malleus/KNOWLEDGE_GRAPH_PROTOCOL.md): the ontology is constitutive (Architecture A), not post-hoc validation.
- **Malleus as root ontology (ADR 013 v2, Phase 0).** Every ET class descends from a [malleus](../malleus) root — `Entity`, `Event`, `Signal`, `Relation` — plus mixins (`Identifiable`, `Temporal`, `Describable`, `Statusable`, `Agent`). `schema/extended_thinking.yaml` imports malleus via a symlink at `schema/imports/malleus.yaml`. Aligns with [malleus's KG Protocol](../malleus/KNOWLEDGE_GRAPH_PROTOCOL.md): the ontology is constitutive (Architecture A), not post-hoc validation.
- **Kuzu codegen (`make schema-kuzu`).** `scripts/gen_kuzu.py` emits `schema/generated/kuzu_ddl.py` (CREATE NODE/REL TABLE statements). `scripts/gen_kuzu_types.py` emits `schema/generated/kuzu_types.py` (Pydantic → Kuzu row serialization with system columns and Kuzu reserved-word renaming, e.g. `description → desc_text`). `make schema-check` regenerates + diffs against git-tracked files to catch drift in CI.
- **Ontology-driven `GraphStore`.** Hand-rolled `_init_schema` deleted. `GraphStore(db_path, ontology=default_ontology())` applies the codegen'd DDL on construction. `storage/ontology.py` provides the `Ontology` abstraction with `.from_module()` and `.merged_with()` so consumer schemas (autoresearch-ET) compose onto ET's base.
- **Typed write path: `GraphStore.insert(instance, *, namespace, source)`.** Takes any Pydantic instance of a registered class, dispatches to `to_kuzu_row`/`edge_endpoints`, resolves edge FROM/TO labels by id lookup, executes the matching Cypher. Returns the row id. Kuzu's binder enforces malleus's `slot_usage` domain/range at write time (a `Wisdom -[:RelatesTo]-> Concept` is rejected because RelatesTo is pinned Concept→Concept).
- **Namespace isolation (ADR 013 C2).** Every node and edge carries a `namespace` string column. Memory-pipeline writes default to `"memory"`; programmatic writes via `GraphStore.insert` default to `"default"` (matching autoresearch-ET's `ETClient` Protocol default). `get_concept`, `list_concepts`, `list_wisdoms`, `get_stats`, `active_nodes` accept an optional `namespace=` kwarg. `AlgorithmContext` gains a `namespace` field; plugins filter scope accordingly (`recency_weighted` updated).
- **Full column migration.** Legacy column names (`t_first_observed`, `t_last_observed`, `t_deprecated`, `t_generated`, `t_relevant_from`, `t_relevant_to`, `entity_type`, `provider_source`) replaced by ontology-native names (`first_seen`, `last_seen`, `t_expired`, `t_created`, `t_valid_from`, `t_valid_to`, `category`, `et_source`). Edge labels standardized PascalCase (`RELATES_TO → RelatesTo`, `INFORMED_BY → InformedBy`, `HAS_PROVENANCE → HasProvenance`). Pre-release; no data migration path.
- **Centralized configuration (ADR 012).** TOML-based, XDG-compliant, tiered. Config lives at `~/.config/extended-thinking/config.toml`; secrets at `~/.config/extended-thinking/secrets.toml` (mode 0600); drop-ins in `conf.d/`; per-project override via `./et.toml`. Seven-tier precedence: defaults → user → drop-ins → project → secrets → env → explicit. Legacy env vars and flat `.env` files still work as top-tier overrides.
- **Secrets guard.** Credentials in `config.toml`, drop-ins, or project config raise `RuntimeError` at load. API keys only accepted in `secrets.toml` or environment variables.
- **All plugin configuration routes through TOML.** `[algorithms.<family>.<plugin>]` tables drive the registry; `build_config_from_settings()` bridges TOML shape to the registry's internal format. Inline config dicts in `pipeline_v2.py` and `graph_store.py` deleted.
- **Provider paths and enable flags configurable.** `[providers.<name>]` tables control every built-in provider (claude-code, chatgpt-export, copilot-chat, cursor, folder, generic-openai-chat, mempalace). Scan paths, projects dir, extra folder locations — all TOML keys now.
- **`et config` CLI subcommands:** `init`, `path`, `show` (with `--format json` / `--show-secrets`), `validate`, `get`, `set` (with `--scope user|project|secrets`), `edit`. Value coercion: bool / int / float / comma-separated list / string.
- **XDG data dir migration.** `~/.extended-thinking/` atomically moves to `~/.local/share/extended-thinking/` on first run. Idempotent; refuses to merge when both locations hold data.
- **Temporal weighting (A/B/C/D).** Pipeline threads `chunk.timestamp` into edge `t_valid_from`. New `algorithms/activity_score/recency_weighted` plugin (default on) scores concepts by `freq * recency * sqrt(effective_degree)` where effective degree is the sum of Physarum-decayed incident weights — so old-evidence concepts naturally demote. Physarum decay gains `source_age_aware=True` (default): effective age is `max(days_since_access, days_since_valid_from)`. Extractor can emit optional `source_created_at` when the text self-dates; pipeline prefers it over chunk timestamp.
- Bitemporal knowledge graph: every edge carries `t_valid_from`, `t_valid_to`, `t_created`, `t_expired`, `t_superseded_by` (ADR 002).
- Contradiction detection during extraction: extractor flags `supersedes` claims, pipeline auto-invalidates old edges.
- Point-in-time queries: `GraphStore.list_concepts(as_of=)`, `.get_relationships(as_of=)`, `.get_stats(as_of=)`.
- `GraphStore.diff(from, to)` — temporal diff showing concepts added/deprecated, edges created/expired.
- Pluggable algorithm chassis (ADR 003): `Algorithm` protocol, `AlgorithmContext`, registry with `register` / `get_active` / `get_by_name` / `list_available`.
- 8 built-in algorithm plugins across 7 families:
  - `decay/physarum` — Tero 2010 slime mold conductance decay.
  - `activation/weighted_bfs` — Anderson 1983 spreading activation, integrates Physarum decay.
  - `resolution/sequence_matcher` — Ratcliff-Obershelp character similarity.
  - `resolution/embedding_cosine` — Sentence-BERT cosine for semantic dedup.
  - `link_prediction/textual_similarity` — SequenceMatcher missing-edge finder.
  - `link_prediction/embedding_similarity` — cosine-based missing-edge finder, catches synonyms.
  - `bow_tie/in_out_degree` — Csete & Doyle 2004 metabolic core pattern.
  - `bridges/top_percentile` — van den Heuvel & Sporns 2011 rich-club hubs.
  - `recombination/cross_cluster_grounded` — DMN-inspired cross-cluster pairs with LLM grounding (Beaty 2024, Granovetter 1973).
- Configurable AI models per tier: `ET_EXTRACTION_MODEL`, `ET_WISDOM_MODEL`, with per-tier provider selection (ADR 004).
- `VectorStore.embed()` — access raw embeddings without committing to storage. Used by embedding-based resolution and link prediction.
- New MCP tools: `et_core` (bow-tie), `et_recombine` (DMN), `et_suggest` (link prediction, with `algorithm` param), `et_shift` (temporal diff), `et_catalog` (registered algorithms).
- Content filter: `.py`, `.rs`, `.toml`, `.yaml`, etc. excluded from extraction (code is ephemeral).
- Multi-provider aggregation in `AutoProvider`: simultaneously reads from mempalace + claude-code + folders.
- Echo loop prevention: MP triples authored by ET are filtered from the unified graph.
- Full license: FSL-1.1-Apache-2.0 (ADR ADR-pending).

### Changed
- Storage migrated from SQLite `ConceptStore` to Kuzu `GraphStore` (proper graph database with Cypher, variable-length paths, pattern matching).
- Wisdom prompt now grounded in graph structure (active set, bridges, clusters) plus source paths — refuses with `nothing_novel` when no grounded insight possible.
- Extraction now batched (20 chunks/call) for richer concept diversity.
- Pipeline resolution, spreading activation, and bridge detection all routed through plugin registry.

### Fixed
- Same-batch edges removed (noise from extraction coincidence, not semantic relatedness).
- Entity resolution plugin-first; pipeline no longer calls `GraphStore.find_similar_concept` directly.
- Provenance now links concepts to source chunks with LLM model + timestamp.

### Architectural
- 9 ADRs covering memory provider protocol, bitemporal foundation, pluggable algorithms, configurable models, DMN recombination, link prediction, resolution family, activation family, bridges family.
- README, CONTRIBUTING, ALGORITHMS catalog, LICENSE.
- /private/ directory gitignored for in-progress work.
- 368 tests across 26 test files.

## [Pre-history]

The provider-based pipeline, VectorStore protocol, and ChromaDB integration were built earlier. See commit history for details. This CHANGELOG starts at the point when the project pivoted toward the K+W owned stack and the pluggable-algorithm architecture.
