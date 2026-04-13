"""Extended-Thinking MCP Server.

Provides tools for Claude Code to access the DIKW pipeline directly.
The primary tool is `et_insight` — generates and renders wisdom with
evidence trail as formatted text.

Usage:
  claude mcp add extended-thinking -- python -m extended_thinking.mcp_server

Tools:
  et_insight  — Sync + generate wisdom. Returns formatted insight with evidence.
  et_concepts — List extracted concepts with frequencies.
  et_sync     — Pull new data from memory provider.
  et_stats    — Pipeline and provider statistics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_pipeline():
    """Lazy pipeline initialization via StorageLayer."""
    from extended_thinking.config import migrate_data_dir, settings
    from extended_thinking.processing.pipeline_v2 import Pipeline
    from extended_thinking.providers import get_provider
    from extended_thinking.storage import StorageLayer

    data_dir = migrate_data_dir(settings)
    storage = StorageLayer.default(data_dir)
    return Pipeline.from_storage(get_provider(), storage)


def _get_unified_graph(pipeline):
    """Build a UnifiedGraph from the pipeline's store + provider KG."""
    from extended_thinking.processing.unified_graph import UnifiedGraph
    kg_view = None
    if hasattr(pipeline.provider, "get_knowledge_graph"):
        kg_view = pipeline.provider.get_knowledge_graph()
    return UnifiedGraph(pipeline.store, kg_view)


MARKERS = {"topic": "◦", "theme": "◈", "decision": "◇", "tension": "⚡", "question": "?", "entity": "●"}

WHY_LABELS = ["WHY", "BECAUSE", "THE PATTERN", "UNDERNEATH", "WHAT'S HIDDEN"]
DO_LABELS = ["DO", "TRY THIS", "THE MOVE", "NEXT STEP", "WHAT TO BUILD"]
PROMPTS = [
    'Say "tell me more" to explore any concept, or "why" for the full reasoning.',
    'Pick a node to zoom in. Or say "deeper" for the full analysis.',
    'Any of these ring true? Point at one and I\'ll unpack it.',
    '"Expand" any concept, or "challenge" this if it feels off.',
]

_call_count = 0


def _render_insight(wisdom: dict, concepts: list[dict]) -> str:
    """Render a dynamic mind map with the insight crystallizing from evidence.

    The topology is generated from the actual concept relationships,
    not a static template. Each call varies labels and prompts.
    """
    global _call_count
    _call_count += 1

    title = wisdom.get("title", "Untitled")
    description = wisdom.get("description", "")
    related_ids = set(wisdom.get("related_concept_ids", []))
    related = [c for c in concepts if c["id"] in related_ids]
    if not related:
        related = concepts[:5]

    # Extract why/action from description
    why_text = ""
    action_text = ""
    if "**Why:**" in description and "**Action:**" in description:
        parts = description.split("**Action:**")
        why_text = parts[0].replace("**Why:**", "").strip()
        action_text = parts[1].strip() if len(parts) > 1 else ""
    else:
        why_text = description

    # Compress to 1-2 sentences
    why_short = ". ".join(why_text.split(". ")[:2]).strip()
    if why_short and not why_short.endswith("."):
        why_short += "."
    action_short = ". ".join(action_text.split(". ")[:2]).strip()
    if action_short and not action_short.endswith("."):
        action_short += "."

    # Pick varied labels
    why_label = WHY_LABELS[_call_count % len(WHY_LABELS)]
    do_label = DO_LABELS[_call_count % len(DO_LABELS)]
    prompt = PROMPTS[_call_count % len(PROMPTS)]

    # Build the mind map dynamically based on concept count
    lines = []
    n = len(related)

    if n >= 5:
        # Star topology: concepts radiate, insight at center bottom
        c = [f"{MARKERS.get(r['category'], '.')} {r['name']}" for r in related]
        lines.append(f"       {c[0]}")
        lines.append(f"      /")
        lines.append(f"  {c[1]} ---*--- {c[2]}")
        lines.append(f"      \\      /")
        lines.append(f"       \\    /")
        lines.append(f"    {c[3]} -'")
        if n > 4:
            lines.append(f"          \\")
            lines.append(f"     {c[4]}")
            lines.append(f"            \\")
        else:
            lines.append(f"              \\")
    elif n >= 3:
        # Triangle
        c = [f"{MARKERS.get(r['category'], '.')} {r['name']}" for r in related]
        lines.append(f"  {c[0]} ---*--- {c[1]}")
        lines.append(f"         \\   /")
        lines.append(f"      {c[2]}")
        lines.append(f"            \\")
    elif n >= 1:
        # Simple chain
        for r in related:
            m = MARKERS.get(r["category"], ".")
            lines.append(f"  {m} {r['name']}")
            lines.append(f"       |")
    else:
        lines.append("  (no evidence concepts)")

    # Insight box — simple ASCII, no Unicode box drawing
    title_lines = _word_wrap(title, 48)
    max_w = max(len(tl) for tl in title_lines) + 4
    lines.append(f"       *{'-' * max_w}*")
    for tl in title_lines:
        lines.append(f"       | {tl:<{max_w - 2}} |")
    lines.append(f"       *{'-' * max_w}*")

    lines.append("")

    # Why + Do
    for wl in _word_wrap(f"  {why_label}  {why_short}", 60):
        lines.append(wl)
    lines.append("")
    for al in _word_wrap(f"  {do_label}  {action_short}", 60):
        lines.append(al)

    return "\n".join(lines)


def _render_concepts(concepts: list[dict]) -> str:
    """Render concept list as formatted text."""
    if not concepts:
        return "No concepts extracted yet. Run et_sync first."

    lines = ["CONCEPTS (by frequency)", ""]
    for c in concepts[:20]:
        cat = c["category"]
        marker = {"topic": "◦", "theme": "◈", "decision": "◇", "tension": "⚡", "question": "?", "entity": "●"}.get(cat, "·")
        line = f"  {marker} {c['name']} ({cat}, freq={c['frequency']})"
        lines.append(line)
        if c.get("source_quote"):
            lines.append(f'    "{c["source_quote"][:60]}{"..." if len(c["source_quote"]) > 60 else ""}"')
    return "\n".join(lines)


def _word_wrap(text: str, width: int) -> list[str]:
    """Simple word wrap."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(current)
    return lines or [""]


# ── MCP Protocol ─────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "et_insight",
        "description": "Generate wisdom from the knowledge graph via Opus. Uses graph structure (active set, bridges, clusters) not just concept lists. Returns insight with evidence trail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "description": "Generate wisdom even with few concepts (default: false)",
                    "default": False,
                },
                "skip_sync": {
                    "type": "boolean",
                    "description": "Skip the sync step, generate from existing graph only (default: false)",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "et_concepts",
        "description": "List extracted concepts from your thinking patterns, ordered by frequency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max concepts to return (default: 20)",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "et_explore",
        "description": "Explore a concept or entity across both ET concepts and provider KG. Shows connections, source quote, related wisdom. Works with names from either system.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_name": {
                    "type": "string",
                    "description": "Name of the concept to explore (from et_insight or et_concepts output)",
                },
            },
            "required": ["concept_name"],
        },
    },
    {
        "name": "et_graph",
        "description": "Unified graph overview across ET concepts + provider KG. Shows clusters, bridges, isolated nodes from both systems.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "et_path",
        "description": "Find how two concepts/entities connect across the unified graph (ET + provider KG). BFS shortest path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_concept": {
                    "type": "string",
                    "description": "Starting concept name",
                },
                "to_concept": {
                    "type": "string",
                    "description": "Target concept name",
                },
            },
            "required": ["from_concept", "to_concept"],
        },
    },
    {
        "name": "et_sync",
        "description": "Pull new data from your memory system and extract concepts. Run this after working on projects to update your thinking graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max chunks to pull from providers (default: 100, use 500+ for deep sync)",
                    "default": 100,
                },
            },
        },
    },
    {
        "name": "et_stats",
        "description": "Show pipeline statistics: provider info, concept count, wisdom count.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "et_recall",
        "description": "Semantic search over your past thinking. Searches conversation chunks stored during sync. Returns your actual words with timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for (semantic, not just keyword)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "et_suggest",
        "description": "Suggest missing links: concept pairs similar but not connected in the graph. Choose between fast textual matching or semantic embeddings. Pairs well with et_recombine for grounded verdicts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "algorithm": {
                    "type": "string",
                    "description": "Which link predictor: 'textual_similarity' (fast, string-based) or 'embedding_similarity' (semantic, catches synonyms). Default: textual.",
                    "default": "textual_similarity",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many candidates to surface (default: 10)",
                    "default": 10,
                },
                "threshold": {
                    "type": "number",
                    "description": "Minimum similarity 0.0-1.0. Textual default: 0.5. Embedding default: 0.75.",
                    "default": 0.5,
                },
                "as_of": {
                    "type": "string",
                    "description": "ISO date for point-in-time suggestion (default: current)",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "et_recombine",
        "description": "DMN-inspired: sample cross-cluster concept pairs, ask Opus for grounded verdicts on whether they connect in reality. Returns candidates, not committed edges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "integer",
                    "description": "How many cross-cluster pairs to evaluate (each = 1 LLM call). Default 3.",
                    "default": 3,
                },
                "as_of": {
                    "type": "string",
                    "description": "ISO date for point-in-time recombination (default: current).",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "et_core",
        "description": "Identify bow-tie core concepts: the 'metabolic center' of your thinking. Concepts attested by many sources AND feeding many downstream ideas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_k": {
                    "type": "integer",
                    "description": "How many core concepts to surface (default: 8)",
                    "default": 8,
                },
                "as_of": {
                    "type": "string",
                    "description": "ISO date for point-in-time bow-tie (default: current)",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "et_catalog",
        "description": "List available algorithms with families, descriptions, and research citations. Shows built-in and third-party plugins.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "family": {
                    "type": "string",
                    "description": "Filter by family (decay, activation, resolution, bridges, bow_tie, recombination). Empty = all.",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "et_shift",
        "description": (
            "Temporal diff between two dates (ADR 013 C5). Returns "
            "nodes_added / nodes_expired / edges_added / edges_expired. "
            "Filter by node_types, edge_types, namespace to scope to your slice."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_date": {
                    "type": "string",
                    "description": "ISO date (e.g. '2026-03-01'). Everything before this is baseline.",
                },
                "to_date": {
                    "type": "string",
                    "description": "ISO date (e.g. '2026-04-11'). Defaults to now.",
                    "default": "",
                },
                "node_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict to these node types (e.g. ['Concept','Hypothesis']). Default: all registered.",
                    "default": [],
                },
                "edge_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict to these edge types. Default: all registered.",
                    "default": [],
                },
                "namespace": {
                    "type": "string",
                    "description": "Scope to this namespace. Omit to span all.",
                    "default": "",
                },
                "property_match": {
                    "type": "object",
                    "description": "Equality filter on node columns (e.g. {'status':'open'}).",
                    "default": {},
                },
            },
            "required": ["from_date"],
        },
    },
    # ── Write-side tools (ADR 013 C4) ────────────────────────────────
    {
        "name": "et_add_node",
        "description": (
            "Write a typed node to the graph (ADR 013 C4). "
            "`type` is a registered class name (Concept, Hypothesis, Variant, etc.). "
            "`properties` is the Pydantic payload for that class. "
            "`namespace` scopes the write (default: 'default' for programmatic consumers)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Registered node class name (Concept / Hypothesis / Variant / ...)",
                },
                "properties": {
                    "type": "object",
                    "description": "Field values for the class. Required fields must be present.",
                },
                "namespace": {
                    "type": "string",
                    "description": "Namespace for this write (default: 'default').",
                    "default": "default",
                },
                "source": {
                    "type": "string",
                    "description": "Consumer identifier (et_source provenance).",
                    "default": "",
                },
            },
            "required": ["type", "properties"],
        },
    },
    {
        "name": "et_add_edge",
        "description": (
            "Write a typed edge between two existing nodes (ADR 013 C4). "
            "`properties` must include `source_id` and `target_id`. "
            "Kuzu's binder enforces the edge's declared FROM/TO against the ontology — "
            "wrong pairs are rejected at commit time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Registered edge class name (RelatesTo / InformedBy / HasProvenance / ...)",
                },
                "properties": {
                    "type": "object",
                    "description": "Must include source_id and target_id; other fields per the edge class.",
                },
                "namespace": {"type": "string", "default": "default"},
                "source": {"type": "string", "default": ""},
            },
            "required": ["type", "properties"],
        },
    },
    # ── Enrichment tools (ADR 011 v2) ────────────────────────────────
    {
        "name": "et_extend",
        "description": (
            "List external-knowledge nodes enriching a concept (ADR 011 v2). "
            "Answers 'what does the world say about this concept?'. "
            "Filter by source or theme when a concept has a lot attached."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_id": {
                    "type": "string",
                    "description": "User concept to inspect.",
                },
                "source": {
                    "type": "string",
                    "description": "Filter to one source ('wikipedia', 'arxiv', ...). Empty = all.",
                    "default": "",
                },
                "theme": {
                    "type": "string",
                    "description": "Filter to one theme tag (matched against the KnowledgeNode theme array). Empty = all.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max KnowledgeNodes to return.",
                    "default": 10,
                },
            },
            "required": ["concept_id"],
        },
    },
    {
        "name": "et_extend_force",
        "description": (
            "Trigger enrichment on a single concept right now, bypassing the "
            "configured triggers (ADR 011 v2). Useful for first-time ingestion "
            "and 'I need Wikipedia on this concept immediately' flows. "
            "Requires [enrichment] enabled = true in config."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_id": {
                    "type": "string",
                    "description": "Concept to enrich.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Source kinds to invoke (e.g. ['wikipedia']). Empty = all active sources.",
                    "default": [],
                },
            },
            "required": ["concept_id"],
        },
    },
    {
        "name": "et_extend_purge",
        "description": (
            "Bitemporally supersede every KnowledgeNode and Enriches edge "
            "from a source (ADR 011 v2). Sets t_expired on the affected rows — "
            "default queries stop seeing them; `as_of` queries still do. "
            "For genuine disk reclamation see et_extend_compact (future)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_kind": {
                    "type": "string",
                    "description": "Source to purge ('wikipedia', 'arxiv', ...).",
                },
            },
            "required": ["source_kind"],
        },
    },
    {
        "name": "et_run_algorithm",
        "description": (
            "Invoke a registered algorithm plugin (ADR 013 C7). When "
            "write_back=true, proposals are persisted as ProposalBy edges "
            "carrying algorithm name, parameters, score, and invocation "
            "timestamp — so 'at time T, spreading activation proposed X->Y "
            "with score S' is queryable forever."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "algorithm": {
                    "type": "string",
                    "description": "Plugin name (e.g. 'embedding_similarity', 'weighted_bfs', 'in_out_degree'). Run et_catalog to list.",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the plugin run (merged over its AlgorithmMeta defaults).",
                    "default": {},
                },
                "seed_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Seed concept ids (only used by activation-family plugins).",
                    "default": [],
                },
                "namespace": {
                    "type": "string",
                    "description": "Scope the algorithm run to this namespace.",
                    "default": "",
                },
                "write_back": {
                    "type": "boolean",
                    "description": "Persist proposals as ProposalBy edges. Default false.",
                    "default": False,
                },
            },
            "required": ["algorithm"],
        },
    },
    {
        "name": "et_find_similar",
        "description": (
            "Vector similarity over arbitrary typed nodes (ADR 013 C6). "
            "Answers 'have we seen something close to this before?' for any "
            "registered node type (Concept, Hypothesis, Variant, Wisdom, etc.). "
            "Filters by node_type and namespace so the research slice doesn't "
            "drown in memory noise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to compare against. Embedded on the fly.",
                },
                "node_type": {
                    "type": "string",
                    "description": "Registered node type to search within (Concept, Hypothesis, Variant, Wisdom, ...).",
                },
                "threshold": {
                    "type": "number",
                    "description": "Minimum similarity score (0..1). Defaults to 0.5.",
                    "default": 0.5,
                },
                "k": {
                    "type": "integer",
                    "description": "Max results. Defaults to 10.",
                    "default": 10,
                },
                "namespace": {
                    "type": "string",
                    "description": "Scope to this namespace. Omit to span all.",
                    "default": "",
                },
                "require_indexed": {
                    "type": "boolean",
                    "description": "Only return rows with vectors_pending=false. Default true.",
                    "default": True,
                },
            },
            "required": ["query", "node_type"],
        },
    },
    {
        "name": "et_write_rationale",
        "description": (
            "Attach a grounded rationale to a subject node (ADR 013 C4 / R8). "
            "Every id in `cited_node_ids` must resolve to an existing node in the "
            "given namespace before commit — the grounded-rationale guarantee. "
            "An LLM-written rationale with dangling citations is rejected."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_node_id": {
                    "type": "string",
                    "description": "Node this rationale justifies (stored as Signal.bearer_id).",
                },
                "text": {
                    "type": "string",
                    "description": "The rationale text, verbatim as produced by the LLM.",
                },
                "cited_node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Node ids cited as evidence. Every one must exist.",
                    "default": [],
                },
                "namespace": {"type": "string", "default": "default"},
                "source": {"type": "string", "default": ""},
                "title": {
                    "type": "string",
                    "description": "Short title for the rationale (defaults to a snippet of the text).",
                    "default": "",
                },
            },
            "required": ["subject_node_id", "text"],
        },
    },
]


async def handle_tool_call(name: str, arguments: dict) -> str:
    """Handle an MCP tool call."""
    pipeline = _get_pipeline()

    if name == "et_insight":
        force = arguments.get("force", False)

        # Skip sync if user explicitly forcing wisdom from existing graph
        # (sync can take 30+ seconds with batched extraction on 300+ chunks)
        if not arguments.get("skip_sync", False):
            await pipeline.sync()

        # Get or generate insight
        insight = await pipeline.get_insight()

        if insight["type"] == "nothing_new" and force:
            wisdom = await pipeline.generate_wisdom(force=True)
            if wisdom:
                insight = {
                    "type": "wisdom",
                    "insight": {"title": wisdom["title"], "description": f"**Why:** {wisdom['why']}\n\n**Action:** {wisdom['action']}"},
                }

        # Get concepts for evidence trail
        concepts = pipeline.store.list_concepts(order_by="frequency", limit=50)

        if insight["type"] in ("wisdom", "reflection"):
            wisdoms = pipeline.store.list_wisdoms(limit=1)
            if wisdoms:
                mind_map = _render_insight(wisdoms[0], concepts)
                prompt = PROMPTS[_call_count % len(PROMPTS)]
                return json.dumps({
                    "_render": "wisdom_card",
                    "mind_map": mind_map,
                    "prompt": prompt,
                }, indent=2)

        ins = insight.get("insight", {})
        return json.dumps({
            "_render": "wisdom_card",
            "mind_map": f"  {ins.get('title', 'No insight available')}\n\n  {ins.get('description', '')}",
            "prompt": "Run et_sync to pull more data, then try again. 🔍",
        }, indent=2)

    elif name == "et_explore":
        concept_name = arguments.get("concept_name", "")

        unified = _get_unified_graph(pipeline)
        all_nodes = unified.all_nodes()

        # Fuzzy match across both stores
        target = None
        for n in all_nodes:
            if n.label.lower() == concept_name.lower() or concept_name.lower() in n.label.lower():
                target = n
                break

        if not target:
            available = ", ".join(n.label for n in all_nodes[:10])
            return json.dumps({"error": f"'{concept_name}' not found. Available: {available}"})

        # Record access (ET concepts only)
        if target.source_system == "et":
            pipeline.store.record_access(target.id.removeprefix("et:"))

        # Get neighborhood from unified graph
        hood = unified.get_neighborhood(target.id)
        neighbor_names = []
        if hood:
            for neighbor in hood["neighbors"]:
                src = "ET" if neighbor.source_system == "et" else "MP"
                neighbor_names.append(f"{neighbor.label} [{src}]")

        # Spreading activation for related concepts (ET only) — via plugin
        related_by_activation = []
        if target.source_system == "et":
            from extended_thinking.algorithms import AlgorithmContext, get_by_name
            raw_id = target.id.removeprefix("et:")
            act_alg = get_by_name("weighted_bfs")
            if act_alg is not None:
                ctx = AlgorithmContext(
                    kg=pipeline.store,
                    vectors=pipeline.vectors,
                    params={"seed_ids": [raw_id]},
                )
                activated = act_alg.run(ctx)
                for cid, score in activated[:5]:
                    c = pipeline.store.get_concept(cid)
                    if c:
                        related_by_activation.append(f"{c['name']} ({score:.2f})")

        # Source quote (ET concepts only)
        source_quote = target.properties.get("source_quote", "")

        # Related wisdom (ET concepts only)
        related_wisdoms = []
        if target.source_system == "et":
            raw_id = target.id.removeprefix("et:")
            for w in pipeline.store.list_wisdoms(limit=10):
                if raw_id in w.get("related_concept_ids", []):
                    related_wisdoms.append(w["title"])

        # Provenance (ET concepts only)
        provenance = []
        if target.source_system == "et":
            raw_id = target.id.removeprefix("et:")
            for p in pipeline.store.get_provenance(raw_id):
                provenance.append(f"{p['source_provider']} ({p['llm_model'] or '?'})")

        return json.dumps({
            "_render": "concept_detail",
            "name": target.label,
            "category": target.category,
            "node_type": target.node_type,
            "source_system": target.source_system,
            "description": target.properties.get("description", ""),
            "source_quote": source_quote,
            "frequency": target.properties.get("frequency", 1),
            "connected_to": neighbor_names,
            "related_by_activation": related_by_activation,
            "related_wisdoms": related_wisdoms,
            "provenance": provenance,
        }, indent=2)

    elif name == "et_graph":
        unified = _get_unified_graph(pipeline)
        overview = unified.get_overview()

        lines = []
        lines.append(f"🔗 UNIFIED GRAPH: {overview['total_nodes']} nodes, {overview['total_edges']} edges")
        lines.append("")

        # Sparse active set (top concepts by activity)
        active = pipeline.store.active_nodes(k=8)
        if active:
            lines.append("  🧠 Active (most engaged):")
            for c in active:
                m = MARKERS.get(c["category"], ".")
                lines.append(f"    {m} {c['name']} (freq={c['frequency']}, accessed={c.get('access_count', 0)})")
            lines.append("")

        # Clusters
        for i, cluster in enumerate(overview["clusters"]):
            if cluster["size"] > 1:
                lines.append(f"  Cluster {i+1} ({cluster['size']} nodes):")
                for node in cluster["nodes"]:
                    m = MARKERS.get(node.category, ".")
                    src = "ET" if node.source_system == "et" else "MP"
                    lines.append(f"    {m} {node.label} [{src}]")
                lines.append("")

        # Bridges
        if overview["bridges"]:
            lines.append("  ⚡ Bridges (high connectivity):")
            for b in overview["bridges"]:
                m = MARKERS.get(b.category, ".")
                src = "ET" if b.source_system == "et" else "MP"
                lines.append(f"    {m} {b.label} [{src}]")
            lines.append("")

        # Isolated
        if overview["isolated"]:
            lines.append("  Isolated (no connections):")
            for iso in overview["isolated"]:
                m = MARKERS.get(iso.category, ".")
                src = "ET" if iso.source_system == "et" else "MP"
                lines.append(f"    {m} {iso.label} [{src}]")
            lines.append("")

        lines.append('Use et_explore "<name>" to zoom into any concept, or et_path to find connections.')
        return "\n".join(lines)

    elif name == "et_path":
        from_name = arguments.get("from_concept", "")
        to_name = arguments.get("to_concept", "")

        unified = _get_unified_graph(pipeline)
        all_nodes = unified.all_nodes()

        # Fuzzy match names to unified node IDs
        def find_node(name):
            name_lower = name.lower()
            for n in all_nodes:
                if n.label.lower() == name_lower or name_lower in n.label.lower():
                    return n
            return None

        from_node = find_node(from_name)
        to_node = find_node(to_name)

        if not from_node:
            available = ", ".join(n.label for n in all_nodes[:10])
            return f"'{from_name}' not found. Available: {available}"
        if not to_node:
            available = ", ".join(n.label for n in all_nodes[:10])
            return f"'{to_name}' not found. Available: {available}"

        path = unified.find_path(from_node.id, to_node.id)

        if path is None:
            return f"No path between '{from_node.label}' and '{to_node.label}'. They live in separate clusters."

        # Record access on all traversed nodes and edges
        for i, node in enumerate(path):
            if node.source_system == "et":
                raw_id = node.id.removeprefix("et:")
                pipeline.store.record_access(raw_id)
                if i > 0 and path[i - 1].source_system == "et":
                    prev_raw = path[i - 1].id.removeprefix("et:")
                    pipeline.store.record_edge_access(prev_raw, raw_id)

        # Render path
        lines = [f"🔗 PATH: {from_node.label} --> {to_node.label} ({len(path)} steps)", ""]
        for i, node in enumerate(path):
            m = MARKERS.get(node.category, ".")
            src = "ET" if node.source_system == "et" else "MP"
            connector = "  --> " if i > 0 else "      "
            lines.append(f"{connector}{m} {node.label} [{src}]")

        return "\n".join(lines)

    elif name == "et_concepts":
        limit = arguments.get("limit", 20)
        concepts = pipeline.store.list_concepts(order_by="frequency", limit=limit)
        return _render_concepts(concepts)

    elif name == "et_sync":
        limit = arguments.get("limit", 100)
        result = await pipeline.sync(limit=limit)
        processed = result["chunks_processed"]
        extracted = result["concepts_extracted"]
        filtered = result.get("filtered_code", 0)
        superseded = result.get("superseded", 0)
        total = pipeline.store.get_stats()["total_concepts"]
        vec_count = pipeline.vectors.count() if pipeline.vectors else 0
        parts = [f"🔍 Synced: {processed} chunks processed"]
        if filtered:
            parts.append(f"({filtered} code filtered)")
        if superseded:
            parts.append(f"⏳ {superseded} edges superseded")
        parts.append(f"💎 +{extracted} concepts extracted. Total: {total} concepts, {vec_count} indexed.")
        return " ".join(parts)

    elif name == "et_stats":
        from extended_thinking.processing.pipeline_v2 import _is_thinking_content
        stats = pipeline.get_stats()
        p = stats["provider"]
        c = stats["concepts"]

        # Content filter diagnostic
        sample_chunks = pipeline.provider.get_recent(limit=500)
        thinking = sum(1 for ch in sample_chunks if _is_thinking_content(ch))
        code = len(sample_chunks) - thinking

        # Unified graph counts
        unified = _get_unified_graph(pipeline)
        nodes = unified.all_nodes()
        edges = unified.all_edges()
        et_count = sum(1 for n in nodes if n.source_system == "et")
        mp_count = sum(1 for n in nodes if n.source_system == "mempalace")
        bridge_count = sum(1 for e in edges if e.edge_type == "SAME_AS")

        # Vector store
        vec_count = pipeline.vectors.count() if pipeline.vectors else 0

        # Active models (configurable via ET_EXTRACTION_MODEL, ET_WISDOM_MODEL)
        from extended_thinking.config import settings

        lines = [
            f"🧠 Provider: {p.get('detected_provider', p.get('provider', '?'))}",
            f"   Memories: {p.get('total_memories', 0)} (sample: {thinking} thinking, {code} code filtered)",
            f"💎 Concepts: {c['total_concepts']}",
            f"🔗 Relationships: {c['total_relationships']}",
            f"   Wisdoms: {c['total_wisdoms']}",
            f"   VectorStore: {vec_count} chunks indexed",
            f"   Last sync: {stats.get('last_sync', 'never')}",
            "",
            f"🤖 Models:",
            f"   Extraction: {settings.extraction_model}",
            f"   Wisdom:     {settings.wisdom_model}",
        ]
        if len(nodes) > 0:
            lines.append(f"🔗 Unified graph: {len(nodes)} nodes ({et_count} ET + {mp_count} MP), {len(edges)} edges, {bridge_count} bridges")

        return "\n".join(lines)

    elif name == "et_recall":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 5)

        if pipeline.vectors is None:
            return "VectorStore not configured. Run with ChromaDB to enable semantic recall."

        results = pipeline.vectors.search(query, limit=limit)
        if not results:
            return f"No matches for '{query}'. Run et_sync to index more conversations."

        lines = [f"🧠 Recall: {len(results)} matches for '{query}'", ""]
        for r in results:
            ts = r.metadata.get("timestamp", "")
            source = r.metadata.get("source", "")
            snippet = r.content[:300].replace("\n", " ")
            lines.append(f"  [{ts[:10] if ts else '?'}] {snippet}...")
            if source:
                lines.append(f"    src: {source[-50:]}")
            lines.append("")

        return "\n".join(lines)

    elif name == "et_suggest":
        from extended_thinking.algorithms import AlgorithmContext, get_by_name
        algorithm = arguments.get("algorithm") or "textual_similarity"
        top_k = arguments.get("top_k", 10)
        threshold = arguments.get("threshold", 0.5)
        as_of = arguments.get("as_of", "") or None

        alg = get_by_name(algorithm)
        if alg is None or alg.meta.family != "link_prediction":
            return (f"Unknown link_prediction algorithm: '{algorithm}'. "
                    f"Use et_catalog family=link_prediction to see options.")
        alg.top_k = top_k
        alg.threshold = threshold

        ctx = AlgorithmContext(
            kg=pipeline.store,
            vectors=pipeline.vectors,
            as_of=as_of,
        )
        results = alg.run(ctx)

        if not results:
            return f"🧩 No link candidates above threshold {threshold} [{algorithm}]. Lower threshold, try embedding_similarity, or sync more content."

        lines = [f"🧩 Link suggestions [{algorithm}]: {len(results)} candidates (threshold={threshold})", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"  {i}. {r['from']['name']} ⟷ {r['to']['name']}  (similarity={r['similarity']:.2f})")
            lines.append(f"     A: {r['signature_a']}")
            lines.append(f"     B: {r['signature_b']}")
            lines.append("")
        lines.append('Feed these to et_recombine for LLM-grounded verdicts, or explore via et_explore.')
        return "\n".join(lines)

    elif name == "et_recombine":
        from extended_thinking.algorithms import AlgorithmContext, get_by_name
        from extended_thinking.config import settings
        from extended_thinking.ai.registry import get_provider as get_ai_provider

        candidates = arguments.get("candidates", 3)
        as_of = arguments.get("as_of", "") or None

        alg = get_by_name("cross_cluster_grounded")
        if alg is None:
            return "Recombination algorithm not registered."
        alg.candidates_per_run = candidates

        # Build LLM caller wrapper (uses configured wisdom model for grounded reasoning)
        wisdom_provider = settings.wisdom_provider or None
        wisdom_model = settings.wisdom_model
        try:
            ai = get_ai_provider(wisdom_provider)
        except RuntimeError:
            return "No AI provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."

        async def _llm_call(prompt: str) -> str:
            return await ai.complete(
                messages=[{"role": "user", "content": prompt}],
                model=wisdom_model,
            )

        # The algorithm expects a sync caller; wrap async in a sync entry point
        import asyncio
        def llm_caller(prompt: str) -> str:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_llm_call(prompt))
            finally:
                loop.close()

        ctx = AlgorithmContext(
            kg=pipeline.store,
            vectors=pipeline.vectors,
            as_of=as_of,
            params={"llm_caller": llm_caller},
        )
        results = alg.run(ctx)

        if not results:
            return "🧘 DMN recombination: need >=2 disconnected clusters to recombine across. Run et_sync first."

        lines = [f"🧘 DMN recombination: {len(results)} candidates (model: {wisdom_model})", ""]
        verdict_icon = {"grounded": "✓", "speculative": "?", "no_connection": "✗"}
        for i, r in enumerate(results, 1):
            icon = verdict_icon.get(r["verdict"], "·")
            conf = r.get("confidence", 0.0)
            lines.append(f"  {i}. {icon} {r['from']['name']} ⟷ {r['to']['name']} "
                         f"({r['verdict']}, conf={conf:.2f})")
            if r.get("bridge"):
                lines.append(f"     bridge: {r['bridge']}")
            if r.get("mechanism"):
                lines.append(f"     mechanism: {r['mechanism']}")
            if r.get("requires"):
                lines.append(f"     requires: {r['requires']}")
            lines.append("")

        lines.append('Grounded = real bridge. Speculative = would require X. No connection = distance is principled.')
        return "\n".join(lines)

    elif name == "et_core":
        from extended_thinking.algorithms import AlgorithmContext, get_by_name
        top_k = arguments.get("top_k", 8)
        as_of = arguments.get("as_of", "") or None

        alg = get_by_name("in_out_degree")
        if alg is None:
            return "Bow-tie algorithm not registered."
        # Override top_k via params
        alg.top_k = top_k
        ctx = AlgorithmContext(kg=pipeline.store, as_of=as_of)
        results = alg.run(ctx)

        if not results:
            return "No bow-tie core yet. Need concepts with ≥2 source chunks AND ≥2 outgoing edges."

        lines = [f"🎯 Bow-tie core ({len(results)} concepts):", ""]
        for i, r in enumerate(results, 1):
            c = r["concept"]
            m = MARKERS.get(c.get("category", ""), ".")
            lines.append(
                f"  {i}. {m} {c['name']} (score={r['bow_tie_score']}, "
                f"in={r['in_degree']} / out={r['out_degree']})"
            )
            lines.append(f"     {r['justification']}")
            lines.append("")
        lines.append('Use et_explore "<name>" to drill into any core concept.')
        return "\n".join(lines)

    elif name == "et_catalog":
        from extended_thinking.algorithms import list_available
        family = arguments.get("family", "") or None
        metas = list_available(family=family)

        if not metas:
            return "No algorithms registered."

        lines = [f"📚 Algorithm catalog ({len(metas)} registered):", ""]
        current_family = ""
        for m in metas:
            if m.family != current_family:
                lines.append(f"── {m.family.upper()} ──")
                current_family = m.family
            lines.append(f"  {m.name}  {'[temporal]' if m.temporal_aware else ''}")
            lines.append(f"    {m.description}")
            lines.append(f"    ref: {m.paper_citation}")
            if m.parameters:
                params_str = ", ".join(f"{k}={v}" for k, v in m.parameters.items())
                lines.append(f"    params: {params_str}")
            lines.append("")
        return "\n".join(lines)

    elif name == "et_shift":
        from datetime import datetime, timezone
        from_date = arguments.get("from_date", "")
        to_date = arguments.get("to_date", "") or datetime.now(timezone.utc).isoformat()

        if not from_date:
            return "et_shift requires from_date (ISO format, e.g. '2026-03-01')"

        if not hasattr(pipeline.store, "diff"):
            return "Temporal diff not supported on this storage backend."

        # ADR 013 C5 filters
        node_types = arguments.get("node_types") or None
        edge_types = arguments.get("edge_types") or None
        namespace = arguments.get("namespace") or None
        property_match = arguments.get("property_match") or None

        try:
            changes = pipeline.store.diff(
                from_date, to_date,
                node_types=node_types,
                edge_types=edge_types,
                property_match=property_match,
                namespace=namespace,
            )
        except ValueError as e:
            return f"error: {e}"

        header = f"⏳ Thinking shift: {from_date[:10]} → {to_date[:10]}"
        if namespace or node_types or edge_types or property_match:
            bits = []
            if namespace:
                bits.append(f"namespace={namespace}")
            if node_types:
                bits.append(f"node_types={node_types}")
            if edge_types:
                bits.append(f"edge_types={edge_types}")
            if property_match:
                bits.append(f"match={property_match}")
            header += f"  ({'; '.join(bits)})"
        lines = [header, ""]

        nodes_added = changes["nodes_added"]
        nodes_expired = changes["nodes_expired"]
        edges_added = changes["edges_added"]
        edges_expired = changes["edges_expired"]

        if nodes_added:
            lines.append(f"💎 Nodes added ({len(nodes_added)}):")
            # Group by type for readability
            by_type: dict[str, list[dict]] = {}
            for n in nodes_added:
                by_type.setdefault(n["_type"], []).append(n)
            for t, group in by_type.items():
                lines.append(f"  {t}: {len(group)}")
                for n in group[:10]:
                    label = n.get("name") or n.get("title") or n.get("id", "?")
                    extras = []
                    if n.get("category"):
                        extras.append(n["category"])
                    tail = f" ({', '.join(extras)})" if extras else ""
                    lines.append(f"    - {label}{tail}")
                if len(group) > 10:
                    lines.append(f"    ... and {len(group) - 10} more")
            lines.append("")

        if nodes_expired:
            lines.append(f"🪦 Nodes expired ({len(nodes_expired)}):")
            for n in nodes_expired[:10]:
                label = n.get("name") or n.get("title") or n.get("id", "?")
                lines.append(f"    ◌ {n['_type']}: {label}")
            lines.append("")

        if edges_added:
            lines.append(f"🔗 Edges added ({len(edges_added)}):")
            by_edge_type: dict[str, int] = {}
            for e in edges_added:
                by_edge_type[e["_type"]] = by_edge_type.get(e["_type"], 0) + 1
            for t, n in by_edge_type.items():
                lines.append(f"    {t}: {n}")
            lines.append("")

        if edges_expired:
            lines.append(f"⚡ Edges expired/superseded ({len(edges_expired)}):")
            for e in edges_expired[:10]:
                reason = f" (superseded by {e['superseded_by']})" if e.get("superseded_by") else ""
                lines.append(f"    {e['source_id']} --{e['_type']}--> {e['target_id']}{reason}")
            lines.append("")

        if not (nodes_added or nodes_expired or edges_added or edges_expired):
            lines.append("(No changes in this window.)")

        return "\n".join(lines)

    # ── Write-side tools (ADR 013 C4) ────────────────────────────────

    elif name == "et_add_node":
        return _handle_et_add_node(pipeline, arguments)

    elif name == "et_add_edge":
        return _handle_et_add_edge(pipeline, arguments)

    elif name == "et_write_rationale":
        return _handle_et_write_rationale(pipeline, arguments)

    elif name == "et_find_similar":
        return _handle_et_find_similar(pipeline, arguments)

    elif name == "et_run_algorithm":
        return _handle_et_run_algorithm(pipeline, arguments)

    elif name == "et_extend":
        return _handle_et_extend(pipeline, arguments)

    elif name == "et_extend_force":
        return _handle_et_extend_force(pipeline, arguments)

    elif name == "et_extend_purge":
        return _handle_et_extend_purge(pipeline, arguments)

    return f"Unknown tool: {name}"


# ── Enrichment handlers (ADR 011 v2) ─────────────────────────────────

def _handle_et_extend(pipeline, arguments: dict) -> str:
    """List KnowledgeNodes attached to a concept, optionally filtered."""
    concept_id = arguments.get("concept_id", "")
    source_filter = arguments.get("source") or ""
    theme_filter = arguments.get("theme") or ""
    limit = int(arguments.get("limit", 10))

    if not concept_id:
        return "error: concept_id is required"

    kg = pipeline.store
    if kg.get_concept(concept_id) is None:
        return f"error: concept {concept_id!r} not found"

    cypher = (
        "MATCH (c:Concept {id: $cid})-[r:Enriches]->(k:KnowledgeNode) "
        "WHERE (k.t_expired IS NULL OR k.t_expired = '') "
    )
    params: dict = {"cid": concept_id, "limit": limit}
    if source_filter:
        cypher += "AND k.source_kind = $src "
        params["src"] = source_filter
    cypher += (
        "RETURN k.id, k.source_kind, k.title, k.abstract, k.url, "
        "k.theme, k.namespace, r.relevance, r.trigger "
        "ORDER BY r.relevance DESC LIMIT $limit"
    )
    rows = kg._query_all(cypher, params)

    import json as _json
    results = []
    for r in rows:
        themes = []
        if r[5]:
            try:
                themes = _json.loads(r[5])
            except (ValueError, TypeError):
                themes = []
        if theme_filter and theme_filter not in themes:
            continue
        results.append({
            "id": r[0],
            "source_kind": r[1],
            "title": r[2],
            "abstract": (r[3] or "")[:400],
            "url": r[4] or "",
            "themes": themes,
            "namespace": r[6],
            "relevance": round(r[7], 3) if r[7] is not None else None,
            "trigger": r[8] or "",
        })

    return _json.dumps({
        "concept_id": concept_id,
        "source_filter": source_filter or None,
        "theme_filter": theme_filter or None,
        "count": len(results),
        "knowledge_nodes": results,
    }, indent=2)


def _handle_et_extend_force(pipeline, arguments: dict) -> str:
    """On-demand enrichment for a single concept.

    Still requires [enrichment] enabled = true — the master toggle
    gates ALL external calls, not just the async trigger path.
    """
    from extended_thinking.algorithms import (
        build_config_from_settings,
        get_active,
    )
    from extended_thinking.algorithms.enrichment.runner import run_enrichment
    from extended_thinking.algorithms.protocol import AlgorithmMeta
    from extended_thinking.config import settings

    concept_id = arguments.get("concept_id", "")
    requested_sources = list(arguments.get("sources", []) or [])

    if not concept_id:
        return "error: concept_id is required"
    if not settings.enrichment.enabled:
        return (
            "error: enrichment is disabled ([enrichment] enabled = false). "
            "Enable it in config.toml before invoking et_extend_force."
        )

    kg = pipeline.store
    if kg.get_concept(concept_id) is None:
        return f"error: concept {concept_id!r} not found"

    algo_config = build_config_from_settings(settings.algorithms)
    sources = get_active("enrichment.sources", algo_config)
    if requested_sources:
        sources = [s for s in sources if s.source_kind() in requested_sources]
    gates = get_active("enrichment.relevance_gates", algo_config)
    cache_plugins = get_active("enrichment.cache", algo_config)
    cache = cache_plugins[0] if cache_plugins else None

    if not sources:
        return (
            "error: no active enrichment sources. "
            f"Registered + enabled sources are required; requested={requested_sources or 'any'}."
        )

    # A synthetic trigger that fires for exactly this one concept.
    class _OneShot:
        meta = AlgorithmMeta(
            name="et_extend_force",
            family="enrichment.triggers",
            description="on-demand synthetic trigger",
            paper_citation="n/a",
        )
        def fired_concepts(self, ctx):
            return [(concept_id, "et_extend_force")]

    summary = run_enrichment(
        kg=kg,
        sources=sources,
        triggers=[_OneShot()],
        gates=gates,
        cache=cache,
        concept_namespace=settings.enrichment.concept_namespace,
    )

    import json as _json
    return _json.dumps({
        "concept_id": concept_id,
        "triggers_fired": summary.triggers_fired,
        "candidates_returned": summary.candidates_returned,
        "candidates_accepted": summary.candidates_accepted,
        "knowledge_nodes_created": summary.knowledge_nodes_created,
        "edges_created": summary.edges_created,
        "runs_recorded": summary.runs_recorded,
        "errors": summary.errors[:5],
    }, indent=2)


def _handle_et_extend_purge(pipeline, arguments: dict) -> str:
    """Supersede every enrichment row from one source. Bitemporal;
    rows stay queryable via `as_of`."""
    from datetime import datetime, timezone

    source_kind = arguments.get("source_kind", "")
    if not source_kind:
        return "error: source_kind is required"

    kg = pipeline.store
    ns = f"enrichment:{source_kind}"
    now = datetime.now(timezone.utc).isoformat()

    # Count first so the response can report what we affected
    kn_count_row = kg._query_one(
        "MATCH (k:KnowledgeNode) WHERE k.namespace = $ns "
        "AND (k.t_expired IS NULL OR k.t_expired = '') RETURN count(k)",
        {"ns": ns},
    )
    kn_count = kn_count_row[0] if kn_count_row else 0

    # Mark KnowledgeNodes expired
    kg._conn.execute(
        "MATCH (k:KnowledgeNode) WHERE k.namespace = $ns "
        "AND (k.t_expired IS NULL OR k.t_expired = '') "
        "SET k.t_expired = $now, k.t_valid_to = $now",
        parameters={"ns": ns, "now": now},
    )
    # Mark Enriches edges expired too
    kg._conn.execute(
        "MATCH ()-[r:Enriches]->() WHERE r.namespace = $ns "
        "AND (r.t_expired IS NULL OR r.t_expired = '') "
        "SET r.t_expired = $now, r.t_valid_to = $now",
        parameters={"ns": ns, "now": now},
    )

    import json as _json
    return _json.dumps({
        "source_kind": source_kind,
        "namespace": ns,
        "knowledge_nodes_superseded": kn_count,
        "as_of": now,
        "note": "rows remain queryable via as_of; use et_extend_compact (future) to reclaim disk.",
    }, indent=2)


def _handle_et_run_algorithm(pipeline, arguments: dict) -> str:
    """ADR 013 C7 — invoke a plugin, optionally persist proposals."""
    from datetime import datetime, timezone

    from extended_thinking.algorithms import (
        AlgorithmContext,
        build_config_from_settings,
        get_by_name,
    )
    from extended_thinking.config import settings

    algo_name = arguments.get("algorithm", "")
    params = dict(arguments.get("params", {}) or {})
    seed_ids = list(arguments.get("seed_ids", []) or [])
    namespace = arguments.get("namespace") or None
    write_back = bool(arguments.get("write_back", False))

    if not algo_name:
        return "error: algorithm name is required"

    cfg = build_config_from_settings(settings.algorithms)
    # Runtime params from the caller override configured defaults for this
    # invocation — lets an MCP caller tune threshold/top_k without editing
    # config. Registry instantiates per-call, so no cross-request leakage.
    if params:
        cfg = {**cfg, "parameters": {
            **(cfg.get("parameters") or {}),
            algo_name: {**(cfg.get("parameters", {}).get(algo_name, {})), **params},
        }}
    alg = get_by_name(algo_name, cfg)
    if alg is None:
        return f"error: unknown algorithm {algo_name!r}. Run et_catalog to list."

    kg = pipeline.store
    if seed_ids:
        params.setdefault("seed_ids", seed_ids)

    ctx = AlgorithmContext(
        kg=kg,
        vectors=getattr(pipeline, "vectors", None),
        namespace=namespace,
        params=params,
        now=datetime.now(timezone.utc),
    )

    try:
        result = alg.run(ctx)
    except Exception as e:
        logger.exception("algorithm %s failed", algo_name)
        return f"error: algorithm {algo_name!r} raised: {e}"

    summary: dict = {
        "algorithm": algo_name,
        "params": params,
        "namespace": namespace,
        "write_back": write_back,
        "result_shape": _describe_result(result),
    }

    if write_back:
        edges = _persist_proposals(kg, algo_name, result, params,
                                   namespace=namespace or "default")
        summary["proposals_written"] = len(edges)
        summary["proposal_edge_ids"] = edges[:10]
        if len(edges) > 10:
            summary["proposal_edge_ids_note"] = (
                f"(first 10 of {len(edges)} shown; all persisted)"
            )

    return json.dumps(summary, indent=2, default=str)


def _extract_id(row: dict, keys: tuple[str, ...]) -> str | None:
    """Pull a node id out of a plugin result row, handling both flat
    string shape (`row["source_id"]`) and the link-prediction nested
    shape (`row["from"]["id"]`)."""
    for k in keys:
        v = row.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict) and v.get("id"):
            return v["id"]
    return None


def _describe_result(result) -> str:
    """One-line shape description so the tool output is legible."""
    if result is None:
        return "None"
    if isinstance(result, list):
        if not result:
            return "empty list"
        first = result[0]
        if isinstance(first, tuple):
            return f"list[{len(result)}] of tuples (e.g. {first!r})"
        if isinstance(first, dict):
            keys = sorted(first.keys())[:4]
            return f"list[{len(result)}] of dicts with keys {keys}"
        return f"list[{len(result)}] of {type(first).__name__}"
    if isinstance(result, dict):
        return f"dict with keys {sorted(result.keys())[:6]}"
    return f"{type(result).__name__}"


def _persist_proposals(kg, algo_name: str, result, params: dict,
                       *, namespace: str) -> list[str]:
    """Turn an algorithm's result into ProposalBy edges (ADR 013 C7).

    Handles the two natural shapes built-in plugins emit:
      - activation-family: list[(node_id, score)] — each proposal is
        anchored at the first seed in `params['seed_ids']`.
      - link-prediction-family: list[dict] with keys like
        'source_id', 'target_id', 'score' (or 'similarity').

    Any other shape is skipped with a log line — we don't fabricate
    connections we can't honestly interpret. Consumers can call
    `GraphStore.record_proposal` directly for custom result shapes.
    """
    edges: list[str] = []
    if not result:
        return edges

    if isinstance(result, list) and result and isinstance(result[0], tuple):
        # Activation-shape: (node_id, score). Anchor each proposal at the
        # first seed if we have one, otherwise skip (no src).
        seeds = params.get("seed_ids") or []
        if not seeds:
            logger.info(
                "et_run_algorithm(%s, write_back): activation result has no "
                "seed to anchor proposals; skipping persistence.",
                algo_name,
            )
            return edges
        src = seeds[0]
        for tgt, score in result:
            try:
                eid = kg.record_proposal(
                    algorithm=algo_name,
                    source_id=src, target_id=tgt,
                    score=float(score),
                    parameters=params,
                    namespace=namespace,
                )
                edges.append(eid)
            except ValueError as e:
                logger.debug("skip proposal %s->%s: %s", src, tgt, e)
        return edges

    if isinstance(result, list) and result and isinstance(result[0], dict):
        for r in result:
            src = _extract_id(r, ("source_id", "src", "from"))
            tgt = _extract_id(r, ("target_id", "tgt", "to"))
            score = (r.get("score") or r.get("similarity")
                     or r.get("weight") or r.get("bow_tie_score") or 0.0)
            if not src or not tgt:
                continue
            try:
                eid = kg.record_proposal(
                    algorithm=algo_name,
                    source_id=src, target_id=tgt,
                    score=float(score),
                    parameters=params,
                    namespace=namespace,
                )
                edges.append(eid)
            except ValueError as e:
                logger.debug("skip proposal %s->%s: %s", src, tgt, e)
        return edges

    logger.info(
        "et_run_algorithm(%s, write_back): result shape not persistable "
        "automatically; call GraphStore.record_proposal() directly.",
        algo_name,
    )
    return edges


def _handle_et_find_similar(pipeline, arguments: dict) -> str:
    """ADR 013 C6 — typed vector similarity over registered node types."""
    query = arguments.get("query", "")
    node_type = arguments.get("node_type", "")
    threshold = float(arguments.get("threshold", 0.5))
    k = int(arguments.get("k", 10))
    namespace = arguments.get("namespace") or None
    require_indexed = bool(arguments.get("require_indexed", True))

    if not query:
        return "error: query is required"
    if not node_type:
        return "error: node_type is required"

    kg = pipeline.store
    try:
        hits = kg.find_similar_typed(
            query,
            node_type=node_type,
            threshold=threshold,
            k=k,
            namespace=namespace,
            require_indexed=require_indexed,
        )
    except ValueError as e:
        return f"error: {e}"
    except AttributeError:
        return (
            "error: the active storage backend does not support "
            "find_similar_typed (no VectorStore attached)."
        )

    if not hits:
        return json.dumps({
            "query": query, "node_type": node_type,
            "namespace": namespace, "results": [],
            "note": "no matches above threshold — lower it, widen namespace, or check vectors_pending state",
        }, indent=2)

    return json.dumps({
        "query": query,
        "node_type": node_type,
        "namespace": namespace,
        "threshold": threshold,
        "results": [{"id": nid, "score": round(score, 4)} for nid, score in hits],
    }, indent=2)


# ── Write-tool handlers (ADR 013 C4) ─────────────────────────────────
#
# Shared with the HTTP layer (api/routes/graph_v2.py): both entry points
# dispatch to `GraphStore.insert` so the write semantics are one path
# from two surfaces.

def _resolve_registered_class(type_name: str, expected: str):
    """Return the registered pydantic class for a string type name, or
    raise ValueError with a helpful list of what's actually registered."""
    from extended_thinking._schema.kuzu_types import EDGE_TYPES, KUZU_TABLE, NODE_TYPES

    for cls in KUZU_TABLE:
        if cls.__name__ == type_name:
            if expected == "node" and cls not in NODE_TYPES:
                raise ValueError(
                    f"{type_name!r} is an edge type; use et_add_edge"
                )
            if expected == "edge" and cls not in EDGE_TYPES:
                raise ValueError(
                    f"{type_name!r} is a node type; use et_add_node"
                )
            return cls
    available = sorted(
        c.__name__ for c in KUZU_TABLE
        if (expected == "node" and c in NODE_TYPES)
        or (expected == "edge" and c in EDGE_TYPES)
    )
    raise ValueError(
        f"unknown {expected} type {type_name!r}. "
        f"Registered {expected} types: {available}"
    )


def _handle_et_add_node(pipeline, arguments: dict) -> str:
    from pydantic import ValidationError
    type_name = arguments.get("type", "")
    properties = arguments.get("properties", {}) or {}
    namespace = arguments.get("namespace", "default")
    source = arguments.get("source", "")

    try:
        cls = _resolve_registered_class(type_name, "node")
    except ValueError as e:
        return f"error: {e}"
    try:
        instance = cls(**properties)
    except ValidationError as e:
        return f"error: payload validation failed:\n{e}"
    except TypeError as e:
        return f"error: {e}"

    kg = pipeline.store
    try:
        nid = kg.insert(instance, namespace=namespace, source=source)
    except Exception as e:
        logger.exception("et_add_node failed")
        return f"error: storage failure: {e}"

    return json.dumps({
        "id": nid,
        "type": type_name,
        "namespace": namespace,
        "vectors_pending": True,
    }, indent=2)


def _handle_et_add_edge(pipeline, arguments: dict) -> str:
    from pydantic import ValidationError
    type_name = arguments.get("type", "")
    properties = arguments.get("properties", {}) or {}
    namespace = arguments.get("namespace", "default")
    source = arguments.get("source", "")

    try:
        cls = _resolve_registered_class(type_name, "edge")
    except ValueError as e:
        return f"error: {e}"
    try:
        instance = cls(**properties)
    except ValidationError as e:
        return f"error: payload validation failed:\n{e}"
    except TypeError as e:
        return f"error: {e}"

    kg = pipeline.store
    try:
        eid = kg.insert(instance, namespace=namespace, source=source)
    except ValueError as e:
        return f"error: {e}"
    except RuntimeError as e:
        if "Expected labels" in str(e):
            return (
                f"error: edge rejected by ontology constraint "
                f"(wrong FROM/TO for type {type_name!r}): {e}"
            )
        logger.exception("et_add_edge failed")
        return f"error: storage failure: {e}"
    except Exception as e:
        logger.exception("et_add_edge failed")
        return f"error: storage failure: {e}"

    return json.dumps({
        "id": eid,
        "type": type_name,
        "namespace": namespace,
    }, indent=2)


def _handle_et_write_rationale(pipeline, arguments: dict) -> str:
    """Grounded-rationale guarantee: verify every citation resolves in the
    same namespace before creating the Rationale node."""
    from extended_thinking._schema import models as _m

    subject_id = arguments.get("subject_node_id", "")
    text = arguments.get("text", "")
    cited = list(arguments.get("cited_node_ids", []) or [])
    namespace = arguments.get("namespace", "default")
    source = arguments.get("source", "")
    title = arguments.get("title") or (text[:60] + "…" if len(text) > 60 else text)

    if not subject_id:
        return "error: subject_node_id is required"
    if not text:
        return "error: text is required"

    kg = pipeline.store

    # Verify subject resolves.
    if kg._find_node_type(subject_id) is None:
        return (
            f"error: subject node {subject_id!r} not found. "
            f"A rationale cannot attach to a node that does not exist."
        )

    # Grounded-citation guard: every cited id must resolve. Collect all
    # failures before reporting so the caller fixes them in one pass.
    unresolved = [cid for cid in cited if kg._find_node_type(cid) is None]
    if unresolved:
        return (
            "error: grounded-rationale guarantee violated. "
            "The following cited_node_ids do not resolve to existing nodes:\n  "
            + "\n  ".join(unresolved)
            + "\nWrite the cited nodes first, or remove them from cited_node_ids."
        )

    import json as _json
    rationale = _m.Rationale(
        id=f"rationale-{uuid.uuid4().hex[:12]}",
        name=title,
        description=title,
        text=text,
        cited_node_ids=_json.dumps(cited),
        # Malleus Signal mandatory fields:
        signal_type="rationale",
        bearer_id=subject_id,
    )
    try:
        rid = kg.insert(rationale, namespace=namespace, source=source)
    except Exception as e:
        logger.exception("et_write_rationale failed")
        return f"error: storage failure: {e}"

    return _json.dumps({
        "id": rid,
        "subject_node_id": subject_id,
        "namespace": namespace,
        "cited_count": len(cited),
    }, indent=2)


def run_mcp_server():
    """Run the MCP server over stdio."""
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "extended-thinking", "version": "0.2.0"},
                },
            }

        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            }

        elif method == "tools/call":
            tool_name = request.get("params", {}).get("name", "")
            arguments = request.get("params", {}).get("arguments", {})

            try:
                loop = asyncio.new_event_loop()
                result_text = loop.run_until_complete(handle_tool_call(tool_name, arguments))
                loop.close()
            except Exception as e:
                result_text = f"Error: {e}"

            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                },
            }

        elif method == "notifications/initialized":
            continue  # No response needed

        else:
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run_mcp_server()
