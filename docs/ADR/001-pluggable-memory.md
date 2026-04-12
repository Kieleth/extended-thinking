# AD-001: Extended-Thinking as a Pluggable Thinking Layer

**Date:** 2026-03-29
**Status:** Accepted
**Context:** Extended-thinking's capture/storage layer is immature. MemPalace and others solve memory well. Our unique value is the DIKW synthesis pipeline.

---

## Decision

Extended-thinking becomes a **thinking layer** that sits on top of any memory system via a `MemoryProvider` protocol. Memory is commoditized. Thinking is not.

## Market Landscape (April 2026)

### Memory Systems (store + retrieve)

| System | Storage | Recall Score | Cost | Key Strength |
|--------|---------|-------------|------|-------------|
| **MemPalace** | ChromaDB + SQLite KG (local) | 96.6% LongMemEval | Free | Verbatim, offline, 19 MCP tools |
| **Mem0** | Cloud API | ~85% | $19-249/mo | Simple API, managed |
| **Zep/Graphiti** | Neo4j (temporal KG) | 94.8% DMR | Enterprise | Bi-temporal model, 300ms P95 |
| **Letta** | Tiered (RAM/disk) | — | Free | OS memory hierarchy analogy |
| **Cognee** | KG layer | — | Free | Open source, queryable |

### Knowledge Capture (browser/cross-device)

| System | Approach | Key Feature |
|--------|----------|-------------|
| **Recall** | Browser ext → knowledge graph | One-click capture, auto-tagging, spaced repetition |
| **Glasp** | Highlights → knowledge base | AI summaries of highlights |
| **Lenovo Qira** | Cross-device ambient | Fused knowledge base across all devices |
| **Dia** | Browser that learns patterns | Adapts assistance to your work patterns |

### What NOBODY does

None of these systems:
1. Extract concepts across ALL your AI interactions (not just one tool)
2. Detect recurring themes, tensions, and contradictions in your thinking
3. Generate novel insights ("you solve problems at the wrong altitude")
4. Track whether you acted on advice and reflect on outcomes
5. Show your cognitive graph spatially

**That is extended-thinking's unique value.**

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│            EXTENDED-THINKING (Thinking Layer)             │
│                                                           │
│  ┌───────────┐  ┌────────────┐  ┌─────────┐  ┌───────┐ │
│  │ Extract    │  │ Pattern    │  │ Wisdom  │  │ UI    │ │
│  │ (Haiku)    │→ │ Detect     │→ │ (Opus)  │→ │Canvas │ │
│  │ concepts   │  │ co-occur   │  │ synth   │  │+Timeline│
│  │ from chunks│  │ cross-ctx  │  │ reflect │  │+MCP   │ │
│  └───────────┘  └────────────┘  └─────────┘  └───────┘ │
│       ↑ reads                        ↓ writes            │
│  ┌─────────────────────────────────────────────────────┐ │
│  │            MemoryProvider (Protocol)                  │ │
│  │                                                       │ │
│  │  search(query, limit) → list[MemoryChunk]            │ │
│  │  get_recent(since, limit) → list[MemoryChunk]        │ │
│  │  get_entities() → list[Entity]                       │ │
│  │  store_insight(title, desc, concepts) → str           │ │
│  │  get_insights() → list[MemoryChunk]                  │ │
│  │  get_stats() → dict                                  │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────┬──────────────────────────────────┘
                       │ adapters
        ┌──────────────┼──────────────┬──────────────┐
        ▼              ▼              ▼              ▼
┌──────────────┐ ┌──────────┐ ┌──────────────┐ ┌─────────┐
│  MemPalace   │ │  Claude  │ │  Obsidian    │ │  Folder │
│  Provider    │ │  Code    │ │  Provider    │ │ Provider│
│              │ │ Provider │ │              │ │         │
│ ChromaDB +   │ │ ~/.claude│ │ vault/*.md   │ │ *.md    │
│ SQLite KG    │ │ JSONL    │ │ + [[links]]  │ │ *.txt   │
└──────────────┘ └──────────┘ └──────────────┘ └─────────┘
```

### Internal Concept KG (extended-thinking owns this)

The thinking layer maintains its OWN graph of extracted concepts, patterns, and wisdom. This is NOT the memory — it's the analysis.

```
Concept nodes:
  - name, category (topic/theme/question/decision/tension/entity)
  - frequency, first_seen, last_seen
  - source_quote (verbatim user words)
  - source_provider (which MemoryProvider it came from)

Wisdom nodes:
  - title, why, action
  - based_on_sessions, based_on_concepts, new_evidence_count
  - status (pending/seen/acted)
  - feedback (user responses)

Edges:
  - ABOUT (session → concept)
  - RELATES_TO (concept → concept, weight)
  - SUGGESTS_EXPLORING (wisdom → concept)
  - REINFORCES (feedback → concept)
```

Storage: SQLite (simple, no Silk/redb complexity). Or keep Silk for the graph algorithms (BFS, subgraph, pattern_match) but use in-memory mode with periodic JSON export.

---

## MemoryProvider Protocol

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass
class MemoryChunk:
    """A piece of content from the memory system."""
    id: str                  # unique identifier
    content: str             # the actual text
    source: str              # where it came from (file, session, URL)
    timestamp: str           # ISO 8601 when created/captured
    metadata: dict           # provider-specific (wing, room, agent, etc.)


@dataclass
class Entity:
    """A named entity from the memory system."""
    name: str
    entity_type: str         # person, project, tool, concept
    properties: dict         # provider-specific


class MemoryProvider(Protocol):
    """Interface for any memory system that extended-thinking can read from."""

    name: str

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        """Semantic or keyword search across all memories."""
        ...

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        """Get recent memories, optionally since a timestamp."""
        ...

    def get_entities(self) -> list[Entity]:
        """Get known entities (people, projects, tools)."""
        ...

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        """Store a generated insight back into the memory system.
        Returns the ID of the stored insight."""
        ...

    def get_insights(self) -> list[MemoryChunk]:
        """Retrieve previously stored insights."""
        ...

    def get_stats(self) -> dict:
        """Stats: total memories, entities, insights, last_updated."""
        ...
```

---

## Provider Implementations

### MemPalaceProvider

```
Reads from:
  ~/.mempalace/palace/        → ChromaDB (mempalace_drawers collection)
  ~/.mempalace/knowledge_graph.sqlite3 → entities + triples

search(query) → chromadb.query(query_texts=[query], n_results=limit)
get_recent(since) → chromadb.get(where={"date_mined": {"$gte": since}})
get_entities() → SELECT * FROM entities
store_insight(title, desc) → add_drawer(wing="wing_wisdom", room="insights", content=...)
                            + INSERT INTO triples (wisdom, suggests_exploring, concept)
get_insights() → chromadb.get(where={"wing": "wing_wisdom"})
get_stats() → collection.count() + entity count + wing/room counts
```

### ClaudeCodeProvider (fallback)

```
Reads from:
  ~/.claude/projects/*/[uuid].jsonl

search(query) → scan JSONL files, keyword match on message content
get_recent(since) → sort by file mtime, parse recent sessions
get_entities() → [] (no entity extraction without LLM)
store_insight(title, desc) → write to ~/.extended-thinking/insights/[id].json
get_insights() → read from ~/.extended-thinking/insights/*.json
get_stats() → count sessions, count files
```

### FolderProvider (zero-dep)

```
Reads from:
  user-specified directory of .md/.txt files

search(query) → keyword search across file contents
get_recent(since) → sort by mtime
get_entities() → [] 
store_insight(title, desc) → write to _insights/ subdirectory
get_insights() → read from _insights/*.md
get_stats() → file count, total size, last modified
```

### AutoProvider (zero-config, built-in)

The default. No external dependencies. Works out of the box.

```
pip install extended-thinking
et init       # scans for data
et insight    # first insight
```

**Detection order:**
1. Check for ~/.mempalace/ → if exists, use MemPalaceProvider (best quality)
2. Check for ~/.claude/projects/ → if exists, use ClaudeCodeProvider
3. Fall back to FolderProvider on ~/Documents or cwd

**How it works:**
  Auto-detects data sources on disk
  Combines: Claude Code sessions + local .md/.txt + ChatGPT exports
  Chunks by Q+A pairs (conversations) or paragraphs (files)
  Basic keyword search (no embeddings, no ChromaDB dependency)
  Stores insights as JSON in ~/.extended-thinking/insights/

**What the user sees:**
  $ et init
  Scanning for data sources...
    Found: ~/.claude/projects/ (52 sessions, ~15k messages)
    Found: ~/Documents/notes/ (34 markdown files)

  Using built-in provider (no external memory system needed).
  Tip: Install mempalace for 96.6% retrieval quality: pip install mempalace

  Extracting concepts... done (142 concepts from 52 sessions)
  Your first insight is ready. Run `et insight` or open http://localhost:3001

**Quality comparison:**
  - AutoProvider (built-in): keyword search, no entities, no temporal. Works.
  - MemPalaceProvider: semantic search, entity graph, temporal KG. Works better.

  The built-in provider is "good enough to be useful, honest about its limits,
  and encourages upgrading." Like SQLite vs Postgres — start simple, graduate
  when you need to.

**Future option: build our own**
  If after using mempalace we find cracks (ChromaDB version pinning, AAAK
  regression, missing features), we have the option to build a proper built-in
  memory layer. The MemoryProvider protocol means we can swap without touching
  the DIKW pipeline. But we don't build this until we've genuinely tried the
  ecosystem and found it lacking.
```

### Future: ObsidianProvider

```
Reads from:
  Obsidian vault directory

search(query) → keyword search + follow [[wikilinks]]
get_recent(since) → sort by mtime
get_entities() → extract from [[links]] and tags
store_insight() → create new .md in vault with [[links]] to related concepts
```

### Future: ZepProvider

```
Reads from:
  Zep/Graphiti REST API or Neo4j direct

search(query) → Zep hybrid search (semantic + BM25 + graph traversal)
get_recent(since) → temporal query with validity windows
get_entities() → graph entity nodes with relationships
store_insight() → add fact to temporal KG
```

---

## What We Keep From Extended-Thinking

### Processing Pipeline (core value)
- `extractor.py` — Haiku concept extraction (reads MemoryChunks, not Silk fragments)
- `graph_builder.py` — concept dedup, co-occurrence relationships
- `pattern_detector.py` — frequency, cross-context, contradictions
- `wisdom.py` — Opus synthesis with honesty, reflection, feedback, prompt caching

### UI
- React Flow canvas (force-directed, DIKW bands, birth animations)
- Insight timeline in sidebar
- Feedback mechanism
- Detail panel with provenance

### Product Invariants
1. Every insight traces to evidence
2. Never forget what it's seen
3. Enrichment is always relevant
4. Distinguish user's words from system's inferences
5. Processing never blocks the user

### Tests
- 42 existing tests (store, capture, processing, invariants, pipeline)
- Adapt to new provider-based architecture

## What We Drop

- Silk/redb as primary storage (replace with SQLite or keep Silk in-memory)
- Our capture layer (providers handle this)
- Our session parsing code (moved into ClaudeCodeProvider)
- The redb stale-lock recovery (no longer needed)

## What We Add

- `providers/` directory with MemoryProvider protocol
- `providers/auto.py` — AutoProvider (zero-config, detects best available source)
- `providers/mempalace.py` — MemPalaceProvider
- `providers/claude_code.py` — ClaudeCodeProvider (existing code, refactored)
- `providers/folder.py` — FolderProvider
- Config: `~/.extended-thinking/config.yaml` with `provider: auto` (default)
- CLI: `et init`, `et insight`, `et status`, `et sync`

---

## Implementation Status (2026-03-29)

### Completed — 129 tests, all green

| Component | File | Tests | Status |
|-----------|------|-------|--------|
| Protocol | `providers/protocol.py` | 7 | Frozen dataclasses, structural typing |
| FolderProvider | `providers/folder.py` | 14 | .md/.txt, keyword search, insight storage |
| ClaudeCodeProvider | `providers/claude_code.py` | 15 | JSONL parsing, exchange-pair chunking |
| AutoProvider | `providers/auto.py` | 7 | Auto-detection, priority delegation |
| Registry | `providers/__init__.py` | 5 | `get_provider(config)` factory |
| ConceptStore | `processing/concept_store.py` | 11 | SQLite: concepts, relationships, wisdom, feedback |
| Extractor v2 | `processing/extractor.py` | 10 | `extract_concepts_from_chunks()` |
| Pipeline v2 | `processing/pipeline_v2.py` | 6 | sync → extract → wisdom |
| API v2 | `api/routes/pipeline_v2.py` | 6 | REST: /v2/sync, /v2/insight, /v2/concepts, /v2/stats |
| Integration | `tests/test_provider_integration.py` | 6 | Synthetic session → concepts end-to-end |
| Real data | verified manually | — | 1,295 chunks from 39 real Claude Code sessions |

### Architecture

```
providers/protocol.py     ← MemoryProvider interface (duck-typed)
providers/folder.py       ← Reads .md/.txt files
providers/claude_code.py  ← Reads ~/.claude/projects/*.jsonl
providers/auto.py         ← Detects best available, delegates
providers/__init__.py     ← get_provider(config) registry

processing/concept_store.py  ← SQLite: concepts, relationships, wisdom
processing/extractor.py      ← Haiku extraction from MemoryChunks
processing/pipeline_v2.py    ← Full DIKW pipeline

api/routes/pipeline_v2.py   ← REST API (/api/v2/*)
```

### API v2 Endpoints

```
POST /api/v2/sync           — pull from provider, extract concepts
POST /api/v2/insight        — full flow: sync + wisdom (background)
GET  /api/v2/insight/status — poll insight generation
GET  /api/v2/concepts       — list extracted concepts
GET  /api/v2/wisdoms        — list generated wisdoms
POST /api/v2/feedback       — submit feedback on wisdom
GET  /api/v2/stats          — pipeline + provider stats
```

---

## Build Sequence

### Phase 1: Protocol + AutoProvider (zero-config)
- Define MemoryProvider protocol
- Implement FolderProvider (scan .md/.txt, keyword search)
- Implement ClaudeCodeProvider (refactor existing JSONL parsing)
- Implement AutoProvider (detects ~/.claude, ~/Documents, picks best)
- Refactor extractor to read from provider
- Test: `et init` on a fresh machine finds Claude Code sessions, extracts concepts
- **Goal: `pip install extended-thinking && et init && et insight` works with zero config**

### Phase 2: MemPalaceProvider
- Detect ~/.mempalace/ presence
- Read from ChromaDB (mempalace_drawers) + SQLite KG (entities/triples)
- Store insights back as drawers in wing_wisdom + KG triples
- AutoProvider prefers MemPalaceProvider when mempalace is installed
- Test: extract concepts from real mempalace data, write wisdom back, verify in mempalace

### Phase 3: Refactor Pipeline
- Wisdom reads from provider, not Silk
- Internal concept graph storage (SQLite — simpler than Silk for this use case)
- Pattern detection on internal graph
- Test: full DIKW pipeline via AutoProvider → MemPalaceProvider fallback chain

### Phase 4: Wire UI
- Canvas reads from internal concept graph
- "Sync from [provider]" replaces "Import Claude Code"
- Provider stats in sidebar (which provider, memory count, last sync)
- Timeline reads from internal + provider
- Test: canvas shows concepts extracted from mempalace data

### Phase 5: CLI + polish
- `et init` — detect sources, configure provider
- `et insight` — run DIKW pipeline, print wisdom to terminal
- `et status` — show graph stats, provider info
- `et sync` — pull new data from provider
- `et serve` — start web UI (FastAPI + Next.js)
- Test: full CLI workflow without web UI

---

## Cross-Device Vision (Future)

Extended-thinking runs as a local service. Multiple surfaces connect to it:

- **Web canvas** — spatial exploration of your thinking graph
- **Claude Code MCP tool** — `wisdom_get_insight`, `wisdom_feedback`
- **Browser extension** — thin "insight surface" in toolbar, shows recent wisdom
- **CLI** — `et insight` / `et status` / `et sync`

The browser extension is NOT a capture tool (mempalace/Recall do that). It's a **display surface** for the thinking layer. It shows:
- "3 new concepts detected from your work today"
- "Your latest insight: [title]. Tap to explore."
- "You've been working on X across 3 projects. Want a fresh perspective?"

Cross-device: the internal concept graph syncs via file (SQLite) or Silk snapshots. The browser extension reads from the same graph as the web canvas.

---

## References

- [MemPalace vs Mem0 Comparison](https://www.mempalace.tech/compare/mempalace-vs-mem0)
- [Zep Temporal KG Architecture](https://arxiv.org/abs/2501.13956)
- [5 AI Agent Memory Systems Compared](https://dev.to/varun_pratapbhardwaj_b13/5-ai-agent-memory-systems-compared-mem0-zep-letta-supermemory-superlocalmemory-2026-benchmark-59p3)
- [Recall AI Personal Knowledge Base](https://www.getrecall.ai/)
- [Lenovo Qira Cross-Device Intelligence](https://news.lenovo.com/pressroom/press-releases/lenovo-unveils-lenovo-and-motorola-qira/)
- [Graphiti Open Source](https://www.getzep.com/product/open-source/)
