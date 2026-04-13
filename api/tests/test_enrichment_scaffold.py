"""ADR 011 v2 Phase A.2/A.3: enrichment plugin scaffold + config + runner.

Tests use stub source/trigger/gate plugins — the MVP Wikipedia source
lands in Phase B.1 as its own test. This file locks the orchestration
shape: correct dispatch order, gate short-circuit behavior, telemetry
writes, and the `[enrichment] enabled = false` → silent no-op contract.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from extended_thinking.algorithms.enrichment.protocol import (
    Candidate,
    EnrichmentGatePlugin,
    EnrichmentSourcePlugin,
    EnrichmentTriggerPlugin,
    GateVerdict,
)
from extended_thinking.algorithms.enrichment.runner import run_enrichment
from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta
from extended_thinking.config.schema import EnrichmentConfig, Settings
from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def kg():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "kg")


# ── Stubs ─────────────────────────────────────────────────────────────

class _StubTrigger:
    meta = AlgorithmMeta(
        name="stub_trigger",
        family="enrichment.triggers",
        description="fires for every concept listed in params['ids']",
        paper_citation="n/a",
    )
    def fired_concepts(self, context):
        ids = context.params.get("ids", [])
        return [(cid, "stub") for cid in ids]


class _StubSource:
    """Returns N fake candidates per concept based on params['per_concept']."""
    meta = AlgorithmMeta(
        name="stub_source",
        family="enrichment.sources",
        description="fake fetcher for tests",
        paper_citation="n/a",
    )
    def __init__(self, kind: str = "stub", per_concept: int = 2):
        self._kind = kind
        self._per_concept = per_concept
    def source_kind(self):
        return self._kind
    def search(self, *, concept_id, concept_name, concept_description, context):
        return [
            Candidate(
                external_id=f"{concept_id}-{i}",
                title=f"article about {concept_name}",
                abstract=f"a {self._kind} summary mentioning {concept_name}",
                url=f"https://example.test/{self._kind}/{concept_id}-{i}",
                themes=["test.theme"],
                source_kind=self._kind,
            )
            for i in range(self._per_concept)
        ]


class _AcceptAllGate:
    meta = AlgorithmMeta(
        name="accept_all",
        family="enrichment.relevance_gates",
        description="accepts everything (test use only)",
        paper_citation="n/a",
    )
    def judge(self, *, concept, candidate, context):
        return GateVerdict(outcome="accept", score=1.0, reason="stub-accept")


class _RejectAllGate:
    meta = AlgorithmMeta(
        name="reject_all",
        family="enrichment.relevance_gates",
        description="rejects everything",
        paper_citation="n/a",
    )
    def judge(self, *, concept, candidate, context):
        return GateVerdict(outcome="reject", score=0.0, reason="stub-reject")


class _RaisingSource:
    meta = AlgorithmMeta(
        name="raising_source",
        family="enrichment.sources",
        description="raises on fetch",
        paper_citation="n/a",
    )
    def source_kind(self):
        return "raising"
    def search(self, **kwargs):
        raise RuntimeError("simulated fetch failure")


# ── Protocol registration ────────────────────────────────────────────

class TestProtocol:

    def test_candidate_shape(self):
        c = Candidate(
            external_id="x",
            title="T",
            abstract="A",
            url="u",
            themes=["a", "b"],
            source_kind="stub",
        )
        assert c.external_id == "x"
        assert c.themes == ["a", "b"]

    def test_verdict_outcomes(self):
        for outcome in ("accept", "auto_accept", "reject"):
            v = GateVerdict(outcome=outcome, score=0.5)
            assert v.outcome == outcome

    def test_sub_packages_exist(self):
        """Every sub-family has its own package so codegen/docs can list them."""
        import importlib
        for sub in ("sources", "triggers", "relevance_gates", "cache"):
            importlib.import_module(
                f"extended_thinking.algorithms.enrichment.{sub}",
            )


# ── Runner happy path ────────────────────────────────────────────────

class TestRunnerHappyPath:

    def _seed_concepts(self, kg):
        kg.add_concept("c1", "sparse attention", "topic",
                       "an efficient attention technique")
        kg.add_concept("c2", "graph neural network", "topic", "")

    def test_accepts_write_knowledge_nodes_and_edges(self, kg):
        self._seed_concepts(kg)
        summary = run_enrichment(
            kg=kg,
            sources=[_StubSource(per_concept=2)],
            triggers=[_StubTrigger()],
            gates=[_AcceptAllGate()],
            cache=None,
            concept_namespace="memory",
            context_overrides={"ids": ["c1", "c2"]},
        )
        # 2 concepts * 2 candidates * 1 source = 4 enrichments
        assert summary.knowledge_nodes_created == 4
        assert summary.edges_created == 4
        assert summary.triggers_fired == 2
        assert summary.candidates_returned == 4
        assert summary.candidates_accepted == 4

        # Verify KnowledgeNodes landed in the per-source namespace
        rows = kg._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'enrichment:stub' "
            "RETURN k.id"
        )
        assert len(rows) == 4

        # Verify Enriches edges
        rows = kg._query_all(
            "MATCH (c:Concept)-[r:Enriches]->(k:KnowledgeNode) "
            "RETURN count(r)"
        )
        assert rows[0][0] == 4

    def test_telemetry_written(self, kg):
        self._seed_concepts(kg)
        summary = run_enrichment(
            kg=kg,
            sources=[_StubSource(per_concept=3)],
            triggers=[_StubTrigger()],
            gates=[_AcceptAllGate()],
            cache=None,
            concept_namespace="memory",
            context_overrides={"ids": ["c1"]},
        )
        # One trigger * one source * one concept = one EnrichmentRun.
        assert summary.runs_recorded == 1
        rows = kg._query_all(
            "MATCH (r:EnrichmentRun) "
            "RETURN r.trigger_name, r.source_kind, r.concept_id, "
            "r.candidates_returned, r.candidates_accepted"
        )
        assert len(rows) == 1
        assert rows[0][0] == "stub_trigger"
        assert rows[0][1] == "stub"
        assert rows[0][2] == "c1"
        assert rows[0][3] == 3
        assert rows[0][4] == 3

    def test_multiple_sources_multiple_namespaces(self, kg):
        """Per-source namespace isolation (ADR 011 v2 key decision)."""
        self._seed_concepts(kg)
        summary = run_enrichment(
            kg=kg,
            sources=[_StubSource(kind="a"), _StubSource(kind="b")],
            triggers=[_StubTrigger()],
            gates=[_AcceptAllGate()],
            cache=None,
            concept_namespace="memory",
            context_overrides={"ids": ["c1"]},
        )
        assert summary.runs_recorded == 2  # one per (trigger, source, concept)

        ns_a = kg._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'enrichment:a' "
            "RETURN count(k)"
        )[0][0]
        ns_b = kg._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'enrichment:b' "
            "RETURN count(k)"
        )[0][0]
        assert ns_a == 2
        assert ns_b == 2


# ── Gate dispatch ────────────────────────────────────────────────────

class TestGateDispatch:

    def test_reject_short_circuits(self, kg):
        kg.add_concept("c1", "x", "topic", "")
        summary = run_enrichment(
            kg=kg,
            sources=[_StubSource(per_concept=2)],
            triggers=[_StubTrigger()],
            gates=[_RejectAllGate(), _AcceptAllGate()],  # reject first
            cache=None,
            concept_namespace="memory",
            context_overrides={"ids": ["c1"]},
        )
        assert summary.candidates_returned == 2
        assert summary.candidates_accepted == 0
        assert summary.knowledge_nodes_created == 0

    def test_accept_after_gate_chain(self, kg):
        kg.add_concept("c1", "x", "topic", "")
        # Both gates accept → commit
        summary = run_enrichment(
            kg=kg,
            sources=[_StubSource(per_concept=1)],
            triggers=[_StubTrigger()],
            gates=[_AcceptAllGate(), _AcceptAllGate()],
            cache=None,
            concept_namespace="memory",
            context_overrides={"ids": ["c1"]},
        )
        assert summary.candidates_accepted == 1


# ── Error paths ──────────────────────────────────────────────────────

class TestErrorPaths:

    def test_source_failure_captured_as_telemetry(self, kg):
        kg.add_concept("c1", "x", "topic", "")
        summary = run_enrichment(
            kg=kg,
            sources=[_RaisingSource()],
            triggers=[_StubTrigger()],
            gates=[_AcceptAllGate()],
            cache=None,
            concept_namespace="memory",
            context_overrides={"ids": ["c1"]},
        )
        assert summary.candidates_accepted == 0
        # Telemetry row must exist with a non-empty error
        rows = kg._query_all(
            "MATCH (r:EnrichmentRun) RETURN r.error"
        )
        assert len(rows) == 1
        assert "simulated fetch failure" in rows[0][0]

    def test_empty_trigger_result_no_writes(self, kg):
        kg.add_concept("c1", "x", "topic", "")
        # Trigger with empty ids → nothing fires, no runs recorded
        summary = run_enrichment(
            kg=kg,
            sources=[_StubSource()],
            triggers=[_StubTrigger()],
            gates=[_AcceptAllGate()],
            cache=None,
            concept_namespace="memory",
            context_overrides={"ids": []},
        )
        assert summary.triggers_fired == 0
        assert summary.runs_recorded == 0

    def test_wrong_family_plugin_rejected_loudly(self, kg):
        """A plugin registered under the wrong family crashes with a
        useful message rather than a cryptic downstream error."""
        class _NotATrigger:
            meta = AlgorithmMeta(
                name="not_a_trigger",
                family="enrichment.sources",  # wrong family
                description="", paper_citation="",
            )
        with pytest.raises(TypeError, match="expected 'enrichment.triggers'"):
            run_enrichment(
                kg=kg,
                sources=[_StubSource()],
                triggers=[_NotATrigger()],  # type: ignore[list-item]
                gates=[_AcceptAllGate()],
                cache=None,
            )


# ── Config schema ────────────────────────────────────────────────────

class TestEnrichmentConfig:

    def test_defaults_to_disabled(self):
        s = Settings()
        assert s.enrichment.enabled is False
        assert s.enrichment.concept_namespace == "memory"
        assert s.enrichment.max_runs_per_sync == 100

    def test_enabled_toggle(self):
        s = Settings(enrichment=EnrichmentConfig(enabled=True))
        assert s.enrichment.enabled is True

    def test_disabled_by_default_is_internal_only(self):
        """The master toggle default is False — nothing leaves the machine
        until a user explicitly opts in. ADR 011 v2 'internal-only' UX
        mode is the shipped default."""
        s = Settings()
        assert s.enrichment.enabled is False

    def test_forbids_unknown_fields(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EnrichmentConfig(enabled=True, nonsense_field="x")


# ── Pipeline gating ──────────────────────────────────────────────────

class TestPipelineGating:
    """Pipeline.sync() invokes enrichment only when [enrichment] enabled.
    This test talks to the Pipeline directly to verify the gate."""

    @pytest.mark.asyncio
    async def test_disabled_is_silent_noop(self, tmp_path, monkeypatch):
        """Master toggle off → the runner is not invoked, no EnrichmentRun
        nodes exist, sync result carries no 'enrichment' key."""
        from extended_thinking.processing.pipeline_v2 import Pipeline
        from extended_thinking.providers import get_provider
        from extended_thinking.storage import StorageLayer

        storage = StorageLayer.default(tmp_path / "d")
        pipe = Pipeline.from_storage(get_provider(), storage)

        # default settings: enrichment disabled
        result = await pipe.sync()
        assert "enrichment" not in result

        rows = storage.kg._query_all("MATCH (r:EnrichmentRun) RETURN count(r)")
        assert rows[0][0] == 0
