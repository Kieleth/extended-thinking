"""ADR 011 v2 B.5: enrichment MCP tools.

Three tools:
  - et_extend         — read attached KnowledgeNodes
  - et_extend_force   — on-demand enrichment (gated on [enrichment] enabled)
  - et_extend_purge   — bitemporal supersession per source

Uses stub sources/triggers/gates so no HTTP or LLM calls fire in tests.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.algorithms.enrichment.protocol import (
    Candidate,
    GateVerdict,
)
from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta
from extended_thinking.config.schema import EnrichmentConfig


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """A pipeline with a throwaway Kuzu + VectorStore + stub enrichment plugins.
    The fixture patches get_active for enrichment families to return our stubs
    so we don't depend on real Wikipedia/HTTP."""
    import extended_thinking.algorithms as algos
    import extended_thinking.algorithms.enrichment.runner as runner_mod
    import extended_thinking.config as config_module
    import extended_thinking.mcp_server as srv
    from extended_thinking.processing.pipeline_v2 import Pipeline
    from extended_thinking.providers import get_provider
    from extended_thinking.storage import StorageLayer

    storage = StorageLayer.default(tmp_path / "d")
    cached = Pipeline.from_storage(get_provider(), storage)
    monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)

    # Enable enrichment via settings (force the module-level singleton)
    monkeypatch.setattr(
        config_module.settings,
        "enrichment",
        EnrichmentConfig(enabled=True, concept_namespace="memory"),
    )

    class _StubSource:
        meta = AlgorithmMeta(
            name="wikipedia",
            family="enrichment.sources",
            description="stub",
            paper_citation="n/a",
        )
        def source_kind(self): return "wikipedia"
        def search(self, **kw):
            return [Candidate(
                external_id="Q_stub",
                title="Stub article",
                abstract="A stub abstract about " + kw["concept_name"],
                url="https://example.test/Q_stub",
                themes=["cs.stub"],
                source_kind="wikipedia",
            )]

    class _AcceptGate:
        meta = AlgorithmMeta(
            name="stub_accept",
            family="enrichment.relevance_gates",
            description="",
            paper_citation="",
        )
        def judge(self, **kw):
            return GateVerdict(outcome="accept", score=0.88)

    def _fake_get_active(family, config):
        if family == "enrichment.sources":
            return [_StubSource()]
        if family == "enrichment.relevance_gates":
            return [_AcceptGate()]
        if family == "enrichment.triggers":
            return []  # empty — tests drive via et_extend_force
        if family == "enrichment.cache":
            return []
        # Fall through to the real registry for any non-enrichment family
        # (e.g. the main sync pipeline's own get_active calls).
        from extended_thinking.algorithms import registry as _reg
        return _reg.get_active.__wrapped__(family, config) if hasattr(
            _reg.get_active, "__wrapped__"
        ) else _reg.get_active(family, config)

    # Only patch the symbol the runner imports via the top-level alias;
    # other sites (pipeline_v2, graph_store) keep the real registry.
    monkeypatch.setattr(algos, "get_active", _fake_get_active)

    yield cached


async def _call(name, args):
    from extended_thinking.mcp_server import handle_tool_call
    return await handle_tool_call(name, args)


# ── et_extend_force ──────────────────────────────────────────────────

class TestEtExtendForce:

    @pytest.mark.asyncio
    async def test_force_writes_knowledge_node_and_edge(self, pipeline):
        pipeline.store.add_concept(
            "c-1", "sparse attention", "topic",
            "efficient transformer attention technique",
        )
        result = await _call("et_extend_force", {"concept_id": "c-1"})
        data = json.loads(result)
        assert data["candidates_accepted"] == 1
        assert data["knowledge_nodes_created"] >= 1
        assert data["edges_created"] >= 1

        # KnowledgeNode landed with the right namespace
        rows = pipeline.store._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'enrichment:wikipedia' "
            "RETURN k.id, k.title"
        )
        assert len(rows) >= 1

    @pytest.mark.asyncio
    async def test_force_requires_enabled(self, pipeline, monkeypatch):
        """Master toggle off → force refuses."""
        import extended_thinking.config as config_module
        monkeypatch.setattr(
            config_module.settings,
            "enrichment",
            EnrichmentConfig(enabled=False),
        )
        pipeline.store.add_concept("c-1", "x", "topic", "")
        result = await _call("et_extend_force", {"concept_id": "c-1"})
        assert result.startswith("error:")
        assert "disabled" in result.lower()

    @pytest.mark.asyncio
    async def test_force_nonexistent_concept_rejected(self, pipeline):
        result = await _call("et_extend_force", {"concept_id": "nope"})
        assert result.startswith("error:")
        assert "not found" in result.lower()


# ── et_extend (read) ─────────────────────────────────────────────────

class TestEtExtend:

    @pytest.mark.asyncio
    async def test_lists_attached_knowledge_nodes(self, pipeline):
        pipeline.store.add_concept(
            "c-1", "sparse attention", "topic", "y",
        )
        # Use force to seed, then read back
        await _call("et_extend_force", {"concept_id": "c-1"})
        result = await _call("et_extend", {"concept_id": "c-1"})
        data = json.loads(result)
        assert data["concept_id"] == "c-1"
        assert data["count"] >= 1
        kn = data["knowledge_nodes"][0]
        assert kn["source_kind"] == "wikipedia"
        assert kn["title"]
        assert kn["themes"]
        assert kn["relevance"] is not None

    @pytest.mark.asyncio
    async def test_theme_filter(self, pipeline):
        pipeline.store.add_concept("c-1", "x", "topic", "")
        await _call("et_extend_force", {"concept_id": "c-1"})
        # Stub tags every article with cs.stub — filter should match
        result = await _call("et_extend", {
            "concept_id": "c-1", "theme": "cs.stub",
        })
        data = json.loads(result)
        assert data["count"] >= 1

        # A theme that wasn't applied → zero matches
        result = await _call("et_extend", {
            "concept_id": "c-1", "theme": "nonexistent",
        })
        data = json.loads(result)
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_concept_without_enrichment_returns_empty_list(self, pipeline):
        pipeline.store.add_concept("c-bare", "bare", "topic", "")
        result = await _call("et_extend", {"concept_id": "c-bare"})
        data = json.loads(result)
        assert data["count"] == 0
        assert data["knowledge_nodes"] == []

    @pytest.mark.asyncio
    async def test_nonexistent_concept_errors(self, pipeline):
        result = await _call("et_extend", {"concept_id": "nope"})
        assert result.startswith("error:")


# ── et_extend_purge ──────────────────────────────────────────────────

class TestEtExtendPurge:

    @pytest.mark.asyncio
    async def test_purge_supersedes_but_does_not_delete(self, pipeline):
        pipeline.store.add_concept("c-1", "x", "topic", "")
        await _call("et_extend_force", {"concept_id": "c-1"})

        # Before purge
        before = await _call("et_extend", {"concept_id": "c-1"})
        assert json.loads(before)["count"] >= 1

        purge_result = await _call("et_extend_purge", {
            "source_kind": "wikipedia",
        })
        pdata = json.loads(purge_result)
        assert pdata["source_kind"] == "wikipedia"
        assert pdata["namespace"] == "enrichment:wikipedia"
        assert pdata["knowledge_nodes_superseded"] >= 1

        # After purge — default query no longer sees them
        after = await _call("et_extend", {"concept_id": "c-1"})
        assert json.loads(after)["count"] == 0

        # But the raw rows still exist in Kuzu (bitemporal)
        rows = pipeline.store._query_all(
            "MATCH (k:KnowledgeNode) RETURN count(k)"
        )
        assert rows[0][0] >= 1

    @pytest.mark.asyncio
    async def test_purge_requires_source_kind(self, pipeline):
        result = await _call("et_extend_purge", {})
        assert result.startswith("error:")
        assert "source_kind" in result


# ── Tool schema registration ──────────────────────────────────────────

class TestSchemaRegistration:

    def test_all_three_tools_registered(self):
        from extended_thinking.mcp_server import TOOLS
        names = {t["name"] for t in TOOLS}
        assert {"et_extend", "et_extend_force", "et_extend_purge"} <= names
