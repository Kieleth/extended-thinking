"""Tests for the MemoryProvider protocol and data classes."""

from dataclasses import FrozenInstanceError

import pytest

from extended_thinking.providers.protocol import (
    Entity,
    Fact,
    KnowledgeGraphView,
    MemoryChunk,
    MemoryProvider,
)


class TestMemoryChunk:

    def test_creation(self):
        chunk = MemoryChunk(
            id="chunk-001",
            content="How should I handle JWT auth?",
            source="/Users/test/.claude/projects/test/abc.jsonl",
            timestamp="2026-03-20T10:00:00Z",
            metadata={"role": "user", "session": "abc"},
        )
        assert chunk.id == "chunk-001"
        assert chunk.content == "How should I handle JWT auth?"
        assert chunk.timestamp == "2026-03-20T10:00:00Z"

    def test_immutable(self):
        chunk = MemoryChunk(
            id="chunk-001",
            content="test",
            source="test.md",
            timestamp="2026-03-20T10:00:00Z",
        )
        with pytest.raises(FrozenInstanceError):
            chunk.content = "modified"

    def test_default_metadata(self):
        chunk = MemoryChunk(
            id="chunk-001",
            content="test",
            source="test.md",
            timestamp="2026-03-20T10:00:00Z",
        )
        assert chunk.metadata == {}


class TestEntity:

    def test_creation(self):
        entity = Entity(
            name="Shelob",
            entity_type="project",
            properties={"language": "python", "status": "active"},
        )
        assert entity.name == "Shelob"
        assert entity.entity_type == "project"

    def test_immutable(self):
        entity = Entity(name="Shelob", entity_type="project")
        with pytest.raises(FrozenInstanceError):
            entity.name = "other"

    def test_default_properties(self):
        entity = Entity(name="Luis", entity_type="person")
        assert entity.properties == {}


class TestMemoryProviderProtocol:

    def test_protocol_is_structural(self):
        """MemoryProvider is a Protocol — any class with the right methods works.
        No inheritance required."""

        class FakeProvider:
            name = "fake"

            def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
                return []

            def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
                return []

            def get_entities(self) -> list[Entity]:
                return []

            def store_insight(self, title: str, description: str, related_concepts: list[str]) -> str:
                return "insight-001"

            def get_insights(self) -> list[MemoryChunk]:
                return []

            def get_stats(self) -> dict:
                return {"total_memories": 0}

            def get_knowledge_graph(self) -> None:
                return None

        provider = FakeProvider()
        # Structural subtyping — if it has the methods, it's a MemoryProvider
        assert isinstance(provider, MemoryProvider)
        assert provider.name == "fake"
        assert provider.search("test") == []
        assert provider.get_stats() == {"total_memories": 0}


class TestFact:

    def test_creation(self):
        fact = Fact(
            subject="silk",
            predicate="has_bug",
            object="outgoing-edges-empty",
            valid_from="2026-04-10",
            confidence=0.9,
            source="shelob-session",
        )
        assert fact.subject == "silk"
        assert fact.predicate == "has_bug"
        assert fact.confidence == 0.9

    def test_immutable(self):
        fact = Fact(subject="a", predicate="b", object="c")
        with pytest.raises(FrozenInstanceError):
            fact.subject = "modified"

    def test_defaults(self):
        fact = Fact(subject="a", predicate="b", object="c")
        assert fact.valid_from == ""
        assert fact.valid_to is None
        assert fact.confidence == 1.0
        assert fact.source == ""


class TestKnowledgeGraphView:

    def test_protocol_is_structural(self):
        """KnowledgeGraphView is a Protocol — structural typing."""

        class FakeKG:
            def facts(self, subject: str | None = None) -> list[Fact]:
                return [Fact(subject="a", predicate="knows", object="b")]

            def entities(self) -> list[Entity]:
                return [Entity(name="A", entity_type="person")]

            def predicates(self) -> list[str]:
                return ["knows"]

            def neighbors(self, entity_id: str) -> list[str]:
                return ["b"]

        kg = FakeKG()
        assert isinstance(kg, KnowledgeGraphView)
        assert len(kg.facts()) == 1
        assert kg.predicates() == ["knows"]
        assert kg.neighbors("a") == ["b"]
