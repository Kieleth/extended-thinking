"""Unit: Pipeline.sync() runs enrichment on the "no new chunks" path.

ADR 011 v2 says enrichment watches the existing concept graph, not new
chunks. So calling `Pipeline.sync()` with enrichment enabled must fire
the runner even when the provider has nothing new to ingest.

This was found by the acceptance test `test_enrichment_at.py` —
moved here as a focused pipeline-level unit test so the failure mode
(enrichment summary missing from `sync()` result) is captured close to
the code under test.
"""

from __future__ import annotations

import pytest

from extended_thinking.algorithms.enrichment.protocol import (
    Candidate,
    GateVerdict,
)
from extended_thinking.algorithms.enrichment.triggers.frequency_threshold import (
    FrequencyThresholdTrigger,
)
from extended_thinking.algorithms.enrichment.cache.time_to_refresh import (
    TimeToRefreshCache,
)
from extended_thinking.algorithms.protocol import AlgorithmMeta
from extended_thinking.config.schema import EnrichmentConfig
from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.storage import StorageLayer


class _EmptyProvider:
    name = "empty"
    extract_concepts = False
    def search(self, q, limit=20): return []
    def get_recent(self, since=None, limit=50): return []
    def get_entities(self): return []
    def store_insight(self, *a, **k): return ""
    def get_insights(self): return []
    def get_stats(self): return {"total_memories": 0, "last_updated": ""}
    def get_knowledge_graph(self): return None


class _StubSource:
    meta = AlgorithmMeta(
        name="wikipedia", family="enrichment.sources",
        description="stub", paper_citation="n/a",
    )
    def source_kind(self): return "wikipedia"
    def search(self, **kw):
        return [Candidate(
            external_id="Q_stub", title="T", abstract="A",
            url="https://x/", themes=["t"], source_kind="wikipedia",
        )]


class _AcceptGate:
    meta = AlgorithmMeta(
        name="stub_accept", family="enrichment.relevance_gates",
        description="", paper_citation="",
    )
    def judge(self, **kw):
        return GateVerdict(outcome="accept", score=0.8)


@pytest.fixture
def enrichment_pipeline(tmp_path, monkeypatch):
    import extended_thinking.algorithms as algos
    import extended_thinking.config as config_module
    from extended_thinking._schema import models as m

    monkeypatch.setattr(
        config_module.settings,
        "enrichment",
        EnrichmentConfig(enabled=True, concept_namespace="memory"),
    )

    real_get_active = algos.get_active
    def _fake(family, config=None):
        if family == "enrichment.sources":
            return [_StubSource()]
        if family == "enrichment.relevance_gates":
            return [_AcceptGate()]
        if family == "enrichment.triggers":
            return [FrequencyThresholdTrigger(min_frequency=3)]
        if family == "enrichment.cache":
            return [TimeToRefreshCache()]
        return real_get_active(family, config)
    monkeypatch.setattr(algos, "get_active", _fake)

    storage = StorageLayer.default(tmp_path / "e")
    pipe = Pipeline.from_storage(_EmptyProvider(), storage)

    # Seed a concept that crosses the frequency threshold
    pipe.store.insert(
        m.Concept(id="c-1", name="sparse attention",
                  category=m.ConceptCategory.topic,
                  description="desc", frequency=10),
        namespace="memory", source="seed",
    )
    return pipe


@pytest.mark.asyncio
async def test_sync_fires_enrichment_when_no_new_chunks(enrichment_pipeline):
    """Regression: the 'no new data' early return used to skip enrichment
    entirely. With existing concepts above threshold + enabled config,
    enrichment must still run."""
    result = await enrichment_pipeline.sync()

    assert "enrichment" in result, (
        f"enrichment summary missing from sync result; got keys={list(result.keys())}"
    )
    summary = result["enrichment"]
    assert summary["triggers_fired"] >= 1
    assert summary["knowledge_nodes_created"] >= 1
    assert summary["edges_created"] >= 1


@pytest.mark.asyncio
async def test_direct_runner_call_returns_summary(enrichment_pipeline):
    """Belt-and-braces: the runner hook itself returns a populated
    summary, independent of the sync() wrapper."""
    summary = enrichment_pipeline._run_enrichment_if_enabled()
    assert summary is not None, (
        "enrichment summary None — check settings.enrichment.enabled and "
        "get_active() stubs"
    )
    assert summary.triggers_fired >= 1
    assert summary.knowledge_nodes_created >= 1
