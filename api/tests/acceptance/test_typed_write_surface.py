"""AT: typed-write surface end-to-end (ADR 013 C1 / C3 / C4 / C5 / C8).

Narrative: a programmatic consumer (autoresearch-ET-shaped) uses ET as
a typed bitemporal state store. This AT walks the full write → query
loop through every surface we ship:

    1. HTTP POST /api/v2/graph/node and /edge (C3)
    2. MCP et_add_node / et_add_edge / et_write_rationale (C4)
    3. Filtered et_shift after a period of activity (C5)
    4. A non-extraction provider (C8) ingests chunks without firing
       the Haiku pass

Complements `tests/test_graph_v2_http.py`, `test_mcp_write_tools.py`,
`test_filtered_diff.py` (unit-shape coverage) by running the story as
a consumer would experience it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from extended_thinking.mcp_server import handle_tool_call
from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.providers.protocol import MemoryChunk
from extended_thinking.storage import StorageLayer

pytestmark = pytest.mark.acceptance


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def http_client(tmp_data_dir, monkeypatch):
    """FastAPI TestClient wired to a throwaway data dir.

    Overrides `_get_graph_store` so POST handlers land on the tmp path
    instead of the developer's real ~/.local/share/extended-thinking.
    """
    import extended_thinking.api.routes.graph_v2 as gv2

    fake_root = tmp_data_dir / "http-data"
    fake_root.mkdir()

    def _fake_store():
        from extended_thinking.storage.graph_store import GraphStore
        return GraphStore(fake_root / "knowledge")

    monkeypatch.setattr(gv2, "_get_graph_store", _fake_store)

    from extended_thinking.api.main import app
    return TestClient(app), fake_root


@pytest.fixture
def mcp_pipeline(tmp_data_dir, monkeypatch):
    """Pipeline wired to mcp_server so et_add_node / et_write_rationale
    dispatch against a throwaway Kuzu."""
    import extended_thinking.mcp_server as srv
    from extended_thinking.providers import get_provider

    data = tmp_data_dir / "mcp-data"
    storage = StorageLayer.default(data)
    cached = Pipeline.from_storage(get_provider(), storage)
    monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)
    return cached


async def _mcp(name: str, args: dict) -> str:
    return await handle_tool_call(name, args)


# ── C3: HTTP sync-write path ─────────────────────────────────────────

class TestHttpWritePath:
    """A consumer writes typed nodes + edges via HTTP. Every call
    returns after Kuzu commit; the next query sees the write. Shape
    mirrors autoresearch-ET's ETClient Protocol."""

    def test_programmatic_consumer_builds_a_concept_map(self, http_client):
        client, fake_root = http_client

        # Seed three related concepts in namespace='research'
        seeds = [
            ("h-1", "sparse attention reduces inference latency",
             "top-k sparsity over attention scores"),
            ("h-2", "flash attention uses tiling for memory efficiency",
             "fuse Q/K/V matmuls; avoid materializing full attention"),
            ("v-1", "block-sparse top-k with k=32, block=64",
             "implementation draft for h-1"),
        ]
        for cid, name, desc in seeds:
            r = client.post("/api/v2/graph/node", json={
                "type": "Concept",
                "properties": {
                    "id": cid, "name": name,
                    "category": "topic", "description": desc,
                },
                "namespace": "research",
                "source": "autoresearch-et",
            })
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["id"] == cid
            assert body["vectors_pending"] is True

        # Connect them
        r = client.post("/api/v2/graph/edge", json={
            "type": "RelatesTo",
            "properties": {
                "id": "e-1",
                "source_id": "h-1", "target_id": "v-1",
                "relation_type": "implements",
                "weight": 1.0,
            },
            "namespace": "research",
        })
        assert r.status_code == 201, r.text

        # Re-open the store and verify write-then-read coherence
        from extended_thinking.storage.graph_store import GraphStore
        kg = GraphStore(fake_root / "knowledge")
        rows = kg.list_concepts(namespace="research")
        assert len(rows) == 3
        assert {c["id"] for c in rows} == {"h-1", "h-2", "v-1"}

    def test_domain_violation_rejected_by_binder(self, http_client):
        """Wisdom→Concept via RelatesTo is not allowed. Architecture A
        guarantee: Kuzu's binder stops the write before commit."""
        client, fake_root = http_client

        # Seed a Concept and a Wisdom (signal_type + bearer_id required by malleus)
        client.post("/api/v2/graph/node", json={
            "type": "Concept",
            "properties": {"id": "c1", "name": "c1",
                           "category": "topic", "description": ""},
            "namespace": "research",
        })
        client.post("/api/v2/graph/node", json={
            "type": "Wisdom",
            "properties": {
                "id": "w1", "name": "w1", "title": "some wisdom",
                "wisdom_type": "wisdom",
                "signal_type": "wisdom_card", "bearer_id": "c1",
            },
            "namespace": "research",
        })

        # Attempt Wisdom→Concept via RelatesTo — pinned Concept→Concept
        r = client.post("/api/v2/graph/edge", json={
            "type": "RelatesTo",
            "properties": {
                "id": "bad",
                "source_id": "w1", "target_id": "c1",
                "relation_type": "r",
            },
            "namespace": "research",
        })
        assert r.status_code in (400, 409), (r.status_code, r.text)


# ── C4: MCP write tools + grounded rationale ─────────────────────────

class TestMcpWriteTools:
    """Same writes as C3 but via MCP stdio. Adds the grounded-rationale
    guard as the focal test — an LLM citation that doesn't resolve is
    rejected before commit."""

    @pytest.mark.asyncio
    async def test_write_rationale_with_all_citations_resolving(self, mcp_pipeline):
        """Seed two concepts, attach a rationale citing both → commit."""
        for cid in ("subj", "cite-a"):
            await _mcp("et_add_node", {
                "type": "Concept",
                "properties": {
                    "id": cid, "name": cid, "category": "topic",
                    "description": "",
                },
                "namespace": "research",
            })

        result = await _mcp("et_write_rationale", {
            "subject_node_id": "subj",
            "text": "cite-a supports subj.",
            "cited_node_ids": ["cite-a"],
            "namespace": "research",
        })
        data = json.loads(result)
        assert data["cited_count"] == 1
        assert data["id"].startswith("rationale-")

    @pytest.mark.asyncio
    async def test_write_rationale_rejects_unresolved_citation(self, mcp_pipeline):
        """The grounded-rationale guard: at least one citation doesn't
        resolve → write refused with all missing ids listed."""
        await _mcp("et_add_node", {
            "type": "Concept",
            "properties": {"id": "subj", "name": "subj",
                           "category": "topic", "description": ""},
            "namespace": "research",
        })

        result = await _mcp("et_write_rationale", {
            "subject_node_id": "subj",
            "text": "hallucination attempt",
            "cited_node_ids": ["ghost-1", "ghost-2"],
            "namespace": "research",
        })
        assert result.startswith("error:")
        assert "grounded-rationale guarantee violated" in result
        assert "ghost-1" in result and "ghost-2" in result

        # No Rationale landed
        count = mcp_pipeline.store._query_one(
            "MATCH (r:Rationale) RETURN count(r)"
        )
        assert count[0] == 0


# ── C5: filtered bitemporal diff ─────────────────────────────────────

class TestFilteredDiff:
    """After a period of activity spanning memory AND research namespaces,
    a consumer's slice must come back clean."""

    def test_consumer_sees_only_their_slice(self, http_client):
        client, fake_root = http_client

        # Programmatic writes in research namespace
        for cid, name in (("r-1", "alpha"), ("r-2", "beta")):
            client.post("/api/v2/graph/node", json={
                "type": "Concept",
                "properties": {
                    "id": cid, "name": name,
                    "category": "topic", "description": "",
                },
                "namespace": "research",
            })

        # Memory-side noise (using GraphStore directly — simulates
        # memory pipeline writes that would normally land via add_concept)
        from extended_thinking.storage.graph_store import GraphStore
        kg = GraphStore(fake_root / "knowledge")
        kg.add_concept("m-1", "memory noise", "topic", "")

        # Scoped diff — should see r-* only
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        scoped = kg.diff(
            past, future,
            node_types=["Concept"], namespace="research",
        )
        ids = {n["id"] for n in scoped["nodes_added"]}
        assert ids == {"r-1", "r-2"}
        assert "m-1" not in ids

        # Unfiltered diff — includes memory
        unscoped = kg.diff(past, future, node_types=["Concept"])
        ids_all = {n["id"] for n in unscoped["nodes_added"]}
        assert ids_all >= {"r-1", "r-2", "m-1"}


# ── C8: non-extraction provider ──────────────────────────────────────

class TestNonExtractionProvider:
    """A structured-data provider opts out of the Haiku extraction loop.
    Chunks still land with provenance (dedup across restarts). No
    concepts emitted for those chunks."""

    @pytest.mark.asyncio
    async def test_structured_provider_skips_llm(self, tmp_data_dir):
        from extended_thinking.processing import pipeline_v2

        chunks = [
            MemoryChunk(
                id=f"structured-{i}",
                content=f"typed run record {i}",
                source="typed-provider://run-42",
                timestamp="2026-04-10T12:00:00+00:00",
                metadata={"provider": "typed"},
            )
            for i in range(3)
        ]

        class _StructuredProvider:
            name = "structured"
            extract_concepts = False

            def search(self, q, limit=20):
                return []
            def get_recent(self, since=None, limit=50):
                return list(chunks)
            def get_entities(self):
                return []
            def store_insight(self, *a, **k):
                return ""
            def get_insights(self):
                return []
            def get_stats(self):
                return {"total_memories": 0, "last_updated": ""}
            def get_knowledge_graph(self):
                return None

        storage = StorageLayer.lite(tmp_data_dir / "s")
        pipe = Pipeline.from_storage(_StructuredProvider(), storage)

        # Spy on the extractor so we can assert zero calls
        calls = {"n": 0}

        async def _fake(*a, **k):
            calls["n"] += 1
            return []

        from unittest.mock import patch
        with patch.object(
            pipeline_v2, "extract_concepts_from_chunks", new=_fake,
        ):
            result = await pipe.sync()

        assert calls["n"] == 0, "extractor should not run for extract_concepts=False"
        assert result["concepts_extracted"] == 0
        # But chunks were still processed (marked for idempotency)
        rows = storage.kg._query_all("MATCH (c:Chunk) RETURN count(c)")
        assert rows[0][0] == 3

    @pytest.mark.asyncio
    async def test_conversation_provider_still_extracts(
        self, tmp_data_dir, cc_session_small,
    ):
        """Conversation providers (default extract_concepts=True) run
        extraction as before. Locking this in so C8 doesn't regress the
        memory-side flow."""
        from extended_thinking.processing import pipeline_v2
        from unittest.mock import patch
        from tests.helpers.fake_llm import DummyLM

        storage = StorageLayer.lite(tmp_data_dir / "cc")
        pipe = Pipeline.from_storage(cc_session_small, storage)

        fake = DummyLM({"CONVERSATION:": json.dumps([{
            "name": "Kuzu", "category": "entity",
            "description": "embedded graph db",
            "source_quote": "look at Kuzu",
        }])}, default="[]")

        with patch(
            "extended_thinking.processing.extractor.get_provider",
            return_value=fake,
        ):
            result = await pipe.sync()

        assert result["concepts_extracted"] >= 1
        assert fake.calls, "conversation provider should trigger the extractor"
