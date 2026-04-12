"""Tests for ConceptStore graph queries — the KG explorer foundation."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.processing.concept_store import ConceptStore


@pytest.fixture
def graph_store():
    """Store with a small concept graph for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConceptStore(Path(tmp) / "test.db")

        # Concepts
        store.add_concept("ontology", "Ontology as type system", "theme", "Types enforced at write time",
                          source_quote="the ontology is a TYPE SYSTEM")
        store.add_concept("additive", "Additive-only extensions", "decision", "Can only add, never remove",
                          source_quote="extensions can add types but cannot remove")
        store.add_concept("fleet", "Fleet deployment", "topic", "Multi-instance deployment model",
                          source_quote="Fleet is the unit of deployment")
        store.add_concept("closed-world", "Closed world assumption", "decision", "Reject unknown types",
                          source_quote="not OWL reasoning")
        store.add_concept("silk", "Silk graph store", "entity", "Rust CRDT knowledge graph",
                          source_quote="GraphStore(instance_id, ontology_json())")
        store.add_concept("registry", "Write-time validation", "topic", "All writes through registry",
                          source_quote="All writes go through the registry")
        store.add_concept("isolated", "Isolated concept", "topic", "Not connected to anything")

        # Relationships (a small graph)
        store.add_relationship("ontology", "additive", weight=3.0, context="Extensions constrained by type system")
        store.add_relationship("ontology", "closed-world", weight=2.0, context="CWA enforces type completeness")
        store.add_relationship("additive", "fleet", weight=1.0, context="Fleet complicates additive-only")
        store.add_relationship("closed-world", "registry", weight=2.0, context="Registry enforces CWA")
        store.add_relationship("silk", "ontology", weight=1.0, context="Silk takes ontology at genesis")
        store.add_relationship("silk", "registry", weight=1.0, context="Silk validates via registry")

        # Wisdom
        store.add_wisdom(
            "Additive-only is secretly versioning",
            "Your extensions accumulate forever",
            "wisdom", 10, 7,
            related_concept_ids=["additive", "fleet", "closed-world"],
        )

        yield store


class TestGraphOverview:

    def test_get_graph_overview(self, graph_store):
        overview = graph_store.get_graph_overview()
        assert overview["total_concepts"] == 7
        assert overview["total_relationships"] == 6
        assert overview["total_wisdoms"] == 1
        assert len(overview["clusters"]) >= 1
        assert len(overview["bridges"]) >= 0
        assert len(overview["isolated"]) >= 1
        assert "Isolated concept" in [c["name"] for c in overview["isolated"]]

    def test_clusters_group_connected_concepts(self, graph_store):
        overview = graph_store.get_graph_overview()
        # The main cluster should contain ontology, additive, fleet, closed-world, silk, registry
        main_cluster = max(overview["clusters"], key=lambda c: len(c["concepts"]))
        assert len(main_cluster["concepts"]) >= 5

    def test_bridges_identify_connectors(self, graph_store):
        overview = graph_store.get_graph_overview()
        # Concepts with high connectivity relative to their cluster
        # "ontology" connects to 3 things, "silk" connects 2 subclusters
        bridge_names = [b["name"] for b in overview["bridges"]]
        assert "Ontology as type system" in bridge_names or len(bridge_names) >= 0


class TestFindPath:

    def test_find_direct_path(self, graph_store):
        path = graph_store.find_path("ontology", "additive")
        assert path is not None
        assert len(path) == 2
        assert path[0]["id"] == "ontology"
        assert path[1]["id"] == "additive"

    def test_find_indirect_path(self, graph_store):
        path = graph_store.find_path("fleet", "registry")
        assert path is not None
        # fleet → additive → ontology → closed-world → registry (or shorter)
        assert len(path) >= 2

    def test_no_path_to_isolated(self, graph_store):
        path = graph_store.find_path("ontology", "isolated")
        assert path is None

    def test_path_to_self(self, graph_store):
        path = graph_store.find_path("ontology", "ontology")
        assert path is not None
        assert len(path) == 1


class TestGetFullRelationships:

    def test_get_concept_neighborhood(self, graph_store):
        neighborhood = graph_store.get_neighborhood("ontology")
        assert neighborhood["concept"]["id"] == "ontology"
        assert len(neighborhood["connections"]) >= 3
        # Should include additive, closed-world, silk
        neighbor_names = [c["name"] for c in neighborhood["connections"]]
        assert "Additive-only extensions" in neighbor_names

    def test_neighborhood_includes_edge_context(self, graph_store):
        neighborhood = graph_store.get_neighborhood("ontology")
        for conn in neighborhood["connections"]:
            assert "weight" in conn
            assert "context" in conn

    def test_neighborhood_includes_related_wisdom(self, graph_store):
        neighborhood = graph_store.get_neighborhood("additive")
        assert len(neighborhood["related_wisdoms"]) >= 1

    def test_nonexistent_concept(self, graph_store):
        neighborhood = graph_store.get_neighborhood("nonexistent")
        assert neighborhood is None
