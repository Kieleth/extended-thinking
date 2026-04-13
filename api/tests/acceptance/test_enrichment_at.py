"""AT: ADR 011 v2 proactive-enrichment lifecycle.

Narrative: a user with a graph full of concepts opts into enrichment.
ET watches the memory namespace, notices concepts that cross the
frequency threshold, and attaches Wikipedia-shaped KnowledgeNodes
behind an `Enriches` edge with provenance. Later they purge one
source and the raw rows survive (bitemporal supersession, not delete).

Walks the full Phase A + B surface end-to-end through `Pipeline.sync()`
rather than exercising the MCP tools in isolation (those are covered
in `test_enrichment_mcp.py`):

    1. Master toggle off (default) → sync fires no enrichment work at
       all: no KnowledgeNode, no Enriches, no EnrichmentRun telemetry.
    2. Toggle on + threshold trigger + stub source + accept gate →
       sync attaches a KnowledgeNode + Enriches edge + EnrichmentRun
       telemetry per (trigger, source, concept).
    3. Per-source namespace isolation — the KnowledgeNode lands in
       `enrichment:<source_kind>`, invisible to default memory reads.
    4. Purge supersedes without deletion — bitemporal history survives.
    5. Telemetry is queryable: every run leaves an EnrichmentRun node
       with trigger_name + source_kind + concept_id + counts.
"""

from __future__ import annotations

import json

import pytest

from extended_thinking.algorithms.enrichment.protocol import (
    Candidate,
    GateVerdict,
)
from extended_thinking.algorithms.protocol import AlgorithmMeta
from extended_thinking.config.schema import EnrichmentConfig
from extended_thinking.mcp_server import handle_tool_call
from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.storage import StorageLayer


class _EmptyProvider:
    """No-op provider: sync() skips extraction entirely. Lets us focus the
    test on the enrichment phase without spending seconds parsing real
    Claude Code sessions the developer happens to have on disk."""
    name = "empty"
    extract_concepts = False
    def search(self, q, limit=20): return []
    def get_recent(self, since=None, limit=50): return []
    def get_entities(self): return []
    def store_insight(self, *a, **k): return ""
    def get_insights(self): return []
    def get_stats(self): return {"total_memories": 0, "last_updated": ""}
    def get_knowledge_graph(self): return None

pytestmark = pytest.mark.acceptance


# ── Stub plugins (no HTTP, no LLM) ───────────────────────────────────

class _StubWikipedia:
    meta = AlgorithmMeta(
        name="wikipedia",
        family="enrichment.sources",
        description="stub that always returns one canned article",
        paper_citation="n/a",
    )
    def source_kind(self) -> str:
        return "wikipedia"
    def search(self, **kw):
        name = kw["concept_name"]
        return [Candidate(
            external_id=f"Q_{name.replace(' ', '_')[:20]}",
            title=f"Wikipedia: {name}",
            abstract=f"Stub summary of {name} for AT coverage.",
            url=f"https://en.wikipedia.test/wiki/{name}",
            themes=["cs.stub"],
            source_kind="wikipedia",
        )]


class _AcceptGate:
    meta = AlgorithmMeta(
        name="stub_accept",
        family="enrichment.relevance_gates",
        description="accepts every candidate at score 0.8",
        paper_citation="n/a",
    )
    def judge(self, **kw):
        return GateVerdict(outcome="accept", score=0.8)


# ── Fixtures ─────────────────────────────────────────────────────────

def _seed_frequent_concepts(kg, ids, *, freq=10):
    """Seed concepts with frequency high enough to cross the default
    trigger threshold (3)."""
    from extended_thinking._schema import models as m
    for cid, name in ids:
        kg.insert(
            m.Concept(
                id=cid, name=name,
                category=m.ConceptCategory.topic,
                description=f"description of {name}",
                frequency=freq,
            ),
            namespace="memory",
            source="seed",
        )


def _install_stub_plugins(monkeypatch, *, with_trigger=True):
    """Force get_active() to return our stubs for the enrichment families.
    Everything else falls through to the real registry (so the main sync
    pipeline still finds its own plugins)."""
    import extended_thinking.algorithms as algos
    from extended_thinking.algorithms.enrichment.triggers.frequency_threshold import (
        FrequencyThresholdTrigger,
    )
    from extended_thinking.algorithms.enrichment.cache.time_to_refresh import (
        TimeToRefreshCache,
    )

    real_get_active = algos.get_active

    def _fake_get_active(family, config=None):
        if family == "enrichment.sources":
            return [_StubWikipedia()]
        if family == "enrichment.relevance_gates":
            return [_AcceptGate()]
        if family == "enrichment.triggers":
            return [FrequencyThresholdTrigger(min_frequency=3)] if with_trigger else []
        if family == "enrichment.cache":
            return [TimeToRefreshCache()]
        return real_get_active(family, config)

    monkeypatch.setattr(algos, "get_active", _fake_get_active)


@pytest.fixture
def et(tmp_data_dir, monkeypatch):
    """Pipeline with MCP server wired to a throwaway Kuzu + stub plugins.
    Default: enrichment DISABLED so tests explicitly opt in per case."""
    import extended_thinking.config as config_module
    import extended_thinking.mcp_server as srv

    storage = StorageLayer.default(tmp_data_dir / "enr")
    pipeline = Pipeline.from_storage(_EmptyProvider(), storage)
    monkeypatch.setattr(srv, "_get_pipeline", lambda: pipeline)

    # Default disabled — individual tests monkeypatch to enable.
    monkeypatch.setattr(
        config_module.settings,
        "enrichment",
        EnrichmentConfig(enabled=False, concept_namespace="memory"),
    )
    _install_stub_plugins(monkeypatch)
    yield pipeline


async def _mcp(name: str, args: dict) -> str:
    return await handle_tool_call(name, args)


def _count(kg, node_type: str) -> int:
    rows = kg._query_all(f"MATCH (n:{node_type}) RETURN count(n)")
    return rows[0][0] if rows else 0


# ── 1. Master toggle off ──────────────────────────────────────────────

class TestEnrichmentDisabledByDefault:
    """ADR 011 v2 contract: internal-only mode. With the toggle off,
    `Pipeline.sync()` must never fetch external data nor write
    KnowledgeNodes/Enriches/EnrichmentRun."""

    @pytest.mark.asyncio
    async def test_sync_does_not_enrich_when_disabled(self, et):
        _seed_frequent_concepts(et.store, [
            ("c-1", "sparse attention"),
            ("c-2", "flash attention"),
        ])

        result = await et.sync()

        # No enrichment section in the result when disabled.
        assert "enrichment" not in result
        # And nothing landed in the graph.
        assert _count(et.store, "KnowledgeNode") == 0
        assert _count(et.store, "EnrichmentRun") == 0


# ── 2. Happy path: toggle on, trigger fires, writes land ──────────────

class TestEnrichmentEnabledHappyPath:
    """Toggle on + concepts above threshold → sync attaches a
    KnowledgeNode + Enriches edge per (concept, source). One
    EnrichmentRun telemetry node per (trigger, source, concept)."""

    @pytest.mark.asyncio
    async def test_sync_attaches_knowledge_and_telemetry(self, et, monkeypatch):
        import extended_thinking.config as config_module
        monkeypatch.setattr(
            config_module.settings,
            "enrichment",
            EnrichmentConfig(enabled=True, concept_namespace="memory"),
        )

        _seed_frequent_concepts(et.store, [
            ("c-1", "sparse attention"),
            ("c-2", "flash attention"),
        ])

        result = await et.sync()

        # Summary shape from runner
        summary = result["enrichment"]
        assert summary["triggers_fired"] == 2
        assert summary["candidates_returned"] == 2
        assert summary["candidates_accepted"] == 2
        assert summary["knowledge_nodes_created"] == 2
        assert summary["edges_created"] == 2
        assert summary["runs_recorded"] == 2

        # Graph-side witness
        assert _count(et.store, "KnowledgeNode") == 2
        assert _count(et.store, "EnrichmentRun") == 2

        # Edges carry the gate verdict as relevance
        rows = et.store._query_all(
            "MATCH (c:Concept)-[r:Enriches]->(k:KnowledgeNode) "
            "RETURN c.id, k.source_kind, r.relevance, r.trigger "
            "ORDER BY c.id"
        )
        assert len(rows) == 2
        for concept_id, source_kind, relevance, trigger in rows:
            assert concept_id in {"c-1", "c-2"}
            assert source_kind == "wikipedia"
            assert relevance == pytest.approx(0.8)
            assert trigger == "frequency_threshold"

    @pytest.mark.asyncio
    async def test_enrichment_run_telemetry_is_queryable(self, et, monkeypatch):
        """One EnrichmentRun per (trigger, source, concept). Fields we
        tuned the thresholds on: trigger_name, source_kind, concept_id,
        candidates_returned, candidates_accepted, duration_ms."""
        import extended_thinking.config as config_module
        monkeypatch.setattr(
            config_module.settings,
            "enrichment",
            EnrichmentConfig(enabled=True, concept_namespace="memory"),
        )
        _seed_frequent_concepts(et.store, [("c-solo", "linear attention")])
        await et.sync()

        rows = et.store._query_all(
            "MATCH (e:EnrichmentRun) RETURN e.trigger_name, e.source_kind, "
            "e.concept_id, e.candidates_returned, e.candidates_accepted, "
            "e.duration_ms"
        )
        assert len(rows) == 1
        trig, src, cid, ret, acc, dur = rows[0]
        assert trig == "frequency_threshold"
        assert src == "wikipedia"
        assert cid == "c-solo"
        assert ret == 1 and acc == 1
        assert dur >= 0


# ── 3. Per-source namespace isolation ─────────────────────────────────

class TestNamespaceIsolation:
    """Each source writes into `enrichment:<source_kind>`. A default
    concept listing from the memory namespace must not surface the
    KnowledgeNodes — they're signals attached to concepts, not concepts
    themselves."""

    @pytest.mark.asyncio
    async def test_kn_lives_in_enrichment_namespace(self, et, monkeypatch):
        import extended_thinking.config as config_module
        monkeypatch.setattr(
            config_module.settings,
            "enrichment",
            EnrichmentConfig(enabled=True, concept_namespace="memory"),
        )
        _seed_frequent_concepts(et.store, [("c-1", "sparse attention")])
        await et.sync()

        # KN landed in enrichment:wikipedia
        rows = et.store._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'enrichment:wikipedia' "
            "RETURN count(k)"
        )
        assert rows[0][0] == 1

        # Same node in memory namespace: zero.
        rows = et.store._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'memory' "
            "RETURN count(k)"
        )
        assert rows[0][0] == 0

        # Concept listing from 'memory' doesn't accidentally surface the KN
        # (list_concepts only returns Concept, but this codifies the
        # expected isolation at the query surface).
        concepts = et.store.list_concepts(namespace="memory")
        ids = {c["id"] for c in concepts}
        assert ids == {"c-1"}


# ── 4. Purge: bitemporal supersession, not delete ────────────────────

class TestPurgeLifecycle:
    """Purging a source hides KnowledgeNodes from default queries but
    leaves the raw rows intact in Kuzu — auditable forever."""

    @pytest.mark.asyncio
    async def test_purge_hides_but_retains(self, et, monkeypatch):
        import extended_thinking.config as config_module
        monkeypatch.setattr(
            config_module.settings,
            "enrichment",
            EnrichmentConfig(enabled=True, concept_namespace="memory"),
        )
        _seed_frequent_concepts(et.store, [("c-1", "sparse attention")])
        await et.sync()

        # Pre-purge: et_extend surfaces the KN
        before = await _mcp("et_extend", {"concept_id": "c-1"})
        assert json.loads(before)["count"] >= 1

        # Purge the wikipedia source
        purge_result = await _mcp("et_extend_purge", {
            "source_kind": "wikipedia",
        })
        pdata = json.loads(purge_result)
        assert pdata["source_kind"] == "wikipedia"
        assert pdata["knowledge_nodes_superseded"] >= 1

        # Post-purge: default et_extend sees nothing
        after = await _mcp("et_extend", {"concept_id": "c-1"})
        assert json.loads(after)["count"] == 0

        # But raw rows still exist — bitemporal audit trail intact
        assert _count(et.store, "KnowledgeNode") >= 1


# ── 5. Force-fetch (consumer on-demand) ──────────────────────────────

class TestForceFetchLifecycle:
    """et_extend_force is the on-demand escape hatch — a consumer who
    doesn't want to wait for a trigger-driven sync can enrich one
    concept immediately."""

    @pytest.mark.asyncio
    async def test_force_bypasses_trigger(self, et, monkeypatch):
        """Force-fetch runs source→gate→write even when the trigger
        family is empty (so no auto-enrichment would fire)."""
        import extended_thinking.config as config_module
        monkeypatch.setattr(
            config_module.settings,
            "enrichment",
            EnrichmentConfig(enabled=True, concept_namespace="memory"),
        )
        # Reinstall with no triggers so we're sure the path isn't
        # coming from the automatic trigger-fire route.
        _install_stub_plugins(monkeypatch, with_trigger=False)

        _seed_frequent_concepts(et.store, [("c-forced", "on-demand topic")],
                                freq=1)  # below threshold — trigger wouldn't fire

        # First, confirm sync-with-no-trigger really does nothing
        result = await et.sync()
        assert result.get("enrichment", {}).get("knowledge_nodes_created", 0) == 0

        # Then force — one KN must land
        force_result = await _mcp("et_extend_force", {"concept_id": "c-forced"})
        data = json.loads(force_result)
        assert data["candidates_accepted"] == 1
        assert data["knowledge_nodes_created"] == 1

    @pytest.mark.asyncio
    async def test_force_requires_master_toggle(self, et):
        """enrichment.enabled = False (the fixture default) → force
        refuses. Belt-and-braces for the internal-only contract."""
        _seed_frequent_concepts(et.store, [("c-x", "x")])
        result = await _mcp("et_extend_force", {"concept_id": "c-x"})
        assert result.startswith("error:")
        assert "disabled" in result.lower()
