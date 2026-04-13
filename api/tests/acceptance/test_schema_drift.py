"""AT: GraphStore.check_schema — fail-fast on populated DBs (R11 follow-up).

Online CREATE TABLE IF NOT EXISTS migration is safe in isolation but
dangerous against a populated DB with other writers. `check_schema`
lets a caller gate on drift before they start using the store:

  - empty DB → no drift (first-time init)
  - DB matches the ontology exactly → no drift
  - DB is populated AND the ontology adds new tables → SchemaDriftError
"""

from __future__ import annotations

import pytest

from extended_thinking._schema import models as m
from extended_thinking.storage.graph_store import (
    GraphStore,
    SchemaDriftError,
    _extract_table_names,
)
from extended_thinking.storage.ontology import Ontology, default_ontology

pytestmark = pytest.mark.acceptance


# ── DDL parsing helper ───────────────────────────────────────────────

class TestExtractTableNames:
    """The regex underpinning check_schema — sanity-check each CREATE
    shape Kuzu supports."""

    def test_node_table(self):
        ddl = ["CREATE NODE TABLE IF NOT EXISTS Concept (id STRING)"]
        assert _extract_table_names(ddl) == {"Concept"}

    def test_rel_table(self):
        ddl = ["CREATE REL TABLE IF NOT EXISTS RelatesTo (FROM Concept TO Concept)"]
        assert _extract_table_names(ddl) == {"RelatesTo"}

    def test_without_if_not_exists(self):
        ddl = ["CREATE NODE TABLE Wisdom (id STRING)"]
        assert _extract_table_names(ddl) == {"Wisdom"}

    def test_multi_statement(self):
        ddl = [
            "CREATE NODE TABLE Concept (id STRING)",
            "CREATE NODE TABLE Wisdom (id STRING)",
            "CREATE REL TABLE InformedBy (FROM Wisdom TO Concept)",
        ]
        assert _extract_table_names(ddl) == {"Concept", "Wisdom", "InformedBy"}

    def test_empty(self):
        assert _extract_table_names([]) == set()


# ── check_schema outcomes ────────────────────────────────────────────

class TestCheckSchema:
    """The three cases R11 calls out: empty / matching / drifted."""

    def test_empty_db_no_drift(self, tmp_path):
        """Opening a brand-new path and calling check_schema before any
        writes is always silent — no existing tables means no drift."""
        kg = GraphStore(tmp_path / "kg")
        try:
            kg.check_schema()  # must not raise
        finally:
            kg.close()

    def test_populated_matching_ontology_no_drift(self, tmp_path):
        """DB + matching ontology = silent. Apply the ontology (happens
        on construction), write a row, reopen with the same ontology,
        check_schema should succeed."""
        with GraphStore(tmp_path / "kg") as kg:
            kg.add_concept("c-1", "one", "topic", "desc", namespace="memory")

        with GraphStore(tmp_path / "kg") as kg:
            # Default ontology matches the DB — no drift.
            kg.check_schema(default_ontology())

    def test_populated_db_with_missing_tables_raises(self, tmp_path):
        """DB is populated with a narrow ontology; caller asks for a
        wider ontology that adds tables. Must raise SchemaDriftError
        with the new table names called out."""
        # Build a minimal ontology with ONLY a handful of tables by
        # filtering the real one, then populate the DB with it.
        real = default_ontology()
        narrow_ddl = [
            stmt for stmt in real.ddl
            if all(tn not in _extract_table_names([stmt])
                   for tn in ("Rationale", "ProposalBy"))
        ]
        narrow = Ontology(name="narrow", ddl=narrow_ddl)

        with GraphStore(tmp_path / "kg", ontology=narrow) as kg:
            kg.add_concept("c-1", "one", "topic", "desc")

        # Reopen with the FULL ontology. It adds tables (e.g. Rationale,
        # ProposalBy) that aren't in the populated DB.
        with GraphStore(tmp_path / "kg", ontology=narrow) as kg:
            with pytest.raises(SchemaDriftError) as exc_info:
                kg.check_schema(real)
            err = exc_info.value
            assert err.db_path == tmp_path / "kg"
            assert err.existing_count > 0
            assert len(err.missing_tables) > 0
            # The specific missing tables should be in the list.
            missing = set(err.missing_tables)
            assert "Rationale" in missing or "ProposalBy" in missing

    def test_drift_error_message_includes_table_names(self, tmp_path):
        """The error message has to tell the user exactly which tables
        are missing so they can migrate deliberately."""
        real = default_ontology()
        narrow_ddl = [
            stmt for stmt in real.ddl
            if "Rationale" not in _extract_table_names([stmt])
        ]
        narrow = Ontology(name="narrow", ddl=narrow_ddl)

        with GraphStore(tmp_path / "kg", ontology=narrow) as kg:
            kg.add_concept("c-1", "one", "topic", "desc")

        with GraphStore(tmp_path / "kg", ontology=narrow) as kg:
            with pytest.raises(SchemaDriftError) as exc_info:
                kg.check_schema(real)
            msg = str(exc_info.value)
            assert "Rationale" in msg
            assert "migrate" in msg.lower()

    def test_extra_tables_in_db_do_not_drift(self, tmp_path):
        """If the DB has MORE tables than the ontology (backwards-compat:
        consumer uses a smaller ontology than what created the file),
        that's not drift — check_schema stays silent."""
        real = default_ontology()

        with GraphStore(tmp_path / "kg", ontology=real) as kg:
            kg.add_concept("c-1", "one", "topic", "desc")

        # Reopen with a NARROWER ontology; the DB has extra tables.
        narrow_ddl = [
            stmt for stmt in real.ddl
            if "Rationale" not in _extract_table_names([stmt])
        ]
        narrow = Ontology(name="narrow", ddl=narrow_ddl)
        with GraphStore(tmp_path / "kg", ontology=narrow) as kg:
            # Should NOT raise — drift is one-directional (new tables
            # in ontology that the DB doesn't know).
            kg.check_schema(narrow)
