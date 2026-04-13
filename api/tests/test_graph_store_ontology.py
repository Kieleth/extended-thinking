"""ADR 013 C1 end-to-end: ontology-driven GraphStore + typed insert path.

Covers the integration between:
  - schema/extended_thinking.yaml (LinkML, imports malleus)
  - schema/generated/kuzu_ddl.py + kuzu_types.py (codegen output)
  - storage/ontology.py (Ontology abstraction)
  - storage/graph_store.py (GraphStore.insert dispatcher, legacy methods)

These tests lock in the Architecture A guarantees: the ontology is
constitutive, the GraphStore cannot be constructed without one, Kuzu
enforces FROM/TO at the binder, bitemporal + namespace columns are
inherited by every typed row, and the legacy memory-pipeline methods
coexist with the typed insert path against the same tables.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from extended_thinking.storage.graph_store import GraphStore
from extended_thinking.storage.ontology import Ontology, default_ontology
from extended_thinking._schema import models as m


# ── Ontology abstraction ──────────────────────────────────────────────

class TestOntology:

    def test_default_loads(self):
        ont = default_ontology()
        assert ont.name  # non-empty
        assert ont.ddl
        assert "Concept" in ont.node_tables
        assert "Wisdom" in ont.node_tables
        assert "RelatesTo" in ont.edge_tables

    def test_from_module_reads_generated_fields(self):
        from extended_thinking._schema import kuzu_ddl
        ont = Ontology.from_module(kuzu_ddl, name="et")
        assert ont.name == "et"
        assert ont.node_tables == kuzu_ddl.NODE_TABLES
        assert ont.edge_tables == kuzu_ddl.EDGE_TABLES

    def test_column_renames_exposed(self):
        """description → desc_text must surface via the Ontology's renames map."""
        ont = default_ontology()
        assert "Concept" in ont.column_renames
        assert ont.column_renames["Concept"]["description"] == "desc_text"

    def test_merged_with_unions_tables_and_ddl(self):
        ont = default_ontology()
        other = Ontology(
            name="consumer",
            ddl=["CREATE NODE TABLE IF NOT EXISTS Hypothesis(id STRING, PRIMARY KEY(id))"],
            node_tables=["Hypothesis"],
            edge_tables=[],
            column_renames={},
        )
        merged = ont.merged_with(other)
        assert "Hypothesis" in merged.node_tables
        assert "Concept" in merged.node_tables
        assert len(merged.ddl) == len(ont.ddl) + 1
        assert "+consumer" in merged.name
        assert ont.name in merged.name

    def test_merged_rename_collision_raises(self):
        """Two ontologies renaming the same slot of the same table differently
        must raise; consumers need to know they conflict."""
        a = Ontology(
            name="a", ddl=[], node_tables=[], edge_tables=[],
            column_renames={"Concept": {"foo": "bar"}},
        )
        b = Ontology(
            name="b", ddl=[], node_tables=[], edge_tables=[],
            column_renames={"Concept": {"foo": "baz"}},
        )
        with pytest.raises(ValueError, match="rename collision"):
            a.merged_with(b)


# ── GraphStore construction ───────────────────────────────────────────

class TestGraphStoreConstruction:

    def test_default_ontology_applied(self):
        """Every declared table must exist after GraphStore is constructed
        (idempotent because the DDL uses IF NOT EXISTS)."""
        with tempfile.TemporaryDirectory() as tmp:
            kg = GraphStore(Path(tmp) / "kg")
            # Every declared node table must be addressable
            for t in kg._ontology.node_tables:
                kg._conn.execute(f"MATCH (n:{t}) RETURN count(n)")
            for t in kg._ontology.edge_tables:
                kg._conn.execute(f"MATCH ()-[r:{t}]->() RETURN count(r)")

    def test_custom_ontology_accepted(self):
        """Caller can pass their own Ontology — the constitutive lever
        consumer projects (autoresearch-ET) will use."""
        custom = default_ontology()
        custom.name = "custom"
        with tempfile.TemporaryDirectory() as tmp:
            kg = GraphStore(Path(tmp) / "kg", ontology=custom)
            assert kg._ontology.name == "custom"

    def test_re_open_idempotent(self):
        """Constructing GraphStore twice against the same path must not
        duplicate or corrupt tables — IF NOT EXISTS semantics."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kg"
            GraphStore(path)
            # Second open should not raise
            GraphStore(path)


# ── Typed insert: nodes ───────────────────────────────────────────────

class TestTypedNodeInsert:

    @pytest.fixture
    def kg(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield GraphStore(Path(tmp) / "kg")

    def test_insert_concept_round_trips(self, kg):
        c = m.Concept(
            id="c1", name="sparse-attention",
            category=m.ConceptCategory.topic,
            description="a technique",
            frequency=3,
        )
        kg.insert(c, namespace="research", source="autoresearch-et")

        # Read back via legacy get_concept
        got = kg.get_concept("c1")
        assert got is not None
        assert got["name"] == "sparse-attention"
        assert got["description"] == "a technique"

    def test_inserted_node_has_namespace_and_source(self, kg):
        c = m.Concept(id="c1", name="x", category=m.ConceptCategory.topic)
        kg.insert(c, namespace="research", source="autoresearch-et")
        row = kg._query_one(
            "MATCH (c:Concept {id: 'c1'}) RETURN c.namespace, c.et_source"
        )
        assert row == ["research", "autoresearch-et"]

    def test_inserted_node_has_bitemporal_fields(self, kg):
        c = m.Concept(id="c1", name="x", category=m.ConceptCategory.topic)
        kg.insert(c)
        row = kg._query_one(
            "MATCH (c:Concept {id: 'c1'}) "
            "RETURN c.t_valid_from, c.t_created, c.t_expired, c.vectors_pending"
        )
        assert row[0]      # t_valid_from non-empty
        assert row[1]      # t_created non-empty
        assert row[2] == ""  # t_expired empty at insert
        assert row[3] is True  # vectors_pending marker set

    def test_unregistered_type_rejected(self, kg):
        class Stray:
            pass
        with pytest.raises(ValueError, match="not in the ontology"):
            kg.insert(Stray())


# ── Typed insert: edges + FROM/TO enforcement ─────────────────────────

class TestTypedEdgeInsert:

    @pytest.fixture
    def kg(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield GraphStore(Path(tmp) / "kg")

    def _seed(self, kg):
        for cid, nm in (("c1", "alpha"), ("c2", "beta")):
            kg.insert(m.Concept(id=cid, name=nm, category=m.ConceptCategory.topic))
        kg.insert(m.Wisdom(
            id="w1", name="w", title="test wisdom",
            wisdom_type=m.WisdomType.wisdom,
            signal_type="wisdom_card", bearer_id="c1",
        ))
        kg.insert(m.Chunk(id="ch1", name="ch1"))

    def test_insert_concept_concept_edge(self, kg):
        self._seed(kg)
        e = m.RelatesTo(id="e1", source_id="c1", target_id="c2",
                        relation_type="semantic", weight=0.9)
        eid = kg.insert(e)
        assert eid == "e1"
        row = kg._query_one(
            "MATCH (a:Concept {id:'c1'})-[r:RelatesTo]->(b:Concept {id:'c2'}) "
            "RETURN r.weight, r.relation_type"
        )
        assert row == [0.9, "semantic"]

    def test_insert_wisdom_concept_edge(self, kg):
        self._seed(kg)
        e = m.InformedBy(id="i1", source_id="w1", target_id="c1",
                         relation_type="informed_by")
        kg.insert(e)
        row = kg._query_one(
            "MATCH (w:Wisdom {id:'w1'})-[r:InformedBy]->(c:Concept {id:'c1'}) "
            "RETURN r.id"
        )
        assert row == ["i1"]

    def test_insert_concept_chunk_edge(self, kg):
        self._seed(kg)
        e = m.HasProvenance(
            id="p1", source_id="c1", target_id="ch1",
            relation_type="has_provenance",
            source_provider="unit-test", llm_model="haiku",
        )
        kg.insert(e)
        row = kg._query_one(
            "MATCH (c:Concept {id:'c1'})-[r:HasProvenance]->(ch:Chunk {id:'ch1'}) "
            "RETURN r.source_provider, r.llm_model"
        )
        assert row == ["unit-test", "haiku"]

    def test_wrong_domain_rejected_by_kuzu_binder(self, kg):
        """RelatesTo is Concept→Concept. Wisdom→Concept via RelatesTo must
        fail at the binder — this is the Architecture A guarantee."""
        self._seed(kg)
        with pytest.raises(Exception) as exc:
            e = m.RelatesTo(id="bad", source_id="w1", target_id="c1",
                            relation_type="semantic", weight=1.0)
            kg.insert(e)
        # Kuzu's error mentions the label constraint
        assert "Concept" in str(exc.value) or "Expected" in str(exc.value)

    def test_missing_source_id_raises(self, kg):
        self._seed(kg)
        e = m.RelatesTo(id="bad", source_id="nonexistent", target_id="c1",
                        relation_type="semantic")
        with pytest.raises(ValueError, match="source node not found"):
            kg.insert(e)


# ── Legacy and typed paths coexist ────────────────────────────────────

class TestLegacyAndTypedCoexistence:
    """Legacy domain methods (add_concept, add_wisdom, …) and the typed
    insert path write to the same ontology-driven tables. A consumer
    mixing both must see a coherent graph."""

    @pytest.fixture
    def kg(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield GraphStore(Path(tmp) / "kg")

    def test_legacy_add_concept_visible_through_typed_query(self, kg):
        kg.add_concept("legacy-1", "legacy concept", "topic", "described")
        # Pull it through a plain MATCH — same table as typed inserts
        row = kg._query_one(
            "MATCH (c:Concept {id: 'legacy-1'}) "
            "RETURN c.name, c.category, c.desc_text"
        )
        assert row == ["legacy concept", "topic", "described"]

    def test_typed_insert_visible_through_legacy_get_concept(self, kg):
        c = m.Concept(
            id="typed-1", name="typed concept",
            category=m.ConceptCategory.theme,
            description="typed desc",
        )
        kg.insert(c)
        got = kg.get_concept("typed-1")
        assert got is not None
        assert got["name"] == "typed concept"
        assert got["description"] == "typed desc"
        assert got["category"] == "theme"

    def test_legacy_and_typed_mix_in_same_query(self, kg):
        kg.add_concept("legacy", "A", "topic", "x")
        kg.insert(m.Concept(id="typed", name="B",
                            category=m.ConceptCategory.topic))
        row = kg._query_one("MATCH (c:Concept) RETURN count(c)")
        assert row[0] == 2


# ── Consumer composition: ET + a small fake consumer ontology ─────────

class TestOntologyComposition:
    """The autoresearch-ET-style flow: a consumer ships its own ontology
    and merges it onto ET's base via `Ontology.merged_with`."""

    def test_merged_ontology_creates_consumer_tables(self):
        ont = default_ontology()
        consumer_ddl = [
            "CREATE NODE TABLE IF NOT EXISTS FakeConsumerNode("
            "id STRING, name STRING, namespace STRING, "
            "t_valid_from STRING, t_valid_to STRING, "
            "t_created STRING, t_expired STRING, t_superseded_by STRING, "
            "et_source STRING, vectors_pending BOOL, PRIMARY KEY(id))"
        ]
        consumer = Ontology(
            name="fake-consumer", ddl=consumer_ddl,
            node_tables=["FakeConsumerNode"], edge_tables=[],
            column_renames={},
        )
        merged = ont.merged_with(consumer)

        with tempfile.TemporaryDirectory() as tmp:
            kg = GraphStore(Path(tmp) / "kg", ontology=merged)
            # ET tables
            kg._conn.execute("MATCH (c:Concept) RETURN count(c)")
            # Consumer tables
            kg._conn.execute("MATCH (n:FakeConsumerNode) RETURN count(n)")
