"""ADR 011 v2 Phase A: KnowledgeNode + Enriches + EnrichmentRun ontology.

Enrichment is a consumer of ADR 013's shipped typed-write path. These
tests verify the new types are registered, respect the per-source
namespace convention, and enforce FROM/TO via Kuzu's binder — exactly
like every other typed class.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.storage.graph_store import GraphStore
from schema.generated import models as m


@pytest.fixture
def kg():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "kg")


# ── Ontology registration ─────────────────────────────────────────────

class TestRegistration:

    def test_knowledge_node_registered(self, kg):
        assert "KnowledgeNode" in kg._ontology.node_tables

    def test_enriches_registered(self, kg):
        assert "Enriches" in kg._ontology.edge_tables

    def test_wisdom_enriches_registered(self, kg):
        """Multi-pair ADR 011 v2 — Wisdom gets its own Enriches subclass."""
        assert "WisdomEnriches" in kg._ontology.edge_tables

    def test_enrichment_run_registered(self, kg):
        """Telemetry node type available as a first-class Event."""
        assert "EnrichmentRun" in kg._ontology.node_tables


# ── KnowledgeNode writes ──────────────────────────────────────────────

class TestKnowledgeNodeWrites:

    def test_basic_write(self, kg):
        kn = m.KnowledgeNode(
            id="kn-wiki-Q89",
            name="Sparse attention",
            description="Wikipedia summary",
            source_kind="wikipedia",
            external_id="Q89",
            url="https://en.wikipedia.org/wiki/Sparse_attention",
            title="Sparse attention",
            abstract="An efficiency technique for transformer attention...",
            theme=json.dumps(["cs.ai", "cs.ml"]),
            # Malleus Signal requirements:
            signal_type="wikipedia",
            bearer_id="c-sparse-attention",  # user concept being enriched
        )
        kg.insert(kn, namespace="enrichment:wikipedia", source="wikipedia-plugin")

        row = kg._query_one(
            "MATCH (k:KnowledgeNode {id: 'kn-wiki-Q89'}) "
            "RETURN k.source_kind, k.external_id, k.theme, k.bearer_id, k.namespace"
        )
        assert row[0] == "wikipedia"
        assert row[1] == "Q89"
        assert set(json.loads(row[2])) == {"cs.ai", "cs.ml"}
        assert row[3] == "c-sparse-attention"
        assert row[4] == "enrichment:wikipedia"

    def test_per_source_namespace_convention(self, kg):
        """Per-source namespaces — Wikipedia vs arXiv live in separate
        namespaces so purge is a namespace-scoped op, not a property scan."""
        for source, nid in (("wikipedia", "kn-wiki-1"), ("arxiv", "kn-ax-1")):
            kn = m.KnowledgeNode(
                id=nid, name=f"test-{source}",
                description="",
                source_kind=source, external_id=nid,
                title=f"test {source}", abstract="",
                theme=json.dumps([]),
                signal_type=source, bearer_id="c-1",
            )
            kg.insert(kn, namespace=f"enrichment:{source}")

        wiki = kg._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'enrichment:wikipedia' "
            "RETURN k.id"
        )
        arxiv = kg._query_all(
            "MATCH (k:KnowledgeNode) WHERE k.namespace = 'enrichment:arxiv' "
            "RETURN k.id"
        )
        assert {r[0] for r in wiki} == {"kn-wiki-1"}
        assert {r[0] for r in arxiv} == {"kn-ax-1"}


# ── Enriches edge semantics ───────────────────────────────────────────

class TestEnrichesEdge:

    def _seed_kn(self, kg, kn_id="kn-1"):
        kn = m.KnowledgeNode(
            id=kn_id, name="x", description="",
            source_kind="wikipedia", external_id="Q1",
            title="x", abstract="",
            theme=json.dumps([]),
            signal_type="wikipedia", bearer_id="c-1",
        )
        kg.insert(kn, namespace="enrichment:wikipedia")
        return kn_id

    def test_concept_to_knowledge_node_edge(self, kg):
        kg.add_concept("c-1", "user concept", "topic", "")
        kn_id = self._seed_kn(kg)
        e = m.Enriches(
            id="e-1",
            source_id="c-1", target_id=kn_id,
            relation_type="enriches",
            relevance=0.88,
            trigger="frequency_threshold",
            gate_verdicts=json.dumps([{"plugin": "embedding_cosine", "score": 0.91, "outcome": "auto_accept"}]),
        )
        kg.insert(e, namespace="enrichment:wikipedia")
        row = kg._query_one(
            "MATCH (c:Concept {id: 'c-1'})-[r:Enriches]->(k:KnowledgeNode {id: $kn}) "
            "RETURN r.relevance, r.trigger",
            {"kn": kn_id},
        )
        assert row[0] == pytest.approx(0.88)
        assert row[1] == "frequency_threshold"

    def test_wisdom_to_knowledge_node_edge(self, kg):
        """Multi-pair ADR 011 v2: Wisdom can also anchor enrichment.
        Uses WisdomEnriches, not Enriches, because Kuzu requires
        per-REL FROM/TO declarations."""
        kg.add_wisdom("wisdom title", "wisdom body", wisdom_type="wisdom")
        wid = kg._query_one("MATCH (w:Wisdom) RETURN w.id")[0]
        kn_id = self._seed_kn(kg, kn_id="kn-2")
        e = m.WisdomEnriches(
            id="we-1",
            source_id=wid, target_id=kn_id,
            relation_type="enriches",
            relevance=0.75,
            trigger="on_wisdom",
            gate_verdicts=json.dumps([]),
        )
        kg.insert(e, namespace="enrichment:wikipedia")
        row = kg._query_one(
            "MATCH (w:Wisdom)-[r:WisdomEnriches]->(k:KnowledgeNode {id: 'kn-2'}) "
            "RETURN r.relevance"
        )
        assert row[0] == pytest.approx(0.75)

    def test_concept_to_concept_via_enriches_rejected(self, kg):
        """Kuzu's binder enforces Concept→KnowledgeNode — attempting
        Enriches between two Concepts is exactly the shape it rejects."""
        kg.add_concept("c-1", "a", "topic", "")
        kg.add_concept("c-2", "b", "topic", "")
        e = m.Enriches(
            id="e-bad",
            source_id="c-1", target_id="c-2",  # wrong TO type
            relation_type="enriches",
        )
        with pytest.raises(Exception) as exc:
            kg.insert(e)
        # Either the pre-insert type check catches it, or Kuzu's binder
        # does. Both are correct — both mean "ontology said no."
        assert ("not found" in str(exc.value).lower()
                or "Expected" in str(exc.value)
                or "KnowledgeNode" in str(exc.value))


# ── EnrichmentRun telemetry ───────────────────────────────────────────

class TestEnrichmentRunTelemetry:

    def test_record_and_query(self, kg):
        """A single enrichment fire gets captured as an Event. The gate
        trace + candidate counts become queryable so users (or LLMs)
        can tune thresholds based on actual data."""
        run = m.EnrichmentRun(
            id="run-1",
            name="wikipedia enrichment for c-sparse-attention",
            description="",
            trigger_name="frequency_threshold",
            source_kind="wikipedia",
            concept_id="c-sparse-attention",
            candidates_returned=8,
            candidates_accepted=1,
            gate_trace=json.dumps([
                {"gate": "embedding_cosine", "in": 8, "out": 2,
                 "scores": {"min": 0.3, "max": 0.93, "median": 0.54}},
                {"gate": "source_type_match", "in": 2, "out": 1,
                 "rule": "wikipedia:cs.* -> topic"},
            ]),
            duration_ms=842,
            error="",
            event_type="enrichment_run",
        )
        kg.insert(run, namespace="enrichment:wikipedia")

        row = kg._query_one(
            "MATCH (r:EnrichmentRun {id: 'run-1'}) "
            "RETURN r.trigger_name, r.candidates_returned, "
            "r.candidates_accepted, r.duration_ms, r.error"
        )
        assert row == ["frequency_threshold", 8, 1, 842, ""]

    def test_error_path_captured(self, kg):
        """Failed runs (Wikipedia 5xx etc.) are first-class rows, not
        dropped. The error string lets users query for retry candidates."""
        run = m.EnrichmentRun(
            id="run-err",
            name="x",
            description="",
            trigger_name="frequency_threshold",
            source_kind="wikipedia",
            concept_id="c-xyz",
            candidates_returned=0,
            candidates_accepted=0,
            gate_trace=json.dumps([]),
            duration_ms=10,
            error="HTTPError: 503 Service Unavailable",
            event_type="enrichment_run",
        )
        kg.insert(run, namespace="enrichment:wikipedia")
        rows = kg._query_all(
            "MATCH (r:EnrichmentRun) WHERE r.error <> '' "
            "RETURN r.id, r.error"
        )
        assert len(rows) == 1
        assert "503" in rows[0][1]

    def test_bitemporal_diff_surface(self, kg):
        """et_shift scoped to EnrichmentRun must surface just the runs,
        not memory-side concepts. This is what makes stats queryable."""
        from datetime import datetime, timedelta, timezone
        # Seed one run
        kg.insert(m.EnrichmentRun(
            id="run-x", name="", description="",
            trigger_name="frequency_threshold",
            source_kind="wikipedia",
            concept_id="c-y",
            candidates_returned=3,
            candidates_accepted=2,
            gate_trace=json.dumps([]),
            duration_ms=100,
            error="",
            event_type="enrichment_run",
        ), namespace="enrichment:wikipedia")
        # And one memory concept (noise we should NOT see)
        kg.add_concept("c-noise", "memory noise", "topic", "")

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        scoped = kg.diff(
            past, future,
            node_types=["EnrichmentRun"],
            namespace="enrichment:wikipedia",
        )
        ids = {n["id"] for n in scoped["nodes_added"]}
        assert ids == {"run-x"}
        assert "c-noise" not in ids


# ── Extensibility hook ────────────────────────────────────────────────

class TestExtensibility:
    """Consumer projects (autoresearch-ET) extend the FROM side of
    Enriches by declaring additional subclasses in their own LinkML.
    This test documents the pattern — we don't implement a consumer
    schema here, just verify the current multi-pair ships with both
    Concept and Wisdom variants."""

    def test_multiple_enriches_variants_coexist(self, kg):
        from schema.generated import kuzu_types as kt
        enriches_variants = [
            t for t in kt.EDGE_TYPES if "nriches" in t.__name__
        ]
        names = {t.__name__ for t in enriches_variants}
        assert names == {"Enriches", "WisdomEnriches"}
