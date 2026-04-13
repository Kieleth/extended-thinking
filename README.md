# extended-thinking

[![CI](https://github.com/Kieleth/extended-thinking/actions/workflows/tests.yml/badge.svg)](https://github.com/Kieleth/extended-thinking/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/extended-thinking.svg)](https://pypi.org/project/extended-thinking/)
[![Python](https://img.shields.io/pypi/pyversions/extended-thinking.svg)](https://pypi.org/project/extended-thinking/)
[![License](https://img.shields.io/badge/license-FSL--1.1--Apache--2.0-blue.svg)](https://github.com/Kieleth/extended-thinking/blob/main/LICENSE.md)

A quiet friend who's been reading your thinking and will point at the one pattern you hadn't noticed.

Extended-thinking reads what you already write (Claude Code sessions, Copilot Chat, Cursor history, markdown notes, CLAUDE.md files scattered across your repos) and builds a concept graph from it. Once you've got a couple weeks of corpus, `et insight` surfaces a noticing in plain English, with the source quotes that back it up. No advice, no Jira tickets, no mind maps. A sentence like "you keep reaching for docs before the code is stable" with three quotes attached so you can see where it came from.

```
  You keep building proof that things are solid before the things
  themselves are solid.

  Across Silk, Malleus, and Jaguar, the thing you keep coming back to
  isn't performance, it's legibility. With Silk you're writing careful
  docs about what it is and isn't. With Malleus you're pushing for
  changelogs and release diligence. With Jaguar the sell isn't "matrix
  math is faster than pandas", it's that you can trace a number back
  to the business rule that produced it...

  seen in:

    ◦ Changelog documentation practice
      "do we have a changelog? that'd force us to be a bit more diligent"
      · 2d ago · claude-code

    ⚡ Early-stage correctness concerns
      "the changelog shows several correctness-relevant fixes this week"
      · 4d ago · claude-code

    ◈ Transparent scope documentation
      "It assumes trusted peer networks and explicitly does not protect"
      · today · folder (~/vault/notes)

  maybe: would you try writing the one-paragraph version of this and
  seeing if it still reads clean?
```

That's what a morning with ET looks like.

## Who is this for?

If you generate a lot of text-based thinking (Claude Code power-user, journaller, researcher, someone with a vault of markdown notes) and want a second brain that remembers what your actual brain forgets, extended-thinking will earn its keep. The minimum useful corpus is roughly a few weeks of conversations or a notes folder of real substance. Less than that, the graph stays too sparse to surface anything interesting.

It isn't for everyone. If you use Claude Code for one-off questions (recipes, trip planning), ET has no signal to chew on. If you write a lot in private apps that don't export (iMessage, paper notebooks), the pipeline never sees it. Honest about the fit: ET is a thinking tool for people who already think in text.

Two audiences share the same system, namespace-isolated:

1. **You, the human.** Ingest your conversations and notes, let ET notice patterns, surface them as grounded noticings. This is the default.
2. **Programs.** ET as a typed, bitemporal, queryable state store for LLM-driven research loops, workflow engines, and typed archives. Driven by [ADR 013](docs/ADR/013-research-backbone-audience.md); the canonical reference consumer is [autoresearch-ET](../autoresearch-ET).

Both audiences use the same Kuzu graph and the same [malleus](../malleus) root ontology. You'll likely only need the first one.

## Install and first run

Requires Python 3.12 or later. Kuzu and ChromaDB install automatically.

```bash
pip install extended-thinking
et wizard     # interactive setup — pick providers, set API key, wire up MCP
et sync       # pull from everything it detected
et insight    # generate your first noticing
```

The wizard walks you through provider selection (Claude Code, Copilot Chat, Cursor, markdown folders, optional Mem0 / Graphiti / MemPalace), API key entry, and MCP registration with Claude Code / Claude Desktop / opencode / Codex CLI. Idempotent and safe to re-run.

If you prefer config files over wizards:

```bash
et config init                                   # scaffold config.toml + secrets.toml
$EDITOR ~/.config/extended-thinking/secrets.toml # add ANTHROPIC_API_KEY
et config validate                               # confirm it parses
et init                                          # register MCP non-interactively
```

Run `et doctor` any time to see what's wired and what's missing. Full configuration reference in [docs/configuration.md](docs/configuration.md).

## Using it day-to-day

The CLI is for housekeeping. The actual interface is the MCP tools inside Claude Code. After `et init` and a CC restart, your session picks up roughly twenty new tools. The most-used three:

- `et_recall("kuzu")` searches your concept graph for anything about Kuzu. Returns the concepts, their source quotes, and which sessions they came from. A memory that answers "what did I decide about this last month?".
- `et_explore("kuzu")` opens one concept: its description, its connections, which wisdom cites it.
- `et_insight` triggers a noticing. Same output shape as the CLI's `et insight`.

Other tools cover path-finding between concepts, bridge-concept detection, algorithm catalog, typed writes for programmatic consumers. See [docs/MCP-tools.md](docs/MCP-tools.md) for the full surface.

Source feeds you can opt into:

- **Claude Code, Copilot Chat, Cursor, ChatGPT export** — auto-detected from the usual paths
- **Markdown folders** — flat (`~/vault/notes`) or recursive-per-project via `[providers.projects] roots = ["~/code", "~/Projects"]` with a `.git` gate so you only harvest real projects
- **Mem0, Graphiti / Zep, MemPalace** — optional integrations, install with `pip install 'extended-thinking[mem0]'` etc.

Each source gets its own namespace so notes from `~/vault/notes` stay separate from notes under `~/writing`, and `~/code/malleus/CLAUDE.md` stays separate from `~/code/autoresearch-ET/CLAUDE.md`.

## What's under the hood

```
External sources (you own these, we adapt)
├── Claude Code sessions
├── Copilot Chat, Cursor, ChatGPT exports
├── Markdown folders (flat or per-project)
└── Optional: Mem0, Graphiti / Zep, MemPalace

         ▼  MemoryProvider protocol

EXTENDED-THINKING (the thinking layer)
├── VectorStore (ChromaDB)   semantic retrieval over raw chunks
├── GraphStore (Kuzu)        bitemporal typed graph, malleus ontology
├── Pluggable algorithms     decay, activation, resolution, link prediction
└── Wisdom synthesis         grounded noticings via Opus, with audit trail

         ▼  MCP / HTTP / CLI

Claude Code, your shell, your scripts
```

A few choices worth knowing about:

**Wisdom is grounded or it refuses.** Opus sees the graph structure and the source paths. The `et_write_rationale` MCP tool literally won't commit a rationale whose cited concepts don't resolve in the graph. When the graph can't support a real noticing, the system returns `nothing_novel` instead of hallucinating one. This is enforced in code (see `tests/test_invariants.py` and `tests/acceptance/test_provenance_chain.py`), not just in the pitch.

**Bitemporal first.** Every edge carries both valid-time (when the fact was true in the world) and transaction-time (when the system learned it). "You decided Kuzu on April 11" supersedes "you considered SQLite on April 9" without erasing either. The graph carries history, not just current state.

**Nature-inspired dynamics.** Edges aren't static strings. They reinforce on traversal (Hebbian LTP, mycelium), decay on neglect (ant pheromones, synaptic pruning), and the graph self-organises into a bow-tie topology with rich-club hubs. Every algorithm cites a paper; see [docs/research-kg-nature-inspiration.md](docs/research-kg-nature-inspiration.md).

**Code is not memory.** Source files are ephemeral. The thinking lives in conversations, markdown, code comments. Content filter drops `.py`, `.rs`, `.toml`, and friends by default.

**ET owns Knowledge and Wisdom. Providers own Data and Information.** ET reads from your memory system and doesn't copy raw chunks into its own store; it extracts concepts, stores them in its own graph, and leaves the originals where they live. Writing synthesis back into provider stores is disabled by default to prevent echo loops.

## What it is not

- **Not a memory system.** Memory is commoditised (MemPalace, Mem0, Graphiti do it well). ET is the synthesis layer above memory.
- **Not a search engine.** ChromaDB handles semantic retrieval; ET doesn't reimplement it.
- **Not an agent framework.** ET exposes MCP tools. Your existing agent (Claude Code, Codex, custom) drives the loop.
- **Not a hosted service.** Local-first, single-user, single-machine.
- **Not an OWL reasoner.** Typed graph with RDFS-style hierarchy and bitemporal validity. No subsumption inference, no open-world reasoning.

## Status

Pre-1.0. Actively evolving. Around 900 tests passing across unit, acceptance, snapshot, and Hypothesis property layers. Thirteen algorithm plugins across eight families. Twenty MCP tools. Bitemporal Kuzu graph + ChromaDB vectors.

See [CHANGELOG.md](CHANGELOG.md) and [ROADMAP.md](ROADMAP.md). Architecture decisions recorded as ADRs in [docs/ADR/](docs/ADR/).

Post-1.0, the API stability commitment is: MCP tool names and response shapes are additive only, the algorithm protocol is additive only, and storage schema changes go through bitemporal migrations rather than breaking column renames. Pre-1.0, assume anything can change between minor versions.

## Origin

Extended-thinking was extracted from private work building a personal cognitive layer over Claude Code sessions and a vault of markdown notes. Design, architecture, and validation are human-directed; code generation, documentation, and tests are AI-assisted via Claude Code. The git history begins at the open-source extraction.

Three libraries left visible fingerprints. [MemPalace](https://github.com/milla-jovovich/mempalace) contributed the Stop / PreCompact blocking-hook protocol, the permission-mode-aware skipping, and the write-ahead log pattern (ported directly, not reimagined). [Graphiti / Zep](https://github.com/getzep/graphiti) is the OSS reference for bitemporal temporal knowledge graphs; ET's bitemporal foundation in ADR 002 uses Graphiti's valid-time / transaction-time split. [malleus](../malleus) provides the root ontology (Entity / Event / Signal / Relation) that every ET class descends from.

If ET uses your work and we missed the credit, open an issue. We'll add it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every new algorithm ships with an ADR (why, what, tradeoffs), a protocol implementation with a research citation, tests against the protocol contract, and an entry in [docs/ALGORITHMS.md](docs/ALGORITHMS.md).

## License

Functional Source License v1.1, converting to Apache-2.0 two years after each release. See [LICENSE.md](LICENSE.md).

You can freely use, modify, and redistribute for anything except offering ET as a commercial managed service competing with us. Same approach as Sentry, MariaDB, and Zep. Designed to keep small projects from being swallowed by large cloud providers without blocking legitimate use.
