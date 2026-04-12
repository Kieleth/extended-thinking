"""ADR 013 C4: write-side MCP tools.

Covers `et_add_node`, `et_add_edge`, and `et_write_rationale` — the MCP
surface of the typed write path. Shares semantics with the HTTP surface
(C3) but lives on stdio, which is how Claude Code and similar MCP
clients reach ET.

The grounded-rationale guarantee (R8 / ADR 013 C4) is the key invariant:
an LLM-written rationale with dangling citations must be rejected, not
silently accepted.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.mcp_server import handle_tool_call
from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """A pipeline backed by a throwaway Kuzu database.

    The MCP dispatch calls `_get_pipeline()` on every tool call; we cache
    one instance so all writes share one Kuzu handle (Kuzu serializes
    writers per-dir, so two handles don't see each other's uncommitted
    state reliably).
    """
    import extended_thinking.mcp_server as srv
    from extended_thinking.processing.pipeline_v2 import Pipeline
    from extended_thinking.providers import get_provider
    from extended_thinking.storage import StorageLayer

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    storage = StorageLayer.default(data_dir)
    cached = Pipeline.from_storage(get_provider(), storage)

    monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)
    yield cached


async def _call(name: str, arguments: dict) -> str:
    """Convenience: call handle_tool_call synchronously from sync tests."""
    return await handle_tool_call(name, arguments)


# ── et_add_node ───────────────────────────────────────────────────────

class TestEtAddNode:

    async def test_writes_typed_concept(self, pipeline):
        result = await _call("et_add_node", {
            "type": "Concept",
            "properties": {
                "id": "c-1",
                "name": "sparse attention",
                "category": "topic",
                "description": "a technique",
            },
            "namespace": "research",
            "source": "mcp-test",
        })
        data = json.loads(result)
        assert data["id"] == "c-1"
        assert data["type"] == "Concept"
        assert data["namespace"] == "research"
        assert data["vectors_pending"] is True

        # Round-trip via the same pipeline's store
        got = pipeline.store.get_concept("c-1")
        assert got is not None
        assert got["name"] == "sparse attention"

    async def test_unknown_type_returns_error(self, pipeline):
        result = await _call("et_add_node", {
            "type": "Nonsense",
            "properties": {"id": "x"},
        })
        assert result.startswith("error:")
        assert "unknown" in result.lower()

    async def test_edge_type_on_node_route_errors(self, pipeline):
        result = await _call("et_add_node", {
            "type": "RelatesTo",  # edge
            "properties": {"id": "x", "source_id": "a", "target_id": "b",
                           "relation_type": "sem"},
        })
        assert result.startswith("error:")
        assert "et_add_edge" in result

    async def test_missing_required_field_errors(self, pipeline):
        result = await _call("et_add_node", {
            "type": "Concept",
            "properties": {"id": "c-2", "name": "x"},  # missing category
        })
        assert result.startswith("error:")
        assert "validation" in result.lower()


# ── et_add_edge ───────────────────────────────────────────────────────

class TestEtAddEdge:

    async def _seed_concepts(self, pipeline):
        await _call("et_add_node", {
            "type": "Concept",
            "properties": {"id": "c-a", "name": "alpha", "category": "topic",
                           "description": ""},
            "namespace": "research",
        })
        await _call("et_add_node", {
            "type": "Concept",
            "properties": {"id": "c-b", "name": "beta", "category": "topic",
                           "description": ""},
            "namespace": "research",
        })

    async def test_writes_typed_edge(self, pipeline):
        await self._seed_concepts(pipeline)
        result = await _call("et_add_edge", {
            "type": "RelatesTo",
            "properties": {
                "id": "e-1",
                "source_id": "c-a", "target_id": "c-b",
                "relation_type": "semantic",
                "weight": 0.9,
            },
            "namespace": "research",
        })
        data = json.loads(result)
        assert data["id"] == "e-1"

    async def test_missing_endpoint_errors(self, pipeline):
        await self._seed_concepts(pipeline)
        result = await _call("et_add_edge", {
            "type": "RelatesTo",
            "properties": {
                "id": "e-bad",
                "source_id": "does-not-exist",
                "target_id": "c-b",
                "relation_type": "semantic",
            },
        })
        assert result.startswith("error:")
        assert "not found" in result.lower()

    async def test_wrong_domain_rejected(self, pipeline):
        """Wisdom → Concept via RelatesTo violates the ontology. Kuzu
        rejects it at the binder; we surface that."""
        await self._seed_concepts(pipeline)
        await _call("et_add_node", {
            "type": "Wisdom",
            "properties": {
                "id": "w-1", "name": "w", "title": "test",
                "wisdom_type": "wisdom",
                "signal_type": "wisdom_card", "bearer_id": "c-a",
            },
            "namespace": "research",
        })
        result = await _call("et_add_edge", {
            "type": "RelatesTo",  # pinned Concept→Concept
            "properties": {
                "id": "e-bad",
                "source_id": "w-1",  # Wisdom, not Concept
                "target_id": "c-a",
                "relation_type": "semantic",
            },
            "namespace": "research",
        })
        assert result.startswith("error:")

    async def test_node_type_on_edge_route_errors(self, pipeline):
        result = await _call("et_add_edge", {
            "type": "Concept",
            "properties": {"id": "x"},
        })
        assert result.startswith("error:")
        assert "et_add_node" in result


# ── et_write_rationale — the grounded guarantee ───────────────────────

class TestEtWriteRationale:

    async def _seed(self, pipeline):
        """Two concepts to cite, one subject."""
        for cid, nm in (("subj", "subject"), ("cite-1", "c1"), ("cite-2", "c2")):
            await _call("et_add_node", {
                "type": "Concept",
                "properties": {"id": cid, "name": nm, "category": "topic",
                               "description": ""},
                "namespace": "research",
            })

    async def test_writes_rationale_with_all_citations_resolving(self, pipeline):
        await self._seed(pipeline)
        result = await _call("et_write_rationale", {
            "subject_node_id": "subj",
            "text": "because cite-1 and cite-2 jointly imply subj",
            "cited_node_ids": ["cite-1", "cite-2"],
            "namespace": "research",
            "source": "test-llm",
        })
        data = json.loads(result)
        assert data["subject_node_id"] == "subj"
        assert data["cited_count"] == 2
        assert data["id"].startswith("rationale-")

        # Verify the Rationale node landed and stored the citations
        row = pipeline.store._query_one(
            "MATCH (r:Rationale {id: $id}) "
            "RETURN r.bearer_id, r.text, r.cited_node_ids",
            {"id": data["id"]},
        )
        assert row[0] == "subj"
        assert row[1].startswith("because")
        assert set(json.loads(row[2])) == {"cite-1", "cite-2"}

    async def test_unresolved_citation_rejected(self, pipeline):
        """The grounded-rationale guarantee: an id that doesn't resolve
        must stop the write. No partial Rationale node should appear."""
        await self._seed(pipeline)
        result = await _call("et_write_rationale", {
            "subject_node_id": "subj",
            "text": "argument",
            "cited_node_ids": ["cite-1", "does-not-exist"],
            "namespace": "research",
        })
        assert result.startswith("error:")
        assert "grounded-rationale guarantee violated" in result
        assert "does-not-exist" in result

        # Ensure no Rationale was created — the DB must stay clean
        count = pipeline.store._query_one("MATCH (r:Rationale) RETURN count(r)")
        assert count[0] == 0

    async def test_multiple_unresolved_reported_together(self, pipeline):
        """When many citations fail, report them all at once so the caller
        fixes in one pass rather than chasing errors sequentially."""
        await self._seed(pipeline)
        result = await _call("et_write_rationale", {
            "subject_node_id": "subj",
            "text": "x",
            "cited_node_ids": ["cite-1", "missing-a", "missing-b", "missing-c"],
            "namespace": "research",
        })
        assert result.startswith("error:")
        for missing in ("missing-a", "missing-b", "missing-c"):
            assert missing in result
        # cite-1 exists → not listed
        assert "cite-1\n" not in result.split("\nWrite")[0] or \
               result.count("cite-1") == 0

    async def test_missing_subject_rejected(self, pipeline):
        await self._seed(pipeline)
        result = await _call("et_write_rationale", {
            "subject_node_id": "nonexistent-subject",
            "text": "x",
            "cited_node_ids": ["cite-1"],
        })
        assert result.startswith("error:")
        assert "subject" in result.lower()

    async def test_empty_citations_allowed(self, pipeline):
        """A rationale with no citations is allowed — the guarantee is that
        IF citations are listed, they must resolve; zero is trivially valid."""
        await self._seed(pipeline)
        result = await _call("et_write_rationale", {
            "subject_node_id": "subj",
            "text": "self-evident",
            "cited_node_ids": [],
            "namespace": "research",
        })
        data = json.loads(result)
        assert data["cited_count"] == 0

    async def test_missing_required_args_rejected(self, pipeline):
        result = await _call("et_write_rationale", {
            "text": "x",
        })
        assert result.startswith("error:")
        assert "subject_node_id" in result

        result = await _call("et_write_rationale", {
            "subject_node_id": "subj",
        })
        assert result.startswith("error:")
        assert "text" in result.lower()


# ── Tool schema visibility ────────────────────────────────────────────

class TestToolRegistration:

    def test_write_tools_listed_in_TOOLS(self):
        from extended_thinking.mcp_server import TOOLS
        names = {t["name"] for t in TOOLS}
        assert "et_add_node" in names
        assert "et_add_edge" in names
        assert "et_write_rationale" in names

    def test_each_write_tool_has_input_schema(self):
        from extended_thinking.mcp_server import TOOLS
        for tool in TOOLS:
            if tool["name"] in ("et_add_node", "et_add_edge", "et_write_rationale"):
                assert "inputSchema" in tool
                assert tool["inputSchema"]["type"] == "object"
                assert "required" in tool["inputSchema"]
