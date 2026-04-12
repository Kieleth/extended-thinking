"""ADR 013 C2: namespace isolation.

Every node and edge carries a `namespace` string property. Queries and
algorithms scope to one namespace unless explicitly told otherwise.
Legacy memory-pipeline writes land in `"memory"`; programmatic consumers
pass their own namespace (e.g. autoresearch-ET's `"default"`).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from extended_thinking.algorithms import AlgorithmContext, get_by_name
from extended_thinking.storage.graph_store import GraphStore
from schema.generated import models as m


@pytest.fixture
def kg():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "kg")


# ── Default namespaces by write path ──────────────────────────────────

class TestDefaultNamespaces:

    def test_legacy_add_concept_lands_in_memory(self, kg):
        """Memory-pipeline writes default to the "memory" namespace so they
        stay separate from programmatic-consumer data."""
        kg.add_concept("c1", "alpha", "topic", "first")
        row = kg._query_one(
            "MATCH (c:Concept {id: 'c1'}) RETURN c.namespace"
        )
        assert row == ["memory"]

    def test_typed_insert_defaults_to_default_namespace(self, kg):
        """autoresearch-ET's ETClient defaults to `"default"`; so does
        GraphStore.insert, matching the ADR 013 contract."""
        c = m.Concept(id="c1", name="alpha", category=m.ConceptCategory.topic)
        kg.insert(c)
        row = kg._query_one(
            "MATCH (c:Concept {id: 'c1'}) RETURN c.namespace"
        )
        assert row == ["default"]

    def test_typed_insert_honors_explicit_namespace(self, kg):
        c = m.Concept(id="c1", name="alpha", category=m.ConceptCategory.topic)
        kg.insert(c, namespace="research")
        row = kg._query_one(
            "MATCH (c:Concept {id: 'c1'}) RETURN c.namespace"
        )
        assert row == ["research"]

    def test_legacy_add_wisdom_lands_in_memory(self, kg):
        kg.add_wisdom("title", "desc", wisdom_type="wisdom")
        row = kg._query_one("MATCH (w:Wisdom) RETURN w.namespace LIMIT 1")
        assert row == ["memory"]

    def test_legacy_add_relationship_lands_in_memory(self, kg):
        kg.add_concept("a", "A", "topic", "")
        kg.add_concept("b", "B", "topic", "")
        kg.add_relationship("a", "b", weight=1.0)
        row = kg._query_one(
            "MATCH ()-[r:RelatesTo]->() RETURN r.namespace LIMIT 1"
        )
        assert row == ["memory"]

    def test_legacy_mark_chunk_lands_in_memory(self, kg):
        kg.mark_chunk_processed("ch1", source="test.md", source_type="note")
        row = kg._query_one("MATCH (c:Chunk {id: 'ch1'}) RETURN c.namespace")
        assert row == ["memory"]


# ── Query-level scoping ───────────────────────────────────────────────

class TestQueryScoping:

    def _seed_multi_namespace(self, kg):
        """Populate two namespaces so scope filters have something to bite."""
        # Memory side
        kg.add_concept("mem-1", "memory alpha", "topic", "x")
        kg.add_concept("mem-2", "memory beta", "topic", "y")
        # Research side
        kg.insert(m.Concept(id="res-1", name="research alpha",
                            category=m.ConceptCategory.topic),
                  namespace="research")
        kg.insert(m.Concept(id="res-2", name="research beta",
                            category=m.ConceptCategory.topic),
                  namespace="research")

    def test_list_concepts_without_namespace_spans_all(self, kg):
        self._seed_multi_namespace(kg)
        got = kg.list_concepts(limit=10)
        assert len(got) == 4

    def test_list_concepts_scoped_to_memory(self, kg):
        self._seed_multi_namespace(kg)
        got = kg.list_concepts(limit=10, namespace="memory")
        ids = {c["id"] for c in got}
        assert ids == {"mem-1", "mem-2"}

    def test_list_concepts_scoped_to_research(self, kg):
        self._seed_multi_namespace(kg)
        got = kg.list_concepts(limit=10, namespace="research")
        ids = {c["id"] for c in got}
        assert ids == {"res-1", "res-2"}

    def test_list_concepts_unknown_namespace_empty(self, kg):
        self._seed_multi_namespace(kg)
        assert kg.list_concepts(limit=10, namespace="does-not-exist") == []

    def test_get_concept_namespace_filter(self, kg):
        self._seed_multi_namespace(kg)
        # mem-1 belongs to memory; asking for it scoped to research returns None
        assert kg.get_concept("mem-1", namespace="research") is None
        assert kg.get_concept("mem-1", namespace="memory") is not None

    def test_get_stats_scoped(self, kg):
        self._seed_multi_namespace(kg)
        total = kg.get_stats()
        mem = kg.get_stats(namespace="memory")
        res = kg.get_stats(namespace="research")
        assert total["total_concepts"] == 4
        assert mem["total_concepts"] == 2
        assert res["total_concepts"] == 2


# ── Wisdom scoping ────────────────────────────────────────────────────

class TestWisdomScoping:

    def test_list_wisdoms_scoped(self, kg):
        kg.add_wisdom("memory wisdom", "d")  # default memory namespace
        # Programmatic wisdom goes via insert; namespace="research"
        w = m.Wisdom(
            id="w-research", name="w-research", title="research wisdom",
            wisdom_type=m.WisdomType.wisdom,
            signal_type="wisdom_card", bearer_id="x",
        )
        kg.insert(w, namespace="research")

        mem = kg.list_wisdoms(namespace="memory")
        res = kg.list_wisdoms(namespace="research")
        all_ = kg.list_wisdoms()

        assert len(mem) == 1
        assert mem[0]["title"] == "memory wisdom"
        assert len(res) == 1
        assert res[0]["id"] == "w-research"
        assert len(all_) == 2


# ── AlgorithmContext wiring ───────────────────────────────────────────

class TestAlgorithmContext:

    def test_context_has_namespace_field(self):
        ctx = AlgorithmContext(kg=None, namespace="research")
        assert ctx.namespace == "research"

    def test_context_defaults_namespace_to_none(self):
        ctx = AlgorithmContext(kg=None)
        assert ctx.namespace is None

    def test_recency_weighted_honors_namespace(self, kg):
        """The activity_score plugin must scope to the caller's namespace
        when the context provides one, so memory ranking and research
        ranking stay isolated."""
        # Memory concepts with edges
        kg.add_concept("m-a", "A", "topic", "")
        kg.add_concept("m-b", "B", "topic", "")
        kg.add_relationship("m-a", "m-b", weight=2.0)
        # Research concepts
        kg.insert(m.Concept(id="r-a", name="A", category=m.ConceptCategory.topic),
                  namespace="research")
        kg.insert(m.Concept(id="r-b", name="B", category=m.ConceptCategory.topic),
                  namespace="research")

        alg = get_by_name("recency_weighted")

        mem_ctx = AlgorithmContext(kg=kg, namespace="memory", params={"top_k": 10})
        res_ctx = AlgorithmContext(kg=kg, namespace="research", params={"top_k": 10})

        mem_result = {c["id"] for c in alg.run(mem_ctx)}
        res_result = {c["id"] for c in alg.run(res_ctx)}

        assert mem_result == {"m-a", "m-b"}
        assert res_result == {"r-a", "r-b"}


# ── Edge-level namespace ──────────────────────────────────────────────

class TestEdgeNamespace:

    def test_typed_edge_carries_namespace(self, kg):
        kg.insert(m.Concept(id="c1", name="a", category=m.ConceptCategory.topic),
                  namespace="research")
        kg.insert(m.Concept(id="c2", name="b", category=m.ConceptCategory.topic),
                  namespace="research")
        e = m.RelatesTo(id="e1", source_id="c1", target_id="c2",
                        relation_type="semantic", weight=0.9)
        kg.insert(e, namespace="research")
        row = kg._query_one(
            "MATCH ()-[r:RelatesTo {id: 'e1'}]->() RETURN r.namespace"
        )
        assert row == ["research"]
