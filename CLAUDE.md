# extended-thinking

A pluggable thinking layer for AI-augmented work. Sits on top of any memory system
(MemPalace, Obsidian, plain folders, Claude Code sessions) and generates wisdom
from your accumulated thinking patterns.

**Not a memory system.** Memory is commoditized (MemPalace, Mem0, Zep do it well).
Extended-thinking is the DIKW synthesis layer: extract concepts, detect patterns,
generate actionable wisdom, track follow-through.

## Product Invariants

Read [project-invariants.md](project-invariants.md) before making any change. Non-negotiable:

1. **Every insight traces to evidence** — no claims without a verifiable trail
2. **Never forget what it's seen** — the graph only grows, never silently loses data
3. **Enrichment is always relevant** — every suggestion links to the user's actual concepts
4. **Distinguish user's words from system's inferences** — raw vs extracted vs synthesized
5. **Processing never blocks the user** — all heavy work is async, UI always responsive

## Architecture

ET owns Knowledge + Wisdom. External systems provide Data + Information. ET serves **two audiences**: memory synthesis (humans) and programmatic consumers (research loops, workflow engines, typed archives — ADR 013).

```
Malleus root ontology (Entity / Event / Signal / Relation + mixins)
    ▲ imports (symlink at schema/imports/malleus.yaml)
    │
    ├── schema/extended_thinking.yaml  → ET classes (LinkML)
    │
    └── consumer schemas (e.g. autoresearch-ET/schema/autoresearch.yaml)

    ▼ make schema-kuzu (LinkML → codegen)

api/src/extended_thinking/_schema/  ← ships in the wheel
  ├── kuzu_ddl.py      CREATE NODE/REL TABLE statements
  ├── kuzu_types.py    Pydantic-to-Kuzu bridge (to_kuzu_row / from_kuzu_row)
  ├── models.py        Pydantic types (existing gen-pydantic output)
  ├── schema.json      JSON Schema
  └── types.ts         TypeScript types (for web consumers)

AUDIENCE 1 — MEMORY SYNTHESIS (namespace="memory"):
External Data Sources (D+I layer, pluggable)
├── Claude Code sessions (~/.claude/projects/*.jsonl)
├── ChatGPT export (conversations.json / zip / folder)
├── Copilot Chat (VSCode workspaceStorage)
├── Cursor (state.vscdb or exported folder)
├── Generic OpenAI chat (catch-all folder of JSON)
├── Folders (.md/.txt files)
├── MemPalace (optional, separate install)
▼ MemoryProvider protocol (adapters, open to third parties)
├── Pipeline: Extract (Haiku) → Connect → Synthesize (Opus)
└── Async; `vectors_pending` markers; never blocks the user.

AUDIENCE 2 — PROGRAMMATIC (namespace="default" or caller-scoped, ADR 013):
Consumer process
├── ships its own LinkML schema importing malleus (Hypothesis, Variant,
│   Run, Metric, etc.), generates its own typed accessors
└── writes via GraphStore.insert(instance) — synchronous, validated,
    bitemporal. Canonical reference: autoresearch-ET.

EXTENDED-THINKING (K+W substrate, shared by both audiences)
├── StorageLayer
│   ├── VectorStore (ChromaDB, optional) — semantic search
│   └── GraphStore (Kuzu, ontology-driven, bitemporal)
│       - GraphStore(db_path, ontology=default_ontology()) at construction
│       - Every node/edge carries t_valid_from/t_valid_to/t_created/
│         t_expired/t_superseded_by + namespace + et_source
│       - Architecture A: Kuzu binder enforces FROM/TO domain/range
│         from malleus slot_usage (constitutive, not post-hoc)
│
├── Plugin layer (ADR 003) — swappable algorithms per family:
│   ├── decay/           physarum (source-age-aware)
│   ├── activity_score/  recency_weighted (effective degree)
│   ├── bow_tie/         in_out_degree (Csete & Doyle 2004)
│   ├── bridges/         top_percentile
│   ├── activation/      weighted_bfs
│   ├── resolution/      sequence_matcher + embedding_cosine
│   ├── link_prediction/ textual + embedding_similarity
│   └── recombination/   cross_cluster_grounded (DMN pattern)
│   AlgorithmContext carries `namespace` so plugins scope to a slice.
│
├── UnifiedGraph (federated: GraphStore + provider KG)
│   SAME_AS bridging across stores
│
└── MCP Tools (13): et_insight, et_concepts, et_explore, et_graph,
    et_path, et_sync, et_stats, et_recall, et_suggest, et_recombine,
    et_core, et_catalog, et_shift
    (write-side tools — et_add_node, et_write_rationale — arrive with ADR 013 C4)
```

See [docs/ADR/001-pluggable-memory.md](docs/ADR/001-pluggable-memory.md) for the original audience contract, [docs/ADR/013-research-backbone-audience.md](docs/ADR/013-research-backbone-audience.md) v2 for the second audience + malleus-as-origin + the nine C's, and ADRs 002-012 for the stack (bitemporal foundation, pluggable algorithms, configurable models, DMN recombination, link prediction, resolution/activation/bridges families, batteries-included providers, centralized configuration). Ontology contract: [malleus KNOWLEDGE_GRAPH_PROTOCOL.md](../malleus/KNOWLEDGE_GRAPH_PROTOCOL.md).

## Stack

- **Backend**: Python 3.12+ / FastAPI
- **Frontend**: Next.js 16 / React / TypeScript / Tailwind / shadcn/ui (legacy, MCP is primary UI)
- **Root ontology**: [malleus](../malleus) (LinkML). Entity / Event / Signal / Relation + mixins. Every ET class declares `is_a:` a malleus root. ADR 013 v2.
- **Internal KG**: Kuzu GraphStore, ontology-driven (Architecture A). Schema = generated DDL from the LinkML. Every node/edge bitemporal + namespaced.
- **Codegen**: `make schema-kuzu` → `api/src/extended_thinking/_schema/kuzu_ddl.py` (DDL) and `kuzu_types.py` (Pydantic-to-Kuzu bridge). Generated artifacts live inside the package so the wheel ships them. `make schema-check` guards against drift.
- **Semantic search**: ChromaDB via VectorStore protocol (optional)
- **Memory providers**: Pluggable via MemoryProvider. Batteries-included: claude-code, chatgpt-export, copilot-chat, cursor, generic-openai-chat, folder. Optional: mempalace.
- **Algorithms**: Plugin registry — 8 families, 9 built-in plugins, all toggleable. `AlgorithmContext.namespace` scopes runs. Third parties can register via entry points.
- **AI**: Haiku (extraction), Opus (wisdom synthesis), configurable per tier via ET_EXTRACTION_MODEL / ET_WISDOM_MODEL.
- **License**: FSL-1.1-Apache-2.0 (Functional Source License, converts to Apache 2.0 after 2 years).
- **Tests**: ~635 unit tests + 35 acceptance tests (9 AT files, 4 of them Hypothesis property files). Run as `make test` + `make at` separately. See `docs/acceptance-tests.md`.

## Commands

```bash
make setup                   # Full setup from clean checkout
make schema                  # Generate all schema artifacts
make dev-api                 # Start FastAPI on :8000
make dev-web                 # Start Next.js on :3001
make dev                     # Start both (separate terminals)
make test                    # Run all unit tests
make lint                    # Lint all code

# Acceptance tests. See docs/acceptance-tests.md for the full workflow.
make at                      # Fast path: fixtures + FakeLLM + DummyVectorizer (~27s, zero network)
make at-vcr                  # Fast path + cassette-replayed real-model tests
make at-live                 # LIVE_API=1, hits Anthropic (pre-release only)
make at-record               # Re-record VCR cassettes (needs real API key)
make at-update-snapshots     # Accept new syrupy snapshot outputs
```

Do not combine `make test` and `make at` in a single pytest run. Kuzu
reserves an 8TB virtual region per `GraphStore()`; combined fixture density
on macOS exhausts address space. Run them as separate invocations.

## Key Files

```
api/src/extended_thinking/
  storage/graph_store.py      → Kuzu GraphStore (bitemporal, Cypher)
  storage/vector_protocol.py  → VectorStore protocol
  storage/chroma_store.py     → ChromaDB impl
  processing/pipeline_v2.py   → DIKW pipeline (sync, extract, wisdom)
  processing/extractor.py     → LLM concept extraction (Haiku)
  processing/unified_graph.py → Federated graph (GraphStore + provider KG)
  algorithms/                 → Plugin families (decay, activity_score, bow_tie,
                                bridges, activation, resolution, link_prediction,
                                recombination) + registry + protocol
  providers/                  → MemoryProvider implementations (claude-code,
                                chatgpt-export, copilot-chat, cursor,
                                generic-openai, folder, mempalace, auto)
  mcp_server.py               → MCP server (13 tools)
  api/routes/                 → FastAPI routes

api/tests/
  conftest.py                 → Shared fixtures (session + function scope)
  fixtures/                   → Committed CC sessions, notes folders, graph shapes, VCR cassettes
  helpers/                    → FakeListLLM, DummyLM, DummyVectorizer, assertion + snapshot helpers
  acceptance/                 → AT suite: end-to-end, algorithm snapshots,
                                invariants at scale, provider fusion, cassette,
                                Hypothesis properties per algorithm family

docs/
  acceptance-tests.md                          → AT framework: layers, fixtures, cassettes, snapshots, gotchas
  ADR/001..013-*.md                            → Architecture decision records
  configuration.md                             → Centralized config loader (ADR 012)
  research-kg-nature-inspiration.md            → Connectome, mycelium, Physarum
  vision-connected-knowledge.md                → External knowledge layer vision

project-invariants.md                          → Product invariants (5 non-negotiable)
```

## Conventions

- ET owns K+W (Knowledge + Wisdom). Providers own D+I (Data + Information).
- **Two audiences (ADR 013).** Memory synthesis writes default to `namespace="memory"`; programmatic consumers (autoresearch-ET and kin) write to `namespace="default"` or their own. Invariants (`project-invariants.md`) apply to both.
- **Malleus is the root ontology (ADR 013 v2).** Every new class added to `schema/extended_thinking.yaml` must `is_a:` one of `Entity` / `Event` / `Signal` / `Relation`. No class stands alone. Consumer projects ship their own LinkML importing malleus.
- **Schema lives in LinkML, not hand-rolled SQL.** Any column change goes through the yaml; then `make schema-kuzu` regenerates DDL + typed accessors. `make schema-check` enforces commit drift in CI.
- **GraphStore is ontology-driven.** `GraphStore(db_path, ontology=default_ontology())`. No `CREATE TABLE` strings in code — it all flows from the LinkML. Typed writes via `GraphStore.insert(instance)`; legacy `add_concept` / `add_wisdom` / etc. still work but write to the same ontology-driven tables.
- The DIKW pipeline reads from providers via the MemoryProvider protocol.
- Wisdom stays in ET's KG. Writing back to providers is disabled (prevents echo loops).
- Code is ephemeral, not memory. Only .md, conversations, comments get indexed.
  Conversation providers (claude-code, copilot-chat, chatgpt-export, cursor, generic-openai-chat) tag chunks via metadata["provider"] so the filter trusts provider identity over file extension.
- Bitemporal by default: every node/edge carries t_valid_from (world time) and t_created (transaction time). Pipeline passes chunk.timestamp as t_valid_from so old evidence decays correctly even after a fresh sync.
- Namespace scoping: pass `namespace=` to `list_concepts` / `get_concept` / `list_wisdoms` / `get_stats` / `active_nodes` when you want a slice. Algorithms receive it via `AlgorithmContext.namespace`.
- Algorithms are plugins, not core. Each plugin cites a paper and has a family. New ideas go as new plugins; don't modify storage for a new algorithm.
- Configuration goes through the centralized loader (ADR 012). New knobs land as TOML keys under `[data]`, `[providers.*]`, `[extraction]`, `[wisdom]`, `[server]`, `[credentials]` (secrets only), or `[algorithms.*.*]` (free-form, plugin-owned validation). No new environment variables or hardcoded paths; they go through `extended_thinking.config.settings`. See `docs/configuration.md`.
- MCP tool names are stable contracts. New features get new tool names, not renamed old ones.
- API routes are thin, delegate to processing modules.
- Never kill processes by name pattern, only by specific port.
- No silent exception swallowing, every except block must log or re-raise.
- **Tests come in two tracks.** Unit tests under `api/tests/` (pytest flat) run via `make test`. Acceptance tests under `api/tests/acceptance/` run via `make at` against committed fixtures with a FakeLLM and a deterministic vectorizer (DSPy-style hash embeddings). AT is the fast iteration loop for algorithm and pipeline behavior, sub-30-second, zero network. Real-model coverage runs cassette-replayed via `make at-vcr`. See [docs/acceptance-tests.md](docs/acceptance-tests.md) before adding a new algorithm, provider, or invariant.
- **Snapshot stability relies on `stabilize()`.** `tests/helpers/snapshot_matchers.py` rounds floats and scrubs volatile keys (timestamps, namespace). Extend `VOLATILE_KEYS` when an algorithm output grows a new wall-clock or config-driven field; do not accept a flaky snapshot.
- **Determinism in AT tests.** Pass `FIXED_NOW` into `AlgorithmContext.now` when the algorithm reads time. Use `DummyVectorizer` for embeddings. `tmp_data_dir` is function-scoped so Kuzu stores do not leak.

## Rendering MCP Tool Results

When `et_insight` returns JSON with `"_render": "wisdom_card"`, render it as a mind map in a code block. The insight crystallizes from its evidence concepts:

```
         {marker} {concept1.name}
        ╱
{marker} {concept2.name} ───●─── {marker} {concept3.name}
        ╲              ╱
         ╲            ╱
  {marker} {concept4.name} ──╯
                ╲
         ╔══════════════════════════════════════╗
         ║  ✦ {title}                           ║
         ╚══════════════════════════════════════╝

  WHY  {first sentence of why — keep it to 1-2 lines}

  DO   {first sentence of action — keep it to 1-2 lines}
```

Rules:
- Arrange evidence concepts as nodes flowing into the insight
- Use category markers: ◦ topic, ◈ theme, ◇ decision, ⚡ tension, ? question, ● entity
- The insight box (╔══╗) sits at the convergence point
- WHY and DO are 1-2 lines each — minimal, not the full description
- After the mind map, say: "Say **tell me more** to explore any concept, or **why** for the full reasoning."
- Keep it compact. The mind map should fit in ~15-20 lines.

When `et_explore` returns JSON with `"_render": "concept_detail"`, render the concept with its connections and source quote in a focused view:

```
  ┄┄┄ {concept.name} ({category}) ┄┄┄

  "{source_quote}"

  Frequency: {freq} sessions
  Connected to: {list of related concept names}

  Related wisdom: {wisdom title if any}
```

Progressive disclosure: the mind map is the entry point. The user drills into concepts via `et_explore`. Each level reveals more detail without overwhelming.
