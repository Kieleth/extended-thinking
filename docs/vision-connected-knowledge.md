# Vision: Connected Knowledge — Concepts Don't Live in Isolation

**Date:** 2026-04-11
**Status:** Vision document, not yet implemented

## The Insight

Concepts and insights in isolation are not useful. A concept like "additive-only extensions" is interesting, but it becomes powerful when it connects to:

1. **Your own thinking** — other concepts, decisions, tensions in your KG (we have this)
2. **Established knowledge** — Wikipedia, textbooks, prior art that contextualizes your concept
3. **Other people's thinking** — someone else working on ontology systems hit the same tension
4. **Academic research** — arXiv papers, conference talks that formalize what you're intuiting

## Three Layers of Connection

### Layer 1: Internal KG (built, working)
Your concepts connected to your sessions, your evidence, your wisdom.
```
[your concept] --RELATES_TO--> [your other concept]
[your concept] --INFORMED_BY--> [your session fragment]
[wisdom] --SUGGESTS_EXPLORING--> [your concept]
```

### Layer 2: External Knowledge (next)
Your concepts linked to established knowledge sources.
```
[your concept: "additive-only extensions"] 
    --RELATED_TO--> [wikipedia: "Open-closed principle"]
    --RELATED_TO--> [wikipedia: "Append-only database"]
    --CITED_IN--> [arXiv: "Temporal Knowledge Graphs for Agent Memory"]
```

**How it works:**
- When a concept is extracted, search Wikipedia/Wikidata for related articles
- Use LLM to judge relevance (not just keyword match)
- Store as edges: concept → external_source
- The mind map shows: "This thing you're thinking about? Here's what the field calls it."

**Reference:** Check ../kieleth for the Wikipedia-linking approach already built.

### Layer 3: Social Knowledge (future, killer feature)
Concepts float between extended-thinking users.
```
[your concept: "ontology as type system"]
    --SIMILAR_TO--> [user:alice concept: "schema as contract"]
    --SIMILAR_TO--> [user:bob concept: "types as documentation"]
```

**How it works:**
- Opt-in: users choose which concepts to share (privacy first)
- Concept embeddings published to a shared index (anonymized)
- When your concept has high similarity to someone else's, suggest the connection
- "3 other people are thinking about ontology-as-type-system. One of them solved the versioning problem."

**Privacy model:**
- Concepts are shared, not sessions or source quotes
- Users control what's visible: public / friends-only / private
- No raw data leaves the machine — only concept embeddings + metadata

## The MCP Explorer

The mind map becomes a KG explorer. Each node is explorable:

```
et_insight          → top-level mind map (wisdom + evidence)
et_explore <node>   → drill into concept (connections, quotes, external links)
et_related <node>   → show Wikipedia/arXiv connections
et_similar <node>   → show what other users think about this (Layer 3)
et_path A B         → show how two concepts connect through the KG
et_graph            → full KG overview (concept count, clusters, bridges)
```

Progressive disclosure at every level:
- Mind map → "explore X" → concept detail → "related" → Wikipedia + arXiv → "similar" → other users

## External Knowledge Sources

| Source | What it provides | Access |
|--------|-----------------|--------|
| Wikipedia/Wikidata | Canonical definitions, related concepts | REST API, free |
| arXiv | Academic papers, formal treatments | REST API, free |
| Semantic Scholar | Citation graph, impact, related papers | REST API, free tier |
| HackerNews | Community discussions, tools, trends | Algolia API, free |
| GitHub | Related projects, implementations | REST API, free |
| CrossRef | DOIs, bibliographic metadata | REST API, free |

## Implementation Path

### Phase 1: KG Explorer (MCP tools)
- `et_graph` — overview of concept clusters
- `et_path` — find connections between concepts
- More relationship types in ConceptStore
- Mind map renders from actual KG topology

### Phase 2: Wikipedia/Wikidata linking
- On concept extraction, search Wikipedia API for related articles
- LLM judges relevance
- Store as external_links in ConceptStore
- `et_related` shows external connections

### Phase 3: arXiv/Semantic Scholar
- Search by concept name + description
- Show relevant papers with abstracts
- "Your thinking about X connects to [paper title]"

### Phase 4: Social layer
- Opt-in concept sharing
- Embedding-based similarity index
- `et_similar` shows anonymized connections
- "3 others are exploring this space"

## Why This Matters

The product thesis was: "memory is commoditized, thinking is not."

But thinking in isolation is limited. The next level:
**"Your thinking, connected to all of human knowledge and to everyone else thinking about the same things."**

Extended-thinking becomes not just a personal cognitive mirror, but a node in a global thinking network. Your insights link to Wikipedia, to research papers, to other practitioners. The mind map isn't just your KG — it's a window into the collective intelligence around your specific questions.
