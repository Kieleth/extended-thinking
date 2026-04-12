"""Generalize product invariants from test_invariants.py over a loaded fixture.

test_invariants.py asserts these at unit-level on tiny synthetic graphs.
Here we apply the same checks to `loaded_graph_small`, which is closer to
production shape (multiple nodes, multiple relationships, bitemporal edges).

Fails on any regression where an algorithm or sync silently produces
inconsistent graph state.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.acceptance

# GraphStore (Kuzu) ontology. Introspected via Kuzu's CALL SHOW_TABLES(),
# asserted against the set we expect to exist. Update this set when the
# schema evolves in graph_store._init_schema.
EXPECTED_NODE_TYPES = {"Concept", "Wisdom", "Chunk"}
EXPECTED_EDGE_TYPES = {"RelatesTo", "InformedBy", "HasProvenance"}


def _introspect_tables(store) -> tuple[set[str], set[str]]:
    """Return (node_tables, rel_tables) from Kuzu via CALL SHOW_TABLES()."""
    rows = store._query_all("CALL show_tables() RETURN *", {})
    nodes: set[str] = set()
    rels: set[str] = set()
    for row in rows:
        # Row shape: (id, name, type, database_name, comment) across Kuzu versions.
        name = row[1] if len(row) > 1 else None
        kind = row[2] if len(row) > 2 else ""
        if not name:
            continue
        kind_lower = str(kind).lower()
        if "node" in kind_lower:
            nodes.add(name)
        elif "rel" in kind_lower:
            rels.add(name)
    return nodes, rels


def test_ontology_is_complete(loaded_graph_small):
    """Every expected node and edge type exists in the Kuzu schema."""
    node_tables, rel_tables = _introspect_tables(loaded_graph_small)
    assert EXPECTED_NODE_TYPES.issubset(node_tables), (
        f"missing node tables: {EXPECTED_NODE_TYPES - node_tables}. "
        f"Found: {node_tables}"
    )
    assert EXPECTED_EDGE_TYPES.issubset(rel_tables), (
        f"missing rel tables: {EXPECTED_EDGE_TYPES - rel_tables}. "
        f"Found: {rel_tables}"
    )


def test_all_edges_reference_existing_nodes(loaded_graph_small):
    """No dangling edges. Source and target must both be concept IDs that exist."""
    concepts = loaded_graph_small._query_all(
        "MATCH (c:Concept) RETURN c.id", {}
    )
    known_ids = {row[0] for row in concepts}

    edges = loaded_graph_small._query_all(
        "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) RETURN a.id, b.id", {}
    )
    for src, tgt in edges:
        assert src in known_ids, f"edge source {src!r} references unknown concept"
        assert tgt in known_ids, f"edge target {tgt!r} references unknown concept"


def test_bitemporal_fields_present_on_every_edge(loaded_graph_small):
    """Every edge carries the ADR-002 bitemporal fields (t_valid_from, t_created)."""
    edges = loaded_graph_small._query_all(
        "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) "
        "RETURN r.t_valid_from, r.t_created, r.t_valid_to",
        {},
    )
    assert edges, "fixture produced no edges (did loaded_graph_small load?)"
    for t_valid_from, t_created, t_valid_to in edges:
        assert t_valid_from, "edge missing t_valid_from"
        assert t_created, "edge missing t_created"
        # t_valid_to empty string means "still valid" in this schema; that is fine.


def test_concepts_have_stable_ids_from_fixture(loaded_graph_small):
    """The committed fixture IDs are what lands in the store. No hashing-over of IDs."""
    expected = {
        "kuzu", "sqlite", "bitemporal_kg", "kg_store_choice",
        "chromadb", "concept_store_legacy", "graphiti_zep",
    }
    got = {
        row[0] for row in loaded_graph_small._query_all(
            "MATCH (c:Concept) RETURN c.id", {}
        )
    }
    assert got == expected, f"expected {expected}, got {got}"


def test_edge_weights_are_positive(loaded_graph_small):
    """No zero / negative edge weights in the loaded fixture."""
    edges = loaded_graph_small._query_all(
        "MATCH (a:Concept)-[r:RelatesTo]->(b:Concept) RETURN r.weight", {}
    )
    for (weight,) in edges:
        assert weight > 0, f"non-positive edge weight: {weight}"
