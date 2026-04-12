# extended-thinking

[![CI](https://github.com/Kieleth/extended-thinking/actions/workflows/tests.yml/badge.svg)](https://github.com/Kieleth/extended-thinking/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/extended-thinking.svg)](https://pypi.org/project/extended-thinking/)
[![Python](https://img.shields.io/pypi/pyversions/extended-thinking.svg)](https://pypi.org/project/extended-thinking/)
[![License](https://img.shields.io/badge/license-FSL--1.1--Apache--2.0-blue.svg)](https://github.com/Kieleth/extended-thinking/blob/main/LICENSE.md)

**Grounded reasoning over your memory, not another memory.**

Extended-thinking sits on top of any memory system (MemPalace, Mem0, Zep/Graphiti, Claude Code sessions, folders) via a pluggable provider protocol. It doesn't own your data. It extracts concepts, runs nature-inspired algorithms over them, and produces grounded wisdom with evidence trails. When the graph doesn't support an insight, it refuses rather than hallucinating.

Memory is commoditized now. What's missing is the layer above memory: the one that extracts concepts, detects patterns across sessions and projects, finds contradictions, surfaces recurring themes, and generates actionable insight with full evidence trails. Extended-thinking is that layer.

### Two audiences

1. **Memory synthesis** (humans). The original target: ingest conversations and notes, let ET find the shape of your thinking, surface insights you wouldn't catch yourself.
2. **Research-backbone** (programs). ET as a typed, bitemporal, queryable state store for LLM-driven research loops, workflow engines, and typed archives. Driven by [ADR 013](docs/ADR/013-research-backbone-audience.md); canonical reference consumer: [autoresearch-ET](../autoresearch-ET).

Both audiences share the same graph, namespace-isolated. Types in both come from the [malleus](../malleus) root ontology.

```
External Data Sources (you own these, we adapt)
├── Claude Code sessions
├── MemPalace drawers
├── Mem0
├── Zep / Graphiti
├── Folders (.md/.txt)
└── Future: Obsidian, Notion, custom

         ▼ MemoryProvider protocol

EXTENDED-THINKING (the thinking layer, we own this)
├── VectorStore (ChromaDB)  ▸ semantic retrieval over raw chunks
├── GraphStore (Kuzu)       ▸ bitemporal knowledge graph
├── Pluggable algorithms    ▸ decay, activation, bow-tie, recombination
└── Wisdom synthesis        ▸ grounded cross-concept insight via Opus

         ▼ MCP / HTTP / CLI

Claude Code, your tools, your workflows
```

## Origin and authorship

Extended-thinking was extracted from private work on building a personal cognitive layer over Claude Code sessions and markdown notes. The library is designed and maintained by [Kieleth](https://github.com/Kieleth), implemented primarily using Claude Code (Anthropic's Opus model). Architecture, design decisions, and validation are human-directed; code generation, documentation, and test implementation are AI-assisted. The git history begins at the open-source extraction; earlier iteration happened in private.

Algorithm design is grounded in research papers (Tero et al. for Physarum decay, van den Heuvel and Sporns for rich-club structure, Graphiti/Zep for bitemporal KGs) and cited in [docs/research-kg-nature-inspiration.md](docs/research-kg-nature-inspiration.md). The claim "wisdom is grounded or it refuses" is enforced in code, not as marketing: see `pipeline_v2.generate_wisdom` and the invariant test in `tests/test_invariants.py`.

## What extended-thinking is NOT

Scope discipline keeps the library useful:

- **Not a memory system.** It does not own your data. Memory lives in MemPalace, Mem0, Graphiti, Claude Code sessions, or plain folders. ET reads, synthesizes, stores insights in its own KG alongside. Writing insights back into provider stores is disabled (prevents echo loops).
- **Not a search engine.** ChromaDB handles semantic retrieval; ET does not reimplement it.
- **Not a chatbot or agent framework.** ET exposes MCP tools so your existing agent (Claude Code, Codex, custom) drives the synthesis. No conversational loop built in.
- **Not a hosted service.** Local-first, single-user, single-machine. A hosted mode, if it exists, will be a separate project.
- **Not a multi-user system.** One user, one `~/.extended-thinking/` data dir, one secrets file. Multi-tenancy is out of scope.
- **Not an OWL reasoner.** The graph uses typed nodes and edges (malleus ontology) with RDFS-style class hierarchy and bitemporal validity. No subsumption inference, no open-world reasoning.

## What it does today

- **Ingests** from multiple memory systems simultaneously (claude-code + mempalace + folders), filtering out source code (ephemeral) and keeping thinking content (conversations, specs, notes).
- **Extracts concepts** via Haiku, batched for diversity.
- **Builds a living graph** with Physarum-inspired edge decay and Hebbian-inspired reinforcement on traversal.
- **Detects co-occurrence** (concepts in the same chunk, weight 2.0).
- **Spreading activation** replaces BFS for "find related concepts" (respects weights, decays with distance).
- **Sparse active set** shows the top-k concepts by recency × connectivity × frequency.
- **Entity resolution** merges similar concepts with full provenance.
- **Cross-store federation** via SAME_AS bridging when ET and provider share a concept name.
- **Bitemporal edges** (valid_time vs transaction_time) with auto-invalidation on contradiction.
- **Grounded wisdom** via Opus: structured graph context + cross-source reasoning + refusal when the graph doesn't support an insight.
- **MCP tools** for Claude Code: `et_sync`, `et_insight`, `et_concepts`, `et_explore`, `et_graph`, `et_path`, `et_recall`, `et_stats`, `et_shift` (temporal diff), `et_core` (bow-tie), `et_suggest` (missing links), `et_recombine` (DMN serendipity), `et_catalog` (registered algorithms).

## Non-obvious design choices

**Nature-inspired dynamics.** Edges aren't static strings. They carry weight that reinforces on traversal (mycelium, Hebbian LTP) and decays on neglect (ant pheromones, synaptic pruning). The graph self-organizes.

**Bitemporal first.** Every edge tracks valid-time (when the fact was true in the world) and transaction-time (when the system learned it). "You decided Kuzu on April 11" supersedes "you considered SQLite on April 9" — the graph carries history, not just current state.

**Pluggable algorithms.** Decay, activation, entity resolution, bridge detection, bow-tie identification, recombination — each is a plugin. Pick what you want in config. Write your own. Registry pattern, not hardcoded pipeline.

**ET owns Knowledge + Wisdom. Providers own Data + Information.** We read from your memory system, we don't copy raw data. Our graph is analytical, not archival.

**Code is not memory.** Source code is ephemeral. The thinking lives in conversations, markdown, comments. Content filter drops `.py`/`.rs`/`.toml` by default.

**Wisdom is grounded or it refuses.** Opus sees graph structure + source paths. It must compose concepts that can interact in reality, or explain what bridge would need to exist. When the graph doesn't support a grounded insight, the system returns `nothing_novel` instead of hallucinating.

## Research foundations

Every algorithm ties to research. See [docs/research-kg-nature-inspiration.md](docs/research-kg-nature-inspiration.md) for the full synthesis. Key references:

- **Connectome**: van den Heuvel & Sporns 2011 (rich-club), Rasch & Born 2024 (memory consolidation)
- **Mycelium**: Simard 2024 (mother trees and resource flow)
- **Physarum**: Tero et al. (adaptive network conductance)
- **Ant stigmergy**: Nature Comm Eng 2024 (distributed pheromone coordination)
- **Metabolic bow-tie**: BMC Bioinformatics (12 core precursors)
- **Temporal KG**: Graphiti/Zep (arxiv 2501.13956) for bitemporal modeling
- **Spreading activation**: arxiv 2512.15922 (for GraphRAG)
- **CLARION / CoALA**: meta-cognitive architecture

## Install

Requires Python 3.12+. Core dependencies (Kuzu, ChromaDB) are installed automatically.

```bash
pip install extended-thinking

# Or, to add optional providers:
pip install 'extended-thinking[mem0]'
pip install 'extended-thinking[graphiti]'
pip install 'extended-thinking[all]'
```

### Configure

```bash
et config init                       # scaffold config.toml + secrets.toml
$EDITOR ~/.config/extended-thinking/secrets.toml   # add API keys
et config validate                   # confirm it parses
```

`config.toml` is safe to commit (no secrets); `secrets.toml` is written with mode 0600 and should be gitignored.

You can also keep using `.env` with `ANTHROPIC_API_KEY=...` / `OPENAI_API_KEY=...` — both work. Full configuration reference (tiers, drop-ins, per-project overrides, every key): [`docs/configuration.md`](docs/configuration.md).

## Quick start

### One-shot setup across clients

```bash
et init
```

Registers extended-thinking as an MCP server with Claude Code, Claude Desktop, opencode, and Codex CLI (whichever are installed). Idempotent, backs up configs before writing, supports `--dry-run`.

### Via Claude Code (MCP)

After `et init`, restart Claude Code. Extended-thinking's tools are available: `et_sync`, `et_insight`, `et_stats`, `et_core`, `et_suggest`, `et_recombine`, and the rest.

### Via CLI

```bash
et sync           # pull from all detected providers
et stats          # show provider + concept stats
et concepts       # list extracted concepts
et insight        # sync + synthesize wisdom
et config show    # inspect resolved config
et config path    # show every config source ET consults
```

### Via HTTP API

```bash
make dev-api   # starts FastAPI on :8000
curl localhost:8000/api/v2/stats
curl -X POST localhost:8000/api/v2/sync
```

### Auto-save hooks (optional)

Package ships with two Stop/PreCompact hook scripts in `extended_thinking/hooks/`. Point Claude Code at them to trigger automatic sync + synthesis on every N exchanges and before each compaction. Permission-mode aware so rapid accept-edits workflows are not interrupted.

## Architecture

Key decisions recorded as ADRs in [docs/ADR/](docs/ADR/):

1. [Pluggable memory architecture](docs/ADR/001-pluggable-memory.md)
2. [Bitemporal foundation](docs/ADR/002-bitemporal-foundation.md)
3. [Pluggable algorithms](docs/ADR/003-pluggable-algorithms.md)
4. [Configurable models](docs/ADR/004-configurable-models.md)
5. [DMN recombination](docs/ADR/005-dmn-recombination.md)
6. [Link prediction family](docs/ADR/006-link-prediction.md)
7. [Resolution family](docs/ADR/007-resolution-family.md)
8. [Activation family](docs/ADR/008-activation-family.md)
9. [Bridges family](docs/ADR/009-bridges-family.md)

Algorithm catalog with research citations: [docs/ALGORITHMS.md](docs/ALGORITHMS.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

Every new algorithm ships with:
1. An ADR (why, what, tradeoffs)
2. A protocol implementation with name + description + research citation
3. Tests against the protocol contract
4. An entry in [docs/ALGORITHMS.md](docs/ALGORITHMS.md)

## Status

Pre-1.0. Actively evolving. 368 tests passing, 8 algorithm plugins across 7 families, 13 MCP tools, bitemporal KG on Kuzu + ChromaDB vector store. See [CHANGELOG.md](CHANGELOG.md) and [ROADMAP.md](ROADMAP.md).

API stability commitment post-1.0:
- MCP tool names and response shapes: additive only. New keys OK, no renames or removals.
- Algorithm protocol contract: additive only. New AlgorithmMeta fields are fine; existing ones stay.
- Storage schema: additive only. Migrations handle evolution, no breaking column renames.

Pre-1.0, assume anything can change between minor versions.

## Credits and acknowledgements

Extended-thinking stands on a lot of shoulders. Named specifically because generic "inspired by" lists rot fast.

### MemPalace (github.com/milla-jovovich/mempalace)

Three concrete patterns ported directly into ET:

- **Stop/PreCompact blocking-hook protocol.** `hooks/et_save_hook.sh` and `hooks/et_precompact_hook.sh` keep MemPalace's `stop_hook_active` infinite-loop prevention verbatim. The infinite-loop trick (return `{"decision": "block"}` once, let the next Stop through because `stop_hook_active=true`) is clever enough to steal wholesale.
- **Permission-mode aware skipping.** Hooks bail out in `acceptEdits`, `auto`, and `bypassPermissions` modes so rapid-fire edits do not get blocked mid-workflow. This fix originated during real use of MemPalace and is baked into ET from day one rather than arriving as a later patch.
- **Write-ahead log pattern.** `storage/wal.py` is a JSONL append-only log of storage operations, modeled on MemPalace's `mcp_server.py` WAL. Extended with transaction IDs so multi-step syncs can be audited as a group.

What we did not port, on purpose: the AAAK dialect, the palace/wing/room/drawer metaphor layer, the L0-L3 layered retrieval verbiage, the combo/work/personal mode selector. These are presentation choices that fit MemPalace's aesthetic and work well there. They do not carry weight for ET's synthesis-layer positioning, and adding them would be overhead without payoff. Different product, different shape.

### Graphiti and Zep (getzep/graphiti, getzep)

The OSS reference for bitemporal temporal knowledge graphs. ET's bitemporal foundation (ADR-002, valid_time vs transaction_time) directly borrows the Graphiti formulation. The `GraphitiProvider` is both a reverence and a concrete integration so Graphiti users can run ET on top.

### Mem0, Letta, Cognee, basic-memory, LangMem

Fellow travelers in the OSS memory space. ET competes with none of these, the `Mem0Provider` exists so Mem0 users can run ET on top without migrating storage. The `MemoryProvider` protocol itself learned from watching how these projects shape their Python APIs.

### Research foundations

Every algorithm ties to a paper. See [docs/research-kg-nature-inspiration.md](docs/research-kg-nature-inspiration.md) for the full synthesis. Key references:

- **Connectome rich-club.** van den Heuvel and Sporns 2011, Rasch and Born 2024. Shaped the bow-tie core/periphery work.
- **Mycelium resource flow.** Simard 2024. Shaped edge weight and reinforcement instincts.
- **Physarum adaptive networks.** Tero et al. Shaped edge decay and conductance-like dynamics.
- **Ant stigmergy.** Nature Communications Engineering 2024. Shaped distributed-coordination instincts.
- **Temporal KG.** Graphiti / Zep, arxiv 2501.13956. Shaped bitemporal modeling.
- **Spreading activation.** arxiv 2512.15922. Shaped the GraphRAG-style retrieval.
- **CLARION and CoALA.** Shaped the meta-cognitive architecture framing.

If we used your work and missed the credit, open an issue. We will add it.

## License

**Functional Source License v1.1 (Apache-2.0 Future Change)** — see [LICENSE.md](LICENSE.md).

You can freely use, modify, and redistribute extended-thinking for anything **except** offering it as a commercial managed service that competes with us. Every version converts to Apache-2.0 two years after release, so the long-term ecosystem is fully open.

This is the same approach used by Sentry, MariaDB, and Zep. Designed to protect small projects from being swallowed by large cloud providers without blocking legitimate use.
