"""Tests for the internal concept store — where extracted concepts live."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.processing.concept_store import ConceptStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ConceptStore(Path(tmp) / "concepts.db")


class TestConceptStore:

    def test_add_concept(self, store):
        store.add_concept("jwt-auth", "JWT auth", "topic", "Token-based auth",
                          source_quote="How should we handle JWT auth?")
        concept = store.get_concept("jwt-auth")
        assert concept["name"] == "JWT auth"
        assert concept["category"] == "topic"
        assert concept["frequency"] == 1

    def test_add_duplicate_increments_frequency(self, store):
        store.add_concept("jwt-auth", "JWT auth", "topic", "desc1")
        store.add_concept("jwt-auth", "JWT auth", "topic", "longer description here")

        concept = store.get_concept("jwt-auth")
        assert concept["frequency"] == 2
        # Longer description wins
        assert "longer" in concept["description"]

    def test_list_concepts(self, store):
        store.add_concept("a", "Alpha", "topic", "")
        store.add_concept("b", "Beta", "theme", "")
        store.add_concept("c", "Gamma", "decision", "")

        concepts = store.list_concepts()
        assert len(concepts) == 3

    def test_list_concepts_by_frequency(self, store):
        store.add_concept("rare", "Rare", "topic", "")
        store.add_concept("common", "Common", "topic", "")
        store.add_concept("common", "Common", "topic", "")
        store.add_concept("common", "Common", "topic", "")

        concepts = store.list_concepts(order_by="frequency")
        assert concepts[0]["name"] == "Common"
        assert concepts[0]["frequency"] == 3

    def test_add_relationship(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "topic", "")
        store.add_relationship("a", "b", weight=2.0, context="co-occur in 2 sessions")

        rels = store.get_relationships("a")
        assert len(rels) == 1
        assert rels[0]["target_id"] == "b"
        assert rels[0]["weight"] == 2.0

    def test_add_wisdom(self, store):
        wisdom_id = store.add_wisdom(
            title="Test wisdom",
            description="Why + Action",
            wisdom_type="wisdom",
            based_on_sessions=10,
            based_on_concepts=50,
            related_concept_ids=["a", "b"],
        )
        assert wisdom_id

        wisdoms = store.list_wisdoms()
        assert len(wisdoms) == 1
        assert wisdoms[0]["title"] == "Test wisdom"
        assert wisdoms[0]["status"] == "pending"

    def test_update_wisdom_status(self, store):
        wid = store.add_wisdom("Test", "desc", "wisdom", 0, 0)
        store.update_wisdom_status(wid, "seen")

        wisdom = store.get_wisdom(wid)
        assert wisdom["status"] == "seen"

    def test_add_feedback(self, store):
        wid = store.add_wisdom("Test", "desc", "wisdom", 0, 0)
        store.add_feedback(wid, "I tried this and it worked great.")

        wisdom = store.get_wisdom(wid)
        assert len(wisdom["feedback"]) == 1
        assert "worked great" in wisdom["feedback"][0]["content"]

    def test_get_stats(self, store):
        store.add_concept("a", "A", "topic", "")
        store.add_concept("b", "B", "theme", "")
        store.add_wisdom("W", "d", "wisdom", 0, 0)

        stats = store.get_stats()
        assert stats["total_concepts"] == 2
        assert stats["total_wisdoms"] == 1

    def test_pending_wisdom(self, store):
        store.add_wisdom("W1", "d1", "wisdom", 0, 0)
        store.add_wisdom("W2", "d2", "wisdom", 0, 0)

        pending = store.list_wisdoms(status="pending")
        assert len(pending) == 2

        store.update_wisdom_status(pending[0]["id"], "seen")
        pending = store.list_wisdoms(status="pending")
        assert len(pending) == 1

    def test_persistence(self):
        """Data survives close and reopen."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"

            store1 = ConceptStore(db_path)
            store1.add_concept("a", "Alpha", "topic", "test")
            del store1

            store2 = ConceptStore(db_path)
            concept = store2.get_concept("a")
            assert concept["name"] == "Alpha"
