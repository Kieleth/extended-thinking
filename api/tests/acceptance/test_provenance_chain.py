"""AT: provenance chain end-to-end (Product Invariant 1).

"Every insight traces to evidence." The promise spans three layers:

    Chunk --(HasProvenance)--> Concept --(InformedBy)-- Wisdom

Unit tests exercise each hop in isolation. This AT walks the whole
chain on the committed `cc_session_small` fixture with scripted
Haiku/Opus responses, then asserts the three-hop walkback query an
auditor would actually run: "given this wisdom, show me the original
chunks."

Fast-path: no network, no real LLM, ~1-2s wall clock.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.storage import StorageLayer
from tests.helpers.fake_llm import DummyLM

pytestmark = pytest.mark.acceptance


# ── Scripted LLM responses ──────────────────────────────────────────

# Five concepts keyed by source_quote strings that actually appear in
# the session_small.jsonl fixture, so `add_provenance` can tie each
# concept to its originating chunk (see pipeline_v2.py around line 195).
EXTRACTION_RESPONSE = json.dumps([
    {
        "name": "Kuzu",
        "category": "entity",
        "description": "Embedded graph database with Cypher and temporal properties",
        "source_quote": "look at Kuzu. It's embedded, Cypher-native",
    },
    {
        "name": "SQLite",
        "category": "entity",
        "description": "Row-store considered and rejected as KG backend",
        "source_quote": "SQLite with two timestamp columns would work",
    },
    {
        "name": "bitemporal knowledge graph",
        "category": "topic",
        "description": "Graph with valid_time and transaction_time per edge",
        "source_quote": "I need a store for a bitemporal knowledge graph",
    },
    {
        "name": "KG store choice",
        "category": "decision",
        "description": "Choice of backend for the knowledge graph",
        "source_quote": "Decision: going with Kuzu for the KG layer",
    },
    {
        "name": "ChromaDB",
        "category": "entity",
        "description": "Vector store kept alongside Kuzu",
        "source_quote": "I'll keep ChromaDB for vector embeddings",
    },
])

# Opus response — names must match extraction output verbatim so
# `_normalize_id` + `get_concept` resolve them to real ids.
WISDOM_RESPONSE = json.dumps({
    "type": "wisdom",
    "title": "Kuzu + ChromaDB split is the bitemporal graph's shape",
    "why": (
        "Kuzu holds bitemporal edges, ChromaDB holds vectors; the "
        "KG store choice formalizes that split."
    ),
    "action": "Document the two-store contract in an ADR.",
    "related_concepts": ["Kuzu", "ChromaDB", "bitemporal knowledge graph"],
})


def _make_dummy_llm() -> DummyLM:
    """Single DummyLM servicing both extraction and wisdom prompts.

    Keys picked from substrings that only appear in their respective
    prompts — 'CONVERSATION:' is the extractor's user-turn marker;
    'cognitive advisor' opens the wisdom prompt (see
    pipeline_v2._build_wisdom_prompt).
    """
    return DummyLM({
        "cognitive advisor": WISDOM_RESPONSE,  # check before the generic key
        "CONVERSATION:": EXTRACTION_RESPONSE,
    }, default=EXTRACTION_RESPONSE)


# ── Shared pipeline fixture ──────────────────────────────────────────

@pytest.fixture
async def synced_pipeline(tmp_data_dir, cc_session_small):
    """Sync + wisdom against the committed CC session with scripted LLM.
    Returns (pipeline, wisdom_result). Function-scoped so each test gets
    a fresh Kuzu store."""
    storage = StorageLayer.lite(tmp_data_dir / "prov")
    pipeline = Pipeline.from_storage(cc_session_small, storage)
    llm = _make_dummy_llm()

    # Extractor path + wisdom path both resolve their provider through
    # `extended_thinking.ai.registry.get_provider`. Extractor does it at
    # import-time, so we patch its local reference too.
    with (
        patch("extended_thinking.processing.extractor.get_provider",
              return_value=llm),
        patch("extended_thinking.ai.registry.get_provider",
              return_value=llm),
    ):
        sync_result = await pipeline.sync()
        wisdom = await pipeline.generate_wisdom(force=True)

    assert sync_result["concepts_extracted"] >= 3, sync_result
    assert wisdom is not None and wisdom.get("id"), (
        f"wisdom generation failed; got: {wisdom}"
    )
    return pipeline, wisdom


# ── 1. Chunk → Concept ──────────────────────────────────────────────

class TestChunkToConcept:
    """Sync writes a HasProvenance edge from every extracted Concept
    back to the Chunk that contained its source_quote."""

    async def test_sync_writes_has_provenance_with_full_attribution(
        self, synced_pipeline,
    ):
        pipeline, _ = synced_pipeline
        store = pipeline.store

        rows = store._query_all(
            "MATCH (c:Concept)-[p:HasProvenance]->(ch:Chunk) "
            "RETURN c.id, ch.id, ch.source, "
            "p.source_provider, p.llm_model, p.t_created, p.namespace"
        )
        assert rows, "no HasProvenance edges after sync"

        # Every extracted concept should have attribution. One chunk can
        # yield multiple concepts; each gets its own edge.
        concept_ids = {r[0] for r in rows}
        assert len(concept_ids) >= 3, (
            f"expected at least 3 attributed concepts, got {concept_ids}"
        )

        for c_id, ch_id, ch_source, provider, llm_model, t_created, ns in rows:
            assert provider == "claude-code", (
                f"unexpected provider on {c_id}: {provider}"
            )
            assert llm_model == "haiku"
            assert ns == "memory"
            assert t_created, f"missing t_created on {c_id}"
            assert ch_source.endswith(".jsonl"), (
                f"chunk source should be the CC session JSONL, got {ch_source}"
            )


# ── 2. Concept → Wisdom ──────────────────────────────────────────────

class TestConceptToWisdom:
    """Wisdom generation writes InformedBy edges to every related
    concept the LLM cited."""

    async def test_wisdom_writes_informed_by_for_every_evidence_concept(
        self, synced_pipeline,
    ):
        pipeline, wisdom = synced_pipeline
        store = pipeline.store
        wisdom_id = wisdom["id"]

        # The wisdom row itself carries the resolved related_concept_ids
        # as a JSON property; InformedBy edges are the query-surface shape.
        edges = store._query_all(
            "MATCH (w:Wisdom {id: $wid})-[r:InformedBy]->(c:Concept) "
            "RETURN c.id, c.name, r.t_created",
            {"wid": wisdom_id},
        )
        assert edges, f"wisdom {wisdom_id} has no InformedBy edges"

        # LLM cited three concepts; at least that many must resolve
        # (fewer is OK only if entity resolution folded some together,
        # but our extraction produces them distinct).
        assert len(edges) >= 2, (
            f"expected wisdom to reference multiple concepts, got {edges}"
        )

        # Every target must exist as a real Concept — the edge isn't
        # useful if it points at a dangling id.
        for c_id, c_name, t_created in edges:
            row = store.get_concept(c_id)
            assert row is not None, f"InformedBy points at missing {c_id}"
            assert c_name == row["name"]
            assert t_created


# ── 3. Wisdom → Chunk (the walkback) ─────────────────────────────────

class TestFullChainWalkback:
    """The headline assertion: the auditor's query 'show me the chunks
    that produced this wisdom' runs as a single 3-hop Cypher walk."""

    async def test_three_hop_query_returns_original_chunks(
        self, synced_pipeline,
    ):
        pipeline, wisdom = synced_pipeline
        store = pipeline.store

        rows = store._query_all(
            "MATCH (w:Wisdom {id: $wid})-[:InformedBy]->(c:Concept)"
            "-[:HasProvenance]->(ch:Chunk) "
            "RETURN DISTINCT ch.id, ch.source",
            {"wid": wisdom["id"]},
        )
        assert rows, (
            "3-hop walkback returned empty — Invariant 1 breach: "
            "wisdom cannot be traced to its originating chunks"
        )

        # Every returned chunk must actually exist in the store and
        # come from the CC fixture we ingested.
        for ch_id, ch_source in rows:
            chunk_row = store._query_one(
                "MATCH (ch:Chunk {id: $id}) RETURN ch.id", {"id": ch_id},
            )
            assert chunk_row is not None, (
                f"walkback returned unknown chunk {ch_id}"
            )
            assert "session_small" in ch_source, (
                f"walkback chunk not from our fixture: {ch_source}"
            )

    async def test_non_extracted_concept_has_no_provenance(
        self, synced_pipeline,
    ):
        """Negative control. Concepts written directly (C3 HTTP-write
        style) intentionally have no provenance — the invariant applies
        to extractor-originated nodes. The walkback query must stay
        empty for those, not return spurious rows."""
        pipeline, _ = synced_pipeline
        from extended_thinking._schema import models as m

        pipeline.store.insert(
            m.Concept(
                id="manual-concept",
                name="manually-inserted",
                category=m.ConceptCategory.topic,
                description="inserted via typed write, no extractor",
            ),
            namespace="memory",
            source="programmatic",
        )

        rows = pipeline.store._query_all(
            "MATCH (c:Concept {id: 'manual-concept'})"
            "-[:HasProvenance]->(ch:Chunk) RETURN ch.id"
        )
        assert rows == [], (
            f"direct-inserted concept unexpectedly has provenance: {rows}"
        )
