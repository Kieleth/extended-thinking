"""ADR 013 C5: filtered bitemporal queries.

`GraphStore.diff(from, to, *, node_types, edge_types, property_match,
namespace)` returns a scoped delta instead of the everything-or-nothing
shape pre-C5.

The MCP-side `et_shift` tool surfaces the same filters through its
input schema. A research-loop consumer watching their slice must not
receive a river of memory noise.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from extended_thinking.storage.graph_store import GraphStore
from schema.generated import models as m


@pytest.fixture
def kg():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "kg")


def _iso_minus(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── Filtered diff ─────────────────────────────────────────────────────

class TestFilteredDiff:

    def test_no_filters_returns_everything(self, kg):
        kg.add_concept("c1", "alpha", "topic", "x")
        # Bitemporal window must straddle the writes
        out = kg.diff(_iso_minus(1), _iso_minus(-1))
        assert any(n["_type"] == "Concept" for n in out["nodes_added"])

    def test_node_types_filter_scopes_to_one_type(self, kg):
        kg.add_concept("c1", "memory", "topic", "x")
        kg.insert(m.Concept(id="r1", name="research",
                            category=m.ConceptCategory.topic),
                  namespace="research")
        kg.insert(m.Wisdom(id="w1", name="w", title="t",
                           wisdom_type=m.WisdomType.wisdom,
                           signal_type="wisdom_card", bearer_id="c1"))

        out = kg.diff(_iso_minus(1), _iso_minus(-1), node_types=["Concept"])
        assert all(n["_type"] == "Concept" for n in out["nodes_added"])
        assert not any(n["_type"] == "Wisdom" for n in out["nodes_added"])

    def test_namespace_filter_scopes_slice(self, kg):
        kg.add_concept("mem-1", "memory concept", "topic", "")  # namespace=memory
        kg.insert(m.Concept(id="res-1", name="research concept",
                            category=m.ConceptCategory.topic),
                  namespace="research")

        mem_diff = kg.diff(_iso_minus(1), _iso_minus(-1), namespace="memory")
        res_diff = kg.diff(_iso_minus(1), _iso_minus(-1), namespace="research")

        mem_ids = {n["id"] for n in mem_diff["nodes_added"]}
        res_ids = {n["id"] for n in res_diff["nodes_added"]}
        assert mem_ids == {"mem-1"}
        assert res_ids == {"res-1"}

    def test_edge_types_filter_scopes_edges(self, kg):
        # Seed: two concepts + RelatesTo + a Wisdom + InformedBy
        kg.add_concept("c1", "a", "topic", "")
        kg.add_concept("c2", "b", "topic", "")
        kg.add_relationship("c1", "c2", weight=1.0)
        kg.add_wisdom("w", "d", wisdom_type="wisdom", related_concept_ids=["c1"])

        out = kg.diff(_iso_minus(1), _iso_minus(-1), edge_types=["RelatesTo"])
        assert all(e["_type"] == "RelatesTo" for e in out["edges_added"])
        assert not any(e["_type"] == "InformedBy" for e in out["edges_added"])

    def test_property_match_scopes_nodes(self, kg):
        """Node-side property filter: restrict by a column equality."""
        kg.add_concept("open-1", "alpha", "topic", "")
        kg.add_concept("open-2", "beta", "theme", "")

        out = kg.diff(_iso_minus(1), _iso_minus(-1),
                      node_types=["Concept"],
                      property_match={"category": "theme"})
        ids = {n["id"] for n in out["nodes_added"]}
        assert ids == {"open-2"}

    def test_combined_filters(self, kg):
        """Multiple filters AND together."""
        kg.add_concept("mem-open", "x", "topic", "")  # memory namespace
        kg.insert(m.Concept(id="res-open", name="r",
                            category=m.ConceptCategory.topic),
                  namespace="research")

        out = kg.diff(_iso_minus(1), _iso_minus(-1),
                      node_types=["Concept"], namespace="research")
        ids = {n["id"] for n in out["nodes_added"]}
        assert ids == {"res-open"}


# ── Validation ────────────────────────────────────────────────────────

class TestDiffValidation:

    def test_unknown_node_type_raises_loudly(self, kg):
        with pytest.raises(ValueError, match="unknown node_types"):
            kg.diff(_iso_minus(1), _iso_minus(-1), node_types=["NotReal"])

    def test_unknown_edge_type_raises_loudly(self, kg):
        with pytest.raises(ValueError, match="unknown edge_types"):
            kg.diff(_iso_minus(1), _iso_minus(-1), edge_types=["NotReal"])


# ── Generic shape coexists with legacy keys ───────────────────────────

class TestLegacyCompat:
    """Pre-C5 callers used concepts_added / concepts_deprecated /
    edges_created. Those keys stay populated so legacy code doesn't break."""

    def test_legacy_keys_populated(self, kg):
        kg.add_concept("c1", "a", "topic", "")
        out = kg.diff(_iso_minus(1), _iso_minus(-1))
        assert "concepts_added" in out
        assert "concepts_deprecated" in out
        assert "edges_created" in out
        # And the new generic keys too
        assert "nodes_added" in out
        assert "edges_added" in out

    def test_legacy_concepts_matches_generic_subset(self, kg):
        kg.add_concept("c1", "a", "topic", "")
        kg.add_wisdom("w", "d", wisdom_type="wisdom")
        out = kg.diff(_iso_minus(1), _iso_minus(-1))
        # legacy `concepts_added` = Concept subset of `nodes_added`
        generic_concepts = [n for n in out["nodes_added"] if n["_type"] == "Concept"]
        assert len(out["concepts_added"]) == len(generic_concepts)


# ── Result shape ──────────────────────────────────────────────────────

class TestResultShape:

    def test_filters_echoed_in_result(self, kg):
        out = kg.diff(_iso_minus(1), _iso_minus(-1),
                      node_types=["Concept"],
                      namespace="memory")
        assert out["filters"]["node_types"] == ["Concept"]
        assert out["filters"]["namespace"] == "memory"

    def test_window_echoed(self, kg):
        out = kg.diff("2024-01-01", "2025-01-01")
        assert out["window"] == {"from": "2024-01-01", "to": "2025-01-01"}

    def test_nodes_carry_type_tag(self, kg):
        kg.add_concept("c1", "a", "topic", "")
        out = kg.diff(_iso_minus(1), _iso_minus(-1))
        for n in out["nodes_added"]:
            assert "_type" in n
            assert n["_type"] in kg._ontology.node_tables

    def test_edges_carry_type_tag(self, kg):
        kg.add_concept("c1", "a", "topic", "")
        kg.add_concept("c2", "b", "topic", "")
        kg.add_relationship("c1", "c2", weight=1.0)
        out = kg.diff(_iso_minus(1), _iso_minus(-1))
        for e in out["edges_added"]:
            assert "_type" in e
            assert e["_type"] in kg._ontology.edge_tables


# ── MCP et_shift surface ──────────────────────────────────────────────

class TestEtShiftSurface:

    def test_et_shift_schema_includes_filters(self):
        from extended_thinking.mcp_server import TOOLS
        shift = next(t for t in TOOLS if t["name"] == "et_shift")
        props = shift["inputSchema"]["properties"]
        for k in ("node_types", "edge_types", "namespace", "property_match"):
            assert k in props, f"et_shift schema missing '{k}' filter"
