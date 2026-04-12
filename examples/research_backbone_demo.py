#!/usr/bin/env python3
"""End-to-end ADR 013 research-backbone demo.

Walks through every shipped capability (C1-C8) with a small research-loop
scenario. No LLM calls, no network, runs in a few seconds against a fresh
tmp Kuzu database.

Audience: consumers evaluating whether to use ET as a typed bitemporal
state store. Read the output, compare to your loop's shape, decide.

Run:
    python examples/research_backbone_demo.py

What gets exercised:
    C1 typed writes       — GraphStore.insert with typed Pydantic instances
    C2 namespace isolation — memory vs research slices
    C3/C4 (via GraphStore.insert; HTTP and MCP forms shown in docs)
    C5 filtered bitemporal — diff(from, to, node_types=..., namespace=...)
    C6 vector similarity   — find_similar_typed (auto-indexed on insert)
    C7 algorithm write-back — record_proposal + textual_similarity plugin
    C8 non-extraction ingest — structural data skips the LLM pass

For the HTTP and MCP write paths, see docs/research-backbone.md.
"""

from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ── Pretty printing ─────────────────────────────────────────────────────

def step(n: int, title: str) -> None:
    print()
    print(f"━━━ Step {n}: {title} ━━━")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def info(msg: str) -> None:
    print(f"    {msg}")


def iso_minus(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── Demo ────────────────────────────────────────────────────────────────

def main() -> int:
    from extended_thinking.storage import StorageLayer
    from schema.generated import models as m

    print("ADR 013 research-backbone demo")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        # StorageLayer = Kuzu + ChromaDB + ontology wiring. One call.
        storage = StorageLayer.default(data)
        kg = storage.kg

        # ──────────────────────────────────────────────────────────
        step(1, "Typed writes (C1) — Concepts as stand-ins for Hypothesis/Variant")
        # In a real consumer, these would be Hypothesis and Variant classes
        # declared in that consumer's LinkML. ET ships with Concept as a
        # generic typed node we can reuse for the demo.
        seeds = [
            ("h-1", "sparse attention reduces inference latency",
             "top-k sparsity over attention scores; skip low scores"),
            ("h-2", "flash attention uses tiling for memory efficiency",
             "fuse Q/K/V matmuls; avoid materializing full attention matrix"),
            ("h-3", "linear attention approximates softmax",
             "kernel-based reformulation; linear in sequence length"),
            ("v-1", "block-sparse top-k with k=32, block=64",
             "implementation draft for h-1"),
            ("v-2", "flash-attn v2 kernel with warp-level fusion",
             "implementation for h-2"),
        ]
        for cid, name, desc in seeds:
            kg.insert(
                m.Concept(
                    id=cid,
                    name=name,
                    category=m.ConceptCategory.topic,
                    description=desc,
                ),
                namespace="research",
                source="demo",
            )
        ok(f"inserted {len(seeds)} typed nodes into namespace='research'")
        info("each write was synchronous; Kuzu committed before insert returned")

        # ──────────────────────────────────────────────────────────
        step(2, "Namespace isolation (C2) — memory-side writes stay separate")
        kg.add_concept("mem-1", "morning pages note", "topic", "unrelated memory data")
        all_concepts = kg.list_concepts(limit=50)
        research_only = kg.list_concepts(limit=50, namespace="research")
        memory_only = kg.list_concepts(limit=50, namespace="memory")
        ok(f"graph total: {len(all_concepts)} concepts")
        info(f"namespace='research' ->  {len(research_only)} concepts")
        info(f"namespace='memory'   ->  {len(memory_only)} concepts")
        assert len(research_only) == len(seeds)
        assert len(memory_only) == 1

        # ──────────────────────────────────────────────────────────
        step(3, "Typed vector similarity (C6) — have we seen something close?")
        # Every insert above ALSO indexed into ChromaDB automatically
        # because StorageLayer passed `vectors` into GraphStore.
        query = "sparsity for faster attention"
        hits = kg.find_similar_typed(
            query, "Concept",
            namespace="research", threshold=0.2, k=3,
        )
        ok(f"query: '{query}'")
        for nid, score in hits:
            c = kg.get_concept(nid, namespace="research")
            info(f"{score:.3f}  {nid}: {c['name']}")
        info("metadata filter by node_type + namespace; indexing is auto")

        # ──────────────────────────────────────────────────────────
        step(4, "Grounded rationale (C4) — citations must resolve")
        rationale = m.Rationale(
            id="rat-1",
            name="why h-1 is worth trying",
            description="substantiation for trying h-1 next",
            text=("h-1 plausible: v-1 is a concrete implementation, "
                  "and it relates to h-2 through the shared goal of "
                  "attention efficiency."),
            cited_node_ids=json.dumps(["v-1", "h-2"]),
            signal_type="rationale",
            bearer_id="h-1",
        )
        kg.insert(rationale, namespace="research", source="demo")
        ok("rationale bound to h-1 with 2 citations (v-1, h-2)")
        info("in MCP form, et_write_rationale would reject a rationale")
        info("citing a non-existent id — see docs/research-backbone.md step 4")

        # ──────────────────────────────────────────────────────────
        step(5, "Algorithm write-back (C7) — who said what, when")
        # Run textual similarity to find unlinked-but-related pairs,
        # persist proposals as ProposalBy edges.
        from extended_thinking.algorithms import (
            AlgorithmContext,
            get_by_name,
        )
        alg = get_by_name("textual_similarity")
        result = alg.run(AlgorithmContext(
            kg=kg,
            namespace="research",
            params={"threshold": 0.25, "top_k": 10},
        ))
        ok(f"textual_similarity returned {len(result)} candidate pairs")

        # Persist each as a ProposalBy edge
        proposals = 0
        invocation_time = datetime.now(timezone.utc).isoformat()
        for pair in result:
            src = pair["from"]["id"]
            tgt = pair["to"]["id"]
            score = pair["similarity"]
            try:
                kg.record_proposal(
                    algorithm="textual_similarity",
                    source_id=src, target_id=tgt,
                    score=score,
                    parameters={"threshold": 0.25, "top_k": 10},
                    namespace="research",
                    et_source="demo",
                )
                proposals += 1
            except ValueError:
                pass
        ok(f"{proposals} ProposalBy edges persisted")
        info("every proposal carries algorithm name + parameters + timestamp")
        info("audit: 'what did textual_similarity say at T?' is a query, not a re-run")

        # Verify provenance survives
        rows = kg._query_all(
            "MATCH (a:Concept)-[r:ProposalBy]->(b:Concept) "
            "WHERE r.namespace = 'research' "
            "RETURN a.id, b.id, r.algorithm, r.score, r.invoked_at "
            "ORDER BY r.score DESC LIMIT 3"
        )
        info("top 3 proposals by score:")
        for a, b, algo, score, when in rows:
            info(f"  {a} --[{algo} @ {when[:19]}]--> {b}  (score={score:.3f})")

        # ──────────────────────────────────────────────────────────
        step(6, "Filtered bitemporal diff (C5) — what changed, in which slice")
        # Simulate a lookback across a small window
        from_t = iso_minus(1)
        to_t = iso_minus(-1)

        # Full graph delta
        full = kg.diff(from_t, to_t)
        info(f"unfiltered:      {len(full['nodes_added'])} nodes, "
             f"{len(full['edges_added'])} edges added")

        # Scoped to research namespace, only Concepts and ProposalBy
        scoped = kg.diff(
            from_t, to_t,
            node_types=["Concept"],
            edge_types=["ProposalBy"],
            namespace="research",
        )
        ok(f"scope=research, node_types=[Concept], edge_types=[ProposalBy]:")
        info(f"  nodes_added:  {len(scoped['nodes_added'])}")
        info(f"  edges_added:  {len(scoped['edges_added'])}")
        info("a research loop watching only its slice gets a clean signal")

        # ──────────────────────────────────────────────────────────
        step(7, "Non-extraction ingest mode (C8) — for structured providers")
        # A provider that returns structured data (e.g. typed Run records)
        # sets extract_concepts=False on itself. The pipeline then stores
        # chunks with provenance but skips the Haiku concept-extraction pass.
        info("providers with `extract_concepts = False` skip the LLM pass.")
        info("ET stores the chunks + provenance; no concept nodes are emitted.")
        info("see tests/test_extract_concepts_flag.py for the flow.")

        # ──────────────────────────────────────────────────────────
        step(8, "Summary stats")
        stats = kg.get_stats(namespace="research")
        info(f"research namespace: "
             f"{stats['total_concepts']} concepts, "
             f"{stats['total_relationships']} relationships, "
             f"{stats['total_wisdoms']} wisdoms")

    print()
    print("━" * 60)
    print("Demo complete. Graph was ephemeral — tmp dir cleaned up.")
    print()
    print("Next steps for a real consumer:")
    print("  - Declare your types in your-project/schema/*.yaml importing malleus")
    print("  - Run scripts/gen_kuzu.py + scripts/gen_kuzu_types.py in your repo")
    print("  - Ontology.merged_with(your_ontology) at GraphStore construction")
    print("  - See docs/research-backbone.md for the full workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
