"""ADR 013 Phase 0: Kuzu DDL codegen (scripts/gen_kuzu.py).

Tests that the generated DDL:
  - Parses and executes against a real Kuzu instance.
  - Emits one NODE TABLE per ET node class and one REL TABLE per edge class.
  - Enforces FROM/TO constraints at insert time (constitutive per the
    malleus KG protocol — Architecture A).
  - Maps `description` → `desc_text` (Kuzu reserved word), round-trippable
    via COLUMN_MAPPING.
  - Attaches bitemporal + namespace system columns to every typed row.

This gives `make schema-kuzu` a regression guard: if the LinkML schema
changes in a way that breaks Kuzu, CI catches it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import kuzu
import pytest

from schema.generated.kuzu_ddl import (
    COLUMN_MAPPING,
    EDGE_TABLES,
    EXTENDED_THINKING_DDL,
    NODE_TABLES,
)


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        db = kuzu.Database(str(Path(tmp) / "kg"))
        c = kuzu.Connection(db)
        for stmt in EXTENDED_THINKING_DDL:
            c.execute(stmt)
        yield c


# ── Structural assertions ─────────────────────────────────────────────

class TestStructure:

    def test_generates_expected_node_tables(self):
        expected = {
            "Source", "Session", "Fragment", "Chunk",
            "Concept", "Insight", "Wisdom", "Suggestion",
            "Rationale",       # ADR 013 C4 — grounded rationale
            "KnowledgeNode",   # ADR 011 v2 — external enrichment
            "EnrichmentRun",   # ADR 011 v2 — enrichment telemetry
        }
        assert set(NODE_TABLES) == expected

    def test_generates_expected_edge_tables(self):
        expected = {
            "RelatesTo", "InformedBy", "HasProvenance", "Supersedes",
            "ProposalBy",        # ADR 013 C7 — algorithm write-back
            "Enriches",          # ADR 011 v2 — Concept → KnowledgeNode
            "WisdomEnriches",    # ADR 011 v2 — Wisdom → KnowledgeNode
        }
        assert set(EDGE_TABLES) == expected

    def test_no_mixin_or_abstract_classes_emitted(self):
        """Malleus Entity/Event/Signal/Relation and mixins are abstract —
        they define the TBox but aren't concrete rows."""
        for abstract in ("Entity", "Event", "Signal", "Relation",
                         "Identifiable", "Temporal", "Describable",
                         "Statusable", "Agent"):
            assert abstract not in NODE_TABLES
            assert abstract not in EDGE_TABLES


# ── DDL executes + schema lives in Kuzu ───────────────────────────────

class TestDDLExecutes:

    def test_all_statements_apply(self, conn):
        """Fixture construction applies every statement; reaching here means
        they all parsed and executed. This test just asserts the fixture
        yielded a real connection."""
        assert conn is not None

    def test_tables_queryable_after_creation(self, conn):
        """Each generated table must be addressable by MATCH."""
        for t in NODE_TABLES:
            # Zero-row query — just verifies the table exists.
            conn.execute(f"MATCH (n:{t}) RETURN count(n)")


# ── System columns on every node table ────────────────────────────────

class TestSystemColumns:

    @pytest.mark.parametrize("node_type", [
        "Source", "Session", "Fragment", "Chunk",
        "Concept", "Insight", "Wisdom", "Suggestion",
    ])
    def test_node_has_bitemporal_columns(self, conn, node_type):
        """Insert a row with all system columns — if any are missing from
        the DDL this fails with "column not found"."""
        conn.execute(
            f"CREATE (:{node_type} {{"
            f"id: 'x', "
            f"t_valid_from: '2024-01-01', "
            f"t_valid_to: '', "
            f"t_created: '2024-01-01', "
            f"t_expired: '', "
            f"t_superseded_by: '', "
            f"namespace: 'default', "
            f"vectors_pending: true"
            f"}})"
        )
        row = conn.execute(
            f"MATCH (n:{node_type} {{id: 'x'}}) "
            f"RETURN n.t_valid_from, n.namespace, n.vectors_pending"
        ).get_next()
        assert row == ["2024-01-01", "default", True]


# ── Column renaming (description → desc_text) ────────────────────────

class TestColumnRenaming:

    def test_description_renamed_in_classes_that_have_it(self):
        """COLUMN_MAPPING must list every class where description is renamed."""
        # Classes with Describable mixin (directly or through Entity) have
        # a description slot in LinkML that maps to desc_text in Kuzu.
        describable_classes = {
            "Source", "Session", "Fragment", "Chunk",       # Entity-based
            "Concept",                                       # Entity + Statusable
            "Insight", "Wisdom", "Suggestion",              # Signal + Describable
        }
        for cname in describable_classes:
            assert cname in COLUMN_MAPPING, (
                f"{cname} should be in COLUMN_MAPPING because it inherits "
                f"description from Describable/Entity"
            )
            assert COLUMN_MAPPING[cname].get("description") == "desc_text"

    def test_desc_text_is_writable(self, conn):
        """Insert with desc_text and read it back — confirms the rename is live."""
        conn.execute(
            "CREATE (:Concept {id: 'c1', name: 'alpha', "
            "category: 'topic', desc_text: 'a description'})"
        )
        row = conn.execute(
            "MATCH (c:Concept {id: 'c1'}) RETURN c.desc_text"
        ).get_next()
        assert row == ["a description"]


# ── FROM/TO enforced at insert (constitutive) ─────────────────────────

class TestConstitutiveDomainRange:

    def _seed(self, conn):
        conn.execute("CREATE (:Concept {id: 'c1', name: 'a', category: 'topic'})")
        conn.execute("CREATE (:Concept {id: 'c2', name: 'b', category: 'topic'})")
        conn.execute("CREATE (:Wisdom {id: 'w1', title: 'x', wisdom_type: 'wisdom'})")
        conn.execute(
            "CREATE (:Chunk {id: 'ch1', source: 'test.md', source_type: 'note'})"
        )

    def test_relates_to_accepts_concept_concept(self, conn):
        self._seed(conn)
        conn.execute(
            "MATCH (a:Concept {id:'c1'}), (b:Concept {id:'c2'}) "
            "CREATE (a)-[:RelatesTo {weight: 1.0}]->(b)"
        )

    def test_informed_by_accepts_wisdom_concept(self, conn):
        self._seed(conn)
        conn.execute(
            "MATCH (w:Wisdom {id:'w1'}), (c:Concept {id:'c1'}) "
            "CREATE (w)-[:InformedBy]->(c)"
        )

    def test_has_provenance_accepts_concept_chunk(self, conn):
        self._seed(conn)
        conn.execute(
            "MATCH (c:Concept {id:'c1'}), (ch:Chunk {id:'ch1'}) "
            "CREATE (c)-[:HasProvenance {source_provider: 'p', llm_model: 'm'}]->(ch)"
        )

    def test_relates_to_rejects_wrong_source_type(self, conn):
        """Wisdom -> Concept via RelatesTo must be rejected: RelatesTo is
        pinned Concept -> Concept in the ontology. This is the whole point
        of Architecture A: invalid writes rejected at call time."""
        self._seed(conn)
        with pytest.raises(Exception) as exc:
            conn.execute(
                "MATCH (w:Wisdom {id:'w1'}), (c:Concept {id:'c1'}) "
                "CREATE (w)-[:RelatesTo {weight: 1.0}]->(c)"
            )
        assert "Expected labels" in str(exc.value) or "Concept" in str(exc.value)

    def test_informed_by_rejects_concept_wisdom_direction(self, conn):
        """InformedBy is Wisdom -> Concept. Reverse direction invalid."""
        self._seed(conn)
        with pytest.raises(Exception):
            conn.execute(
                "MATCH (c:Concept {id:'c1'}), (w:Wisdom {id:'w1'}) "
                "CREATE (c)-[:InformedBy]->(w)"
            )


# ── Regenerability ────────────────────────────────────────────────────

_MALLEUS_IMPORT = Path(__file__).resolve().parents[2] / "schema" / "imports" / "malleus.yaml"


class TestRegenerability:
    """`make schema-kuzu` must be idempotent: regenerating produces
    byte-identical output. Without this, CI drift-checks become flaky."""

    @pytest.mark.skipif(
        not _MALLEUS_IMPORT.exists(),
        reason="malleus ontology not resolvable in this environment "
               "(symlink to sibling repo broken). Skipped until malleus-dev "
               "lands on PyPI and CI can install it.",
    )
    def test_two_runs_produce_identical_output(self, tmp_path, monkeypatch):
        from scripts import gen_kuzu
        from linkml_runtime.utils.schemaview import SchemaView

        sv = SchemaView(str(gen_kuzu.SCHEMA_PATH))
        bundle_a = gen_kuzu.generate(sv)
        bundle_b = gen_kuzu.generate(sv)

        assert bundle_a == bundle_b
