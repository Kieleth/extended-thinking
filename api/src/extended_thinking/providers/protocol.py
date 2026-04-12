"""MemoryProvider protocol — the interface between extended-thinking and any memory system.

Extended-thinking doesn't care where your memories live. MemPalace, Obsidian,
a folder of markdown files, Claude Code sessions — anything that implements
this protocol can feed the DIKW pipeline.

Design choices:
  - Protocol (structural subtyping), not ABC. No forced inheritance.
  - Frozen dataclasses for data transfer. Immutable, hashable, safe to cache.
  - All methods are synchronous. Providers are expected to be fast (local reads).
    If a provider needs async (e.g., cloud API), wrap in asyncio.to_thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryChunk:
    """A piece of content from the memory system.

    The atomic unit that extended-thinking reasons about. Could be a Q+A exchange,
    a paragraph from a document, a note, a code snippet — anything textual with
    provenance.
    """

    id: str
    """Unique identifier within the provider. Provider-specific format."""

    content: str
    """The actual text content. Verbatim, never summarized by the provider."""

    source: str
    """Where this came from. File path, URL, session ID — provider-specific."""

    timestamp: str
    """ISO 8601 timestamp. When the content was created or captured."""

    metadata: dict = field(default_factory=dict)
    """Provider-specific metadata. Examples:
    - MemPalace: {"wing": "work", "room": "auth", "memory_type": "decision"}
    - Claude Code: {"role": "user", "session_id": "abc", "project": "shelob"}
    - Folder: {"filename": "notes.md", "line_start": 42}
    """


@dataclass(frozen=True)
class Entity:
    """A named entity from the memory system.

    People, projects, tools, organizations — anything the memory system
    has identified as a distinct named thing.
    """

    name: str
    """Canonical name. "Luis", "Shelob", "FastAPI"."""

    entity_type: str
    """Category. "person", "project", "tool", "organization", "concept"."""

    properties: dict = field(default_factory=dict)
    """Provider-specific properties. Examples:
    - MemPalace: {"valid_from": "2026-01", "confidence": 0.9}
    - Obsidian: {"file": "People/Luis.md", "tags": ["team", "founder"]}
    """


@dataclass(frozen=True)
class Fact:
    """A knowledge triple from the memory system's KG.

    Represents a temporal relationship: (subject, predicate, object) with
    validity window. Examples:
      ("Silk", "has_bug", "outgoing-edges-empty", valid_from="2026-04-10")
      ("Shelob", "implemented", "capability-scope-enum", valid_from="2026-03-15")
    """

    subject: str
    """Entity or concept that this fact is about."""

    predicate: str
    """Relationship type. "has_bug", "implemented", "design_decision", etc."""

    object: str
    """Target entity, concept, or value."""

    valid_from: str = ""
    """ISO 8601 date when this fact became true."""

    valid_to: str | None = None
    """ISO 8601 date when this fact stopped being true. None = still valid."""

    confidence: float = 1.0
    """Confidence score (0.0 to 1.0)."""

    source: str = ""
    """Where this fact came from."""


@runtime_checkable
class KnowledgeGraphView(Protocol):
    """Read-only view of a memory system's native knowledge graph.

    Not all memory systems have structured knowledge. Providers that do
    (MemPalace, Zep, Obsidian) implement this. Others return None from
    MemoryProvider.get_knowledge_graph().

    The unified graph layer merges this with ET's ConceptStore for
    federated queries across both systems.
    """

    def facts(self, subject: str | None = None) -> list[Fact]:
        """Get facts (triples). If subject is given, only facts about that entity."""
        ...

    def entities(self) -> list[Entity]:
        """All entities in the knowledge graph."""
        ...

    def predicates(self) -> list[str]:
        """Distinct relationship types."""
        ...

    def neighbors(self, entity_id: str) -> list[str]:
        """Entity IDs connected to this entity (outgoing + incoming)."""
        ...


@runtime_checkable
class MemoryProvider(Protocol):
    """Interface for any memory system that extended-thinking can read from.

    Implement this protocol to connect your memory system to the DIKW pipeline.
    All methods are synchronous and should return quickly (local reads).

    Extended-thinking calls:
      - get_recent() on sync to find new memories
      - search() when Opus needs context for wisdom generation
      - get_entities() to populate the concept graph with known entities
      - store_insight() to write generated wisdom back to your memory system
      - get_insights() to retrieve previously generated insights
      - get_stats() for the UI dashboard

    Optional attributes:
      - extract_concepts: bool (default True). When False, the pipeline
        stores chunks with provenance but skips the LLM concept-extraction
        pass. Providers returning structured data (typed run records,
        telemetry, form submissions) set this to False. Conversation
        providers keep the default (ADR 013 C8).
    """

    @property
    def name(self) -> str:
        """Provider identifier. "mempalace", "claude-code", "folder", etc."""
        ...

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        """Search memories by content. Semantic if the provider supports it,
        keyword match otherwise. Returns most relevant first."""
        ...

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        """Get recent memories, newest first. If since is provided (ISO 8601),
        only return memories created after that timestamp."""
        ...

    def get_entities(self) -> list[Entity]:
        """Get known entities. Returns empty list if the provider doesn't
        support entity extraction (e.g., FolderProvider)."""
        ...

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        """Store a generated insight back into the memory system.
        Returns the ID of the stored insight. The provider decides
        the storage format (drawer, file, KG triple, etc.)."""
        ...

    def get_insights(self) -> list[MemoryChunk]:
        """Retrieve previously stored insights."""
        ...

    def get_stats(self) -> dict:
        """Provider statistics for the UI dashboard.
        Must include: total_memories (int), last_updated (str or None).
        May include: total_entities, total_insights, provider-specific counts."""
        ...

    def get_knowledge_graph(self) -> KnowledgeGraphView | None:
        """Return a queryable view of the provider's native knowledge graph.
        Returns None for providers without structured knowledge (folders, raw files).
        When available, the UnifiedGraph layer merges this with ET's ConceptStore."""
        ...
