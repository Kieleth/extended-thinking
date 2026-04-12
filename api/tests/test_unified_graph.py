"""Tests for UnifiedGraph — federated queries across ConceptStore + provider KG."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.providers.protocol import Entity, Fact
from extended_thinking.processing.concept_store import ConceptStore
from extended_thinking.processing.unified_graph import UnifiedGraph


class FakeKGView:
    """Simulates a provider's KG (like mempalace)."""

    def __init__(self):
        self._entities = [
            Entity(name="Silk", entity_type="project", properties={"id": "silk"}),
            Entity(name="Shelob", entity_type="project", properties={"id": "shelob"}),
            Entity(name="outgoing-edges-bug", entity_type="unknown", properties={"id": "outgoing-edges-bug"}),
        ]
        self._facts = [
            Fact(subject="silk", predicate="has_bug", object="outgoing-edges-bug", valid_from="2026-04-10"),
            Fact(subject="shelob", predicate="implemented", object="capability-scope-enum", valid_from="2026-03-15"),
            Fact(subject="shelob", predicate="design_decision", object="unified-provision", valid_from="2026-03-20"),
        ]

    def facts(self, subject=None):
        if subject:
            return [f for f in self._facts if f.subject == subject]
        return self._facts

    def entities(self):
        return self._entities

    def predicates(self):
        return list({f.predicate for f in self._facts})

    def neighbors(self, entity_id):
        result = set()
        for f in self._facts:
            if f.subject == entity_id:
                result.add(f.object)
            if f.object == entity_id:
                result.add(f.subject)
        return list(result)


@pytest.fixture
def unified():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConceptStore(Path(tmp) / "concepts.db")

        # Add ET concepts
        store.add_concept("ontology", "Ontology as type system", "theme", "Types enforced at write time",
                          source_quote="the ontology is a TYPE SYSTEM")
        store.add_concept("additive", "Additive-only extensions", "decision", "Can only add, never remove",
                          source_quote="extensions can add but cannot remove")
        store.add_concept("silk-concept", "Silk validation", "entity", "Rust CRDT graph store",
                          source_quote="GraphStore takes ontology as constructor param")

        # Add ET relationships
        store.add_relationship("ontology", "additive", weight=3.0, context="Type system constrains extensions")
        store.add_relationship("silk-concept", "ontology", weight=1.0, context="Silk enforces ontology")

        kg = FakeKGView()
        yield UnifiedGraph(store, kg)


@pytest.fixture
def unified_no_kg():
    """UnifiedGraph without a provider KG (folder/claude-code providers)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConceptStore(Path(tmp) / "concepts.db")
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "topic", "")
        store.add_relationship("a", "b", weight=1.0)
        yield UnifiedGraph(store, None)


class TestUnifiedGraphNodes:

    def test_all_nodes_includes_both_stores(self, unified):
        nodes = unified.all_nodes()
        ids = {n.id for n in nodes}
        # ET concepts (prefixed)
        assert "et:ontology" in ids
        assert "et:additive" in ids
        # Provider entities (prefixed)
        assert "mp:silk" in ids
        assert "mp:shelob" in ids

    def test_node_types_preserved(self, unified):
        nodes = unified.all_nodes()
        node_map = {n.id: n for n in nodes}
        assert node_map["et:ontology"].node_type == "concept"
        assert node_map["et:ontology"].source_system == "et"
        assert node_map["mp:silk"].node_type == "entity"
        assert node_map["mp:silk"].source_system == "mempalace"

    def test_degrades_without_kg(self, unified_no_kg):
        nodes = unified_no_kg.all_nodes()
        assert len(nodes) == 2  # Only ET concepts
        assert all(n.source_system == "et" for n in nodes)


class TestUnifiedGraphEdges:

    def test_all_edges_includes_both_stores(self, unified):
        edges = unified.all_edges()
        edge_types = {e.edge_type for e in edges}
        # ET relationships
        assert "RelatesTo" in edge_types
        # Provider triples
        assert "has_bug" in edge_types
        assert "implemented" in edge_types

    def test_edges_have_source_system(self, unified):
        edges = unified.all_edges()
        et_edges = [e for e in edges if e.source_system == "et"]
        mp_edges = [e for e in edges if e.source_system == "mempalace"]
        assert len(et_edges) >= 2
        assert len(mp_edges) >= 3


class TestUnifiedGraphTraversal:

    def test_neighbors_crosses_stores(self, unified):
        # Silk has neighbors in provider KG (outgoing-edges-bug)
        neighbors = unified.neighbors("mp:silk")
        neighbor_ids = {n.id for n in neighbors}
        assert "mp:outgoing-edges-bug" in neighbor_ids

    def test_neighbors_et_concepts(self, unified):
        neighbors = unified.neighbors("et:ontology")
        neighbor_ids = {n.id for n in neighbors}
        assert "et:additive" in neighbor_ids
        assert "et:silk-concept" in neighbor_ids

    def test_find_path_within_et(self, unified):
        path = unified.find_path("et:ontology", "et:additive")
        assert path is not None
        assert len(path) == 2

    def test_find_path_within_provider(self, unified):
        path = unified.find_path("mp:shelob", "mp:capability-scope-enum")
        assert path is not None

    def test_no_path_across_disconnected(self, unified):
        # ET concepts and MP entities aren't connected by edges in this test data
        path = unified.find_path("et:ontology", "mp:shelob")
        assert path is None  # No cross-store edges in test data

    def test_find_path_degrades_without_kg(self, unified_no_kg):
        path = unified_no_kg.find_path("et:a", "et:b")
        assert path is not None
        assert len(path) == 2


class TestUnifiedGraphOverview:

    def test_overview_counts_both(self, unified):
        overview = unified.get_overview()
        assert overview["total_nodes"] >= 5  # 3 ET + 3 MP (at least)
        assert overview["total_edges"] >= 5  # 2 ET + 3 MP

    def test_overview_shows_clusters(self, unified):
        overview = unified.get_overview()
        assert len(overview["clusters"]) >= 1

    def test_overview_degrades_without_kg(self, unified_no_kg):
        overview = unified_no_kg.get_overview()
        assert overview["total_nodes"] == 2
        assert overview["total_edges"] == 1


class TestUnifiedGraphNeighborhood:

    def test_neighborhood_includes_both_systems(self, unified):
        hood = unified.get_neighborhood("et:silk-concept")
        assert hood is not None
        assert hood["node"].source_system == "et"

    def test_neighborhood_not_found(self, unified):
        hood = unified.get_neighborhood("et:nonexistent")
        assert hood is None


class TestSameAsBridging:
    """Tests for SAME_AS edges that bridge ET and MP subgraphs."""

    @pytest.fixture
    def bridged(self):
        """UnifiedGraph where ET and MP share a concept name."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConceptStore(Path(tmp) / "concepts.db")
            # ET concept named "Silk" (matches MP entity "Silk")
            store.add_concept("silk", "Silk", "entity", "Rust CRDT graph")
            store.add_concept("ontology", "Ontology", "theme", "Type system")
            store.add_relationship("silk", "ontology", weight=1.0)

            kg = FakeKGView()  # Has entity "Silk" with id "silk"
            yield UnifiedGraph(store, kg)

    def test_same_as_edges_exist(self, bridged):
        edges = bridged.all_edges()
        same_as = [e for e in edges if e.edge_type == "SAME_AS"]
        assert len(same_as) >= 1
        assert same_as[0].source_system == "bridge"

    def test_cross_store_path_via_bridge(self, bridged):
        """Can find a path from ET concept to MP entity via SAME_AS bridge."""
        path = bridged.find_path("et:ontology", "mp:silk")
        assert path is not None
        # ontology -> silk (ET) -> silk (MP) via SAME_AS
        systems = [n.source_system for n in path]
        assert "et" in systems
        assert "mempalace" in systems

    def test_cross_store_neighbors(self, bridged):
        """Silk ET node should have MP neighbors via bridge."""
        neighbors = bridged.neighbors("et:silk")
        systems = {n.source_system for n in neighbors}
        # Should include both ET neighbors (ontology) and MP neighbors (via SAME_AS -> outgoing-edges-bug)
        assert "et" in systems
        assert "mempalace" in systems

    def test_no_bridge_without_matching_names(self, unified):
        """Original fixture has no matching names, so no SAME_AS edges."""
        edges = unified.all_edges()
        same_as = [e for e in edges if e.edge_type == "SAME_AS"]
        assert len(same_as) == 0


class TestUnifiedGraphRealData:
    """Integration tests against real mempalace KG + real ET ConceptStore."""

    @pytest.fixture
    def real_unified(self):
        from extended_thinking.storage.graph_store import GraphStore

        knowledge_path = Path.home() / ".extended-thinking" / "knowledge"
        if not knowledge_path.exists():
            pytest.skip("No real Kuzu KG")

        try:
            store = GraphStore(knowledge_path)
        except RuntimeError as e:
            # MCP server holds the Kuzu lock in normal dev; don't fail tests for it
            if "lock" in str(e).lower():
                pytest.skip("Kuzu DB locked (likely by running MCP server)")
            raise

        if store.get_stats()["total_concepts"] < 5:
            pytest.skip("Not enough concepts in real store (run et_sync first)")

        try:
            from extended_thinking.providers.mempalace import MemPalaceProvider
            provider = MemPalaceProvider()
            kg = provider.get_knowledge_graph()
        except Exception:
            pytest.skip("MemPalace provider not available")
        if kg is None:
            pytest.skip("No mempalace KG available")

        yield UnifiedGraph(store, kg)

    def test_real_unified_node_counts(self, real_unified):
        nodes = real_unified.all_nodes()
        et_nodes = [n for n in nodes if n.source_system == "et"]
        mp_nodes = [n for n in nodes if n.source_system == "mempalace"]
        assert len(et_nodes) >= 10
        assert len(mp_nodes) >= 30
        assert len(nodes) >= 40

    def test_real_unified_edge_counts(self, real_unified):
        edges = real_unified.all_edges()
        et_edges = [e for e in edges if e.source_system == "et"]
        mp_edges = [e for e in edges if e.source_system == "mempalace"]
        assert len(et_edges) >= 5
        assert len(mp_edges) >= 10  # Organic triples only (echo loop filtered)

    def test_real_path_within_mp(self, real_unified):
        """Silk -> its bug, within mempalace KG."""
        nodes = real_unified.all_nodes()
        silk = next((n for n in nodes if n.label == "Silk" and n.source_system == "mempalace"), None)
        if silk is None:
            pytest.skip("Silk entity not in mempalace KG")

        neighbors = real_unified.neighbors(silk.id)
        assert len(neighbors) >= 1

    def test_real_overview_has_clusters(self, real_unified):
        overview = real_unified.get_overview()
        assert overview["total_nodes"] >= 40
        assert overview["total_edges"] >= 15  # Organic edges only (echo loop filtered)
        assert len(overview["clusters"]) >= 1

    def test_real_no_cross_store_path(self, real_unified):
        """ET and MP graphs are disconnected (no cross-store edges in real data)."""
        nodes = real_unified.all_nodes()
        et_node = next((n for n in nodes if n.source_system == "et"), None)
        mp_node = next((n for n in nodes if n.source_system == "mempalace"), None)
        if not et_node or not mp_node:
            pytest.skip("Need both ET and MP nodes")
        path = real_unified.find_path(et_node.id, mp_node.id)
        assert path is None
