from __future__ import annotations

import re
import sys
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)


metamodel_version = "1.7.0"
version = "None"


class ConfiguredBaseModel(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias=True,
        validate_by_name=True,
        validate_assignment=True,
        validate_default=True,
        extra="forbid",
        arbitrary_types_allowed=True,
        use_enum_values=True,
        strict=False,
    )


class LinkMLMeta(RootModel):
    root: dict[str, Any] = {}
    model_config = ConfigDict(frozen=True)

    def __getattr__(self, key: str):
        return getattr(self.root, key)

    def __getitem__(self, key: str):
        return self.root[key]

    def __setitem__(self, key: str, value):
        self.root[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.root


linkml_meta = LinkMLMeta(
    {
        "default_prefix": "et",
        "default_range": "string",
        "description": "Ontology for an externalized cognitive architecture that "
        "captures how humans think when using LLMs. Nodes represent "
        "captured sources, sessions, fragments, concepts, chunks, "
        "insights, wisdoms, and suggestions. Edges (all subclassing "
        "malleus:Relation) encode typed directed connections with "
        "bitemporal metadata.",
        "id": "https://extended-thinking.dev/schema",
        "imports": ["linkml:types", "imports/malleus"],
        "name": "extended_thinking",
        "prefixes": {
            "et": {"prefix_prefix": "et", "prefix_reference": "https://extended-thinking.dev/schema/"},
            "linkml": {"prefix_prefix": "linkml", "prefix_reference": "https://w3id.org/linkml/"},
            "malleus": {"prefix_prefix": "malleus", "prefix_reference": "https://malleus.dev/schema/"},
        },
        "source_file": "schema/extended_thinking.yaml",
        "title": "Extended Thinking Cognitive Architecture",
    }
)


class EntityStatus(str, Enum):
    """
    Lifecycle state of any identifiable entity.
    """

    ACTIVE = "ACTIVE"
    """
    Entity exists and is operational.
    """
    INACTIVE = "INACTIVE"
    """
    Entity exists but is suspended.
    """
    DESTROYED = "DESTROYED"
    """
    Entity has been permanently removed.
    """


class Platform(str, Enum):
    """
    Source platform where content was captured.
    """

    claude_code = "claude_code"
    """
    Claude Code CLI session.
    """
    opencode = "opencode"
    """
    opencode CLI session.
    """
    chatgpt = "chatgpt"
    """
    ChatGPT web or API.
    """
    claude_ai = "claude_ai"
    """
    Claude.ai web interface.
    """
    slack = "slack"
    """
    Slack message or thread.
    """
    browser = "browser"
    """
    Generic browser typing captured by extension.
    """
    native = "native"
    """
    Native macOS typing captured by agent.
    """
    manual = "manual"
    """
    Manually pasted or imported content.
    """


class FragmentType(str, Enum):
    """
    Type of content fragment.
    """

    message = "message"
    """
    A conversational message (user or assistant).
    """
    code = "code"
    """
    A code block or snippet.
    """
    note = "note"
    """
    A free-form text note.
    """
    command = "command"
    """
    A terminal command or shell invocation.
    """


class Role(str, Enum):
    """
    Role of the message author.
    """

    user = "user"
    """
    Human user input.
    """
    assistant = "assistant"
    """
    AI assistant response.
    """
    system = "system"
    """
    System prompt or context.
    """


class ConceptCategory(str, Enum):
    """
    Category of an extracted concept.
    """

    topic = "topic"
    """
    A subject or domain area.
    """
    theme = "theme"
    """
    A recurring pattern or tendency.
    """
    entity = "entity"
    """
    A named thing (tool, library, person, project).
    """
    question = "question"
    """
    An open question being explored.
    """
    decision = "decision"
    """
    A choice that was made.
    """
    tension = "tension"
    """
    A recurring conflict or trade-off.
    """


class InsightType(str, Enum):
    """
    Type of detected insight.
    """

    pattern = "pattern"
    """
    A recurring behavioral pattern.
    """
    connection = "connection"
    """
    A non-obvious link between concepts.
    """
    recurrence = "recurrence"
    """
    Something that keeps coming up across contexts.
    """
    evolution = "evolution"
    """
    A concept or position that changed over time.
    """


class WisdomType(str, Enum):
    """
    Type of synthesized wisdom card.
    """

    wisdom = "wisdom"
    """
    Grounded insight with evidence trail.
    """
    nothing_novel = "nothing_novel"
    """
    Refusal — graph did not support a grounded insight.
    """


class SuggestionType(str, Enum):
    """
    Type of proactive suggestion.
    """

    explore = "explore"
    """
    Suggest exploring a new direction.
    """
    revisit = "revisit"
    """
    Suggest revisiting a past topic.
    """
    connect = "connect"
    """
    Suggest connecting two unrelated concepts.
    """
    reconsider = "reconsider"
    """
    Suggest reconsidering a past decision.
    """


class SuggestionStatus(str, Enum):
    """
    Current status of a suggestion.
    """

    pending = "pending"
    """
    Not yet acted upon.
    """
    acted = "acted"
    """
    User explored or acted on this.
    """
    dismissed = "dismissed"
    """
    User dismissed this suggestion.
    """


class SourceType(str, Enum):
    """
    Classification of a chunk's source material.
    """

    conversation = "conversation"
    """
    Transcript of a dialogue (Claude Code, ChatGPT, etc.).
    """
    documentation = "documentation"
    """
    Docs, READMEs, guides.
    """
    spec = "spec"
    """
    Specification, protocol, RFC, ontology.
    """
    note = "note"
    """
    Free-form note or markdown.
    """
    unknown = "unknown"
    """
    Uncategorized source.
    """


class Identifiable(ConfiguredBaseModel):
    """
    Anything with a stable, globally unique identity. Aligned with BFO Independent Continuant: exists on its own, bears qualities, participates in processes.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://malleus.dev/schema",
            "mixin": True,
            "slot_usage": {"id": {"identifier": True, "name": "id", "required": True}},
        }
    )

    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )


class Temporal(ConfiguredBaseModel):
    """
    Timestamps for creation and last modification. Follows PROV-O temporal patterns.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({"from_schema": "https://malleus.dev/schema", "mixin": True})

    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Describable(ConfiguredBaseModel):
    """
    Human-readable description and metadata.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({"from_schema": "https://malleus.dev/schema", "mixin": True})

    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )


class Statusable(ConfiguredBaseModel):
    """
    Entity with a lifecycle status.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({"from_schema": "https://malleus.dev/schema", "mixin": True})

    status: Optional[EntityStatus] = Field(
        default=None,
        description="""Lifecycle status.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Statusable"]}},
    )


class Agent(ConfiguredBaseModel):
    """
    Capability of acting, deciding, or bearing responsibility. Aligned with PROV:Agent and BDI model. Agency is a trait, not a taxonomic position: an entity CAN act, it is not defined by acting. Apply this mixin to any entity class that needs volitional behavior.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({"from_schema": "https://malleus.dev/schema", "mixin": True})

    agent_type: Optional[str] = Field(
        default=None,
        description="""Classification of the agent. Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Agent"]}},
    )


class Entity(Describable, Temporal, Identifiable):
    """
    Root class for all identifiable, enduring things. Aligned with BFO:Independent Continuant. Every domain object that persists through time and needs identity extends this.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://malleus.dev/schema", "mixins": ["Identifiable", "Temporal", "Describable"]}
    )

    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )


class Event(Temporal, Identifiable):
    """
    Something that happens at a point or over an interval. Aligned with BFO:Occurrent / PROV:Activity. Instantaneous events use occurred_at. Processes with duration use started_at / ended_at (Allen's interval algebra).
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://malleus.dev/schema", "mixins": ["Identifiable", "Temporal"]}
    )

    event_type: str = Field(
        default=...,
        description="""Classification of the event. Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    occurred_at: Optional[datetime] = Field(
        default=None,
        description="""When the event happened (instantaneous events).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="""When a duration event began.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    ended_at: Optional[datetime] = Field(
        default=None,
        description="""When a duration event ended.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    caused_by: Optional[str] = Field(
        default=None,
        description="""ID of the event or agent that caused this event. Follows PROV-O wasInformedBy / wasAssociatedWith pattern.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Signal(Temporal, Identifiable):
    """
    A continuously derived quality that emerges from patterns of Events between Entities. Aligned with BFO:Specifically Dependent Continuant (Quality) and SSN/SOSA:Observation. A Signal inheres in its bearer(s) — it does not exist independently. It is computed, not asserted: derived on demand from the Event log and Entity graph via a named algorithm. Domain projects define signal types, algorithms, and interpretation semantics. The Signal class captures the universal pattern: something measurable that emerges from activity, has a current value, and is recomputable from the underlying data.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://malleus.dev/schema",
            "mixins": ["Identifiable", "Temporal"],
            "slot_usage": {
                "bearer_id": {
                    "description": "ID of the entity (or "
                    "relationship) this signal "
                    "inheres in. Required because a "
                    "Signal cannot exist without a "
                    "bearer (BFO: dependent "
                    "continuant).",
                    "name": "bearer_id",
                    "required": True,
                }
            },
        }
    )

    signal_type: str = Field(
        default=...,
        description="""Classification of the signal. Domain projects should constrain this to an enum. Examples: trust_score, health_score, centrality.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    value: Optional[float] = Field(
        default=None,
        description="""Current computed value. Interpretation is signal-type specific. Often 0.0–1.0 but not constrained at the root level (domain decides range and semantics).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    algorithm: Optional[str] = Field(
        default=None,
        description="""Name or reference of the computation that produces this signal. Domain projects should document algorithms and constrain this to an enum. Examples: appleseed, pagerank, ewma, linear_decay.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal", "ProposalBy"]}},
    )
    perspective: Optional[str] = Field(
        default=None,
        description="""ID of the entity from whose perspective this signal is computed. Null for global/objective signals (e.g., graph density). Set for subjective signals (e.g., trust computed from one agent's viewpoint).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    computed_at: Optional[datetime] = Field(
        default=None,
        description="""When this signal value was last computed.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    bearer_id: str = Field(
        default=...,
        description="""ID of the entity (or relationship) this signal inheres in. Required because a Signal cannot exist without a bearer (BFO: dependent continuant).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Relation(Temporal, Identifiable):
    """
    A typed, directed edge between two entities. Reified as a class so relations can carry metadata (strength, confidence, temporal validity).
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://malleus.dev/schema", "mixins": ["Identifiable", "Temporal"]}
    )

    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Source(Entity):
    """
    A capture source — where data was imported or streamed from (a user's ~/.claude/projects, a ChatGPT export, etc.).
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({"from_schema": "https://extended-thinking.dev/schema"})

    platform: Platform = Field(default=..., json_schema_extra={"linkml_meta": {"domain_of": ["Source"]}})
    url: Optional[str] = Field(
        default=None,
        description="""Optional URL or path to the source.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Source", "KnowledgeNode"]}},
    )
    captured_at: Optional[datetime] = Field(
        default=None,
        description="""When this source was first captured.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Source"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )


class Session(Event, Describable):
    """
    A conversation or interaction session — an Event because it happens over an interval, has a cause chain, and bounds a coherent episode.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Describable"]}
    )

    title: Optional[str] = Field(
        default=None,
        description="""Session title (auto-generated or user-provided).""",
        json_schema_extra={
            "linkml_meta": {"domain_of": ["Session", "Insight", "Wisdom", "Suggestion", "KnowledgeNode"]}
        },
    )
    source_id: Optional[str] = Field(
        default=None,
        description="""ID of the Source this session came from.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    message_count: Optional[int] = Field(default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Session"]}})
    metadata: Optional[str] = Field(
        default=None,
        description="""Additional metadata as JSON string.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Session"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    event_type: str = Field(
        default=...,
        description="""Classification of the event. Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    occurred_at: Optional[datetime] = Field(
        default=None,
        description="""When the event happened (instantaneous events).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="""When a duration event began.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    ended_at: Optional[datetime] = Field(
        default=None,
        description="""When a duration event ended.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    caused_by: Optional[str] = Field(
        default=None,
        description="""ID of the event or agent that caused this event. Follows PROV-O wasInformedBy / wasAssociatedWith pattern.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Fragment(Entity):
    """
    A discrete piece of content inside a Session — a message, code block, note, or command. The atomic unit of captured thinking.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({"from_schema": "https://extended-thinking.dev/schema"})

    content: str = Field(
        default=...,
        description="""The actual text content.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Fragment"]}},
    )
    role: Optional[Role] = Field(
        default=None,
        description="""Role of the author (for conversational fragments).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Fragment"]}},
    )
    fragment_type: FragmentType = Field(default=..., json_schema_extra={"linkml_meta": {"domain_of": ["Fragment"]}})
    position: Optional[int] = Field(
        default=None,
        description="""Ordering position within a session.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Fragment"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )


class Chunk(Entity):
    """
    An ingested chunk of memory from a MemoryProvider. Source-of-truth for provenance; referenced by Concept via HasProvenance edges.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({"from_schema": "https://extended-thinking.dev/schema"})

    source: Optional[str] = Field(
        default=None, description="""Source path or URL.""", json_schema_extra={"linkml_meta": {"domain_of": ["Chunk"]}}
    )
    source_type: Optional[SourceType] = Field(default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Chunk"]}})
    t_source_created: Optional[datetime] = Field(
        default=None,
        description="""When the user wrote the underlying content.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Chunk"]}},
    )
    t_ingested: Optional[datetime] = Field(
        default=None,
        description="""When ET ingested the chunk.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Chunk"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )


class Concept(Entity, Statusable):
    """
    An extracted topic, theme, entity, question, decision, or tension. Statusable tracks ACTIVE / deprecated lifecycle; other mixins come from Entity.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Statusable"]}
    )

    category: ConceptCategory = Field(default=..., json_schema_extra={"linkml_meta": {"domain_of": ["Concept"]}})
    frequency: Optional[int] = Field(
        default=None,
        description="""How many times this concept has been observed.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Concept"]}},
    )
    first_seen: Optional[datetime] = Field(default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Concept"]}})
    last_seen: Optional[datetime] = Field(default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Concept"]}})
    canonical_id: Optional[str] = Field(
        default=None,
        description="""When two concepts are resolved to the same entity, the non-canonical one points here at the canonical id. Null for active canonicals.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Concept"]}},
    )
    access_count: Optional[int] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Concept", "RelatesTo"]}}
    )
    last_accessed: Optional[datetime] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Concept", "RelatesTo"]}}
    )
    source_quote: Optional[str] = Field(
        default=None,
        description="""The user's exact words that demonstrate this concept.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Concept"]}},
    )
    status: Optional[EntityStatus] = Field(
        default=None,
        description="""Lifecycle status.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Statusable"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )


class Insight(Signal, Describable):
    """
    A detected pattern, connection, recurrence, or evolution. A Signal because it is a derived quality that emerges from patterns of events (extractions, relationships) across Entities (concepts, sessions).
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Describable"]}
    )

    title: str = Field(
        default=...,
        json_schema_extra={
            "linkml_meta": {"domain_of": ["Session", "Insight", "Wisdom", "Suggestion", "KnowledgeNode"]}
        },
    )
    insight_type: InsightType = Field(default=..., json_schema_extra={"linkml_meta": {"domain_of": ["Insight"]}})
    confidence: Optional[float] = Field(
        default=None,
        description="""Confidence score (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Insight"]}},
    )
    detected_at: Optional[datetime] = Field(default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Insight"]}})
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    signal_type: str = Field(
        default=...,
        description="""Classification of the signal. Domain projects should constrain this to an enum. Examples: trust_score, health_score, centrality.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    value: Optional[float] = Field(
        default=None,
        description="""Current computed value. Interpretation is signal-type specific. Often 0.0–1.0 but not constrained at the root level (domain decides range and semantics).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    algorithm: Optional[str] = Field(
        default=None,
        description="""Name or reference of the computation that produces this signal. Domain projects should document algorithms and constrain this to an enum. Examples: appleseed, pagerank, ewma, linear_decay.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal", "ProposalBy"]}},
    )
    perspective: Optional[str] = Field(
        default=None,
        description="""ID of the entity from whose perspective this signal is computed. Null for global/objective signals (e.g., graph density). Set for subjective signals (e.g., trust computed from one agent's viewpoint).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    computed_at: Optional[datetime] = Field(
        default=None,
        description="""When this signal value was last computed.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    bearer_id: str = Field(
        default=...,
        description="""ID of the entity (or relationship) this signal inheres in. Required because a Signal cannot exist without a bearer (BFO: dependent continuant).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Wisdom(Signal, Statusable, Describable):
    """
    A synthesized wisdom card generated by the Opus pass over the graph. Statusable because wisdoms flow pending → seen → dismissed.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Describable", "Statusable"]}
    )

    title: str = Field(
        default=...,
        json_schema_extra={
            "linkml_meta": {"domain_of": ["Session", "Insight", "Wisdom", "Suggestion", "KnowledgeNode"]}
        },
    )
    wisdom_type: WisdomType = Field(default=..., json_schema_extra={"linkml_meta": {"domain_of": ["Wisdom"]}})
    based_on_sessions: Optional[int] = Field(default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Wisdom"]}})
    based_on_concepts: Optional[int] = Field(default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Wisdom"]}})
    related_concept_ids: Optional[str] = Field(
        default=None,
        description="""JSON array of concept ids this wisdom cites.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Wisdom"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    status: Optional[EntityStatus] = Field(
        default=None,
        description="""Lifecycle status.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Statusable"]}},
    )
    signal_type: str = Field(
        default=...,
        description="""Classification of the signal. Domain projects should constrain this to an enum. Examples: trust_score, health_score, centrality.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    value: Optional[float] = Field(
        default=None,
        description="""Current computed value. Interpretation is signal-type specific. Often 0.0–1.0 but not constrained at the root level (domain decides range and semantics).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    algorithm: Optional[str] = Field(
        default=None,
        description="""Name or reference of the computation that produces this signal. Domain projects should document algorithms and constrain this to an enum. Examples: appleseed, pagerank, ewma, linear_decay.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal", "ProposalBy"]}},
    )
    perspective: Optional[str] = Field(
        default=None,
        description="""ID of the entity from whose perspective this signal is computed. Null for global/objective signals (e.g., graph density). Set for subjective signals (e.g., trust computed from one agent's viewpoint).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    computed_at: Optional[datetime] = Field(
        default=None,
        description="""When this signal value was last computed.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    bearer_id: str = Field(
        default=...,
        description="""ID of the entity (or relationship) this signal inheres in. Required because a Signal cannot exist without a bearer (BFO: dependent continuant).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Suggestion(Signal, Statusable, Describable):
    """
    A proactive recommendation — something the system thinks the user should explore, revisit, connect, or reconsider.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Describable", "Statusable"]}
    )

    title: str = Field(
        default=...,
        json_schema_extra={
            "linkml_meta": {"domain_of": ["Session", "Insight", "Wisdom", "Suggestion", "KnowledgeNode"]}
        },
    )
    suggestion_type: SuggestionType = Field(
        default=..., json_schema_extra={"linkml_meta": {"domain_of": ["Suggestion"]}}
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    status: Optional[EntityStatus] = Field(
        default=None,
        description="""Lifecycle status.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Statusable"]}},
    )
    signal_type: str = Field(
        default=...,
        description="""Classification of the signal. Domain projects should constrain this to an enum. Examples: trust_score, health_score, centrality.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    value: Optional[float] = Field(
        default=None,
        description="""Current computed value. Interpretation is signal-type specific. Often 0.0–1.0 but not constrained at the root level (domain decides range and semantics).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    algorithm: Optional[str] = Field(
        default=None,
        description="""Name or reference of the computation that produces this signal. Domain projects should document algorithms and constrain this to an enum. Examples: appleseed, pagerank, ewma, linear_decay.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal", "ProposalBy"]}},
    )
    perspective: Optional[str] = Field(
        default=None,
        description="""ID of the entity from whose perspective this signal is computed. Null for global/objective signals (e.g., graph density). Set for subjective signals (e.g., trust computed from one agent's viewpoint).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    computed_at: Optional[datetime] = Field(
        default=None,
        description="""When this signal value was last computed.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    bearer_id: str = Field(
        default=...,
        description="""ID of the entity (or relationship) this signal inheres in. Required because a Signal cannot exist without a bearer (BFO: dependent continuant).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Rationale(Signal, Describable):
    """
    An LLM-generated justification attached to a subject node. Uses malleus Signal's bearer_id to point at the subject; cited_node_ids enumerates the evidence. The grounded-rationale guarantee (ADR 013 C4 / R8) requires every citation to resolve before commit — an ungrounded rationale cannot enter the graph.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Describable"]}
    )

    text: str = Field(
        default=...,
        description="""The rationale text, verbatim as produced by the LLM.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Rationale"]}},
    )
    cited_node_ids: Optional[str] = Field(
        default=None,
        description="""JSON array of node ids the rationale cites as evidence.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Rationale"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    signal_type: str = Field(
        default=...,
        description="""Classification of the signal. Domain projects should constrain this to an enum. Examples: trust_score, health_score, centrality.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    value: Optional[float] = Field(
        default=None,
        description="""Current computed value. Interpretation is signal-type specific. Often 0.0–1.0 but not constrained at the root level (domain decides range and semantics).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    algorithm: Optional[str] = Field(
        default=None,
        description="""Name or reference of the computation that produces this signal. Domain projects should document algorithms and constrain this to an enum. Examples: appleseed, pagerank, ewma, linear_decay.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal", "ProposalBy"]}},
    )
    perspective: Optional[str] = Field(
        default=None,
        description="""ID of the entity from whose perspective this signal is computed. Null for global/objective signals (e.g., graph density). Set for subjective signals (e.g., trust computed from one agent's viewpoint).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    computed_at: Optional[datetime] = Field(
        default=None,
        description="""When this signal value was last computed.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    bearer_id: str = Field(
        default=...,
        description="""ID of the entity (or relationship) this signal inheres in. Required because a Signal cannot exist without a bearer (BFO: dependent continuant).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class RelatesTo(Relation):
    """
    Concept-to-concept semantic relation. Weighted, with optional context quote describing why they relate. Primary edge in the cognitive graph.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://extended-thinking.dev/schema",
            "slot_usage": {
                "source_id": {"name": "source_id", "range": "Concept"},
                "target_id": {"name": "target_id", "range": "Concept"},
            },
        }
    )

    weight: Optional[float] = Field(
        default=None,
        description="""Edge strength (accumulates on repeat extraction).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["RelatesTo"]}},
    )
    context: Optional[str] = Field(
        default=None,
        description="""Free-text explanation of the relation.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["RelatesTo"]}},
    )
    edge_type: Optional[str] = Field(
        default=None,
        description="""Sub-kind of RelatesTo (e.g. same-chunk, cross-session).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["RelatesTo"]}},
    )
    access_count: Optional[int] = Field(
        default=None,
        description="""Number of times this edge has been traversed. Drives Physarum decay — frequently traversed edges stay strong, idle edges fade.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Concept", "RelatesTo"]}},
    )
    last_accessed: Optional[datetime] = Field(
        default=None,
        description="""Timestamp of the most recent access.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Concept", "RelatesTo"]}},
    )
    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class InformedBy(Relation):
    """
    A Wisdom is informed by Concepts. Provenance trail for a synthesized insight back to its evidence.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://extended-thinking.dev/schema",
            "slot_usage": {
                "source_id": {"name": "source_id", "range": "Wisdom"},
                "target_id": {"name": "target_id", "range": "Concept"},
            },
        }
    )

    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class HasProvenance(Relation):
    """
    Concept-to-Chunk provenance edge. Records which chunk (and which LLM extraction pass) produced this concept.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://extended-thinking.dev/schema",
            "slot_usage": {
                "source_id": {"name": "source_id", "range": "Concept"},
                "target_id": {"name": "target_id", "range": "Chunk"},
            },
        }
    )

    source_provider: Optional[str] = Field(
        default=None,
        description="""Name of the MemoryProvider that supplied the chunk.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["HasProvenance"]}},
    )
    llm_model: Optional[str] = Field(
        default=None,
        description="""Model id that performed the extraction.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["HasProvenance"]}},
    )
    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Supersedes(Relation):
    """
    A concept supersedes another — e.g. the user changed their mind and the older stance is deprecated. Drives the t_superseded_by bitemporal pointer for truth maintenance (ADR 002).
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://extended-thinking.dev/schema",
            "slot_usage": {
                "source_id": {"name": "source_id", "range": "Concept"},
                "target_id": {"name": "target_id", "range": "Concept"},
            },
        }
    )

    reason: Optional[str] = Field(
        default=None,
        description="""Why the newer concept supersedes the older one.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Supersedes"]}},
    )
    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class ProposalBy(Relation):
    """
    Algorithm write-back edge (ADR 013 C7). Records that at time T, plugin P proposed the source→target connection with score S under scope parameters Q. Distinct from committed edges like RelatesTo: a proposal captures what the algorithm said, not what the user or consumer accepted. For MVP this is pinned Concept→Concept; other endpoint pairs land as new subclasses when a consumer needs them.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://extended-thinking.dev/schema",
            "slot_usage": {
                "source_id": {"name": "source_id", "range": "Concept"},
                "target_id": {"name": "target_id", "range": "Concept"},
            },
        }
    )

    algorithm: str = Field(
        default=...,
        description="""Plugin name (e.g. 'weighted_bfs', 'embedding_similarity').""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal", "ProposalBy"]}},
    )
    parameters_json: Optional[str] = Field(
        default=None,
        description="""JSON-encoded parameters used for this invocation.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["ProposalBy"]}},
    )
    invoked_at: datetime = Field(
        default=...,
        description="""When the algorithm was invoked.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["ProposalBy"]}},
    )
    score: Optional[float] = Field(
        default=None,
        description="""Algorithm-specific score attached to this proposal.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["ProposalBy"]}},
    )
    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class KnowledgeNode(Signal, Describable):
    """
    An external knowledge item (Wikipedia article, arXiv paper, Fowler refactoring, etc.) attached to a user-facing concept. Signal because it is a derived quality — the source's representation of a topic — inhering in its bearer concept via Signal.bearer_id. Namespace convention: `enrichment:<source_kind>` (ADR 011 v2).
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Describable"]}
    )

    source_kind: str = Field(
        default=...,
        description="""Canonical source name ('wikipedia', 'arxiv', 'fowler', ...).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["KnowledgeNode", "EnrichmentRun"]}},
    )
    external_id: str = Field(
        default=...,
        description="""Source-native id (Wikidata QID, arXiv ID, Fowler slug).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["KnowledgeNode"]}},
    )
    url: Optional[str] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Source", "KnowledgeNode"]}}
    )
    title: str = Field(
        default=...,
        json_schema_extra={
            "linkml_meta": {"domain_of": ["Session", "Insight", "Wisdom", "Suggestion", "KnowledgeNode"]}
        },
    )
    abstract: Optional[str] = Field(
        default=None,
        description="""Text representation indexed for semantic recall.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["KnowledgeNode"]}},
    )
    theme: Optional[str] = Field(
        default=None,
        description="""JSON array of theme tags for sub-classification within a source. Multi-theme membership allowed. Source plugins populate this (Wikipedia via LLM classifier, arXiv via native category IDs, etc.). Empty means unclassified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["KnowledgeNode"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    signal_type: str = Field(
        default=...,
        description="""Classification of the signal. Domain projects should constrain this to an enum. Examples: trust_score, health_score, centrality.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    value: Optional[float] = Field(
        default=None,
        description="""Current computed value. Interpretation is signal-type specific. Often 0.0–1.0 but not constrained at the root level (domain decides range and semantics).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    algorithm: Optional[str] = Field(
        default=None,
        description="""Name or reference of the computation that produces this signal. Domain projects should document algorithms and constrain this to an enum. Examples: appleseed, pagerank, ewma, linear_decay.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal", "ProposalBy"]}},
    )
    perspective: Optional[str] = Field(
        default=None,
        description="""ID of the entity from whose perspective this signal is computed. Null for global/objective signals (e.g., graph density). Set for subjective signals (e.g., trust computed from one agent's viewpoint).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    computed_at: Optional[datetime] = Field(
        default=None,
        description="""When this signal value was last computed.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    bearer_id: str = Field(
        default=...,
        description="""ID of the entity (or relationship) this signal inheres in. Required because a Signal cannot exist without a bearer (BFO: dependent continuant).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Signal"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class Enriches(Relation):
    """
    Connects a user-facing node to external knowledge (ADR 011 v2). FROM spans Concept / Wisdom for now; consumer ontologies extend via slot_usage delta to include their own typed nodes.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://extended-thinking.dev/schema",
            "slot_usage": {
                "source_id": {"name": "source_id", "range": "Concept"},
                "target_id": {"name": "target_id", "range": "KnowledgeNode"},
            },
        }
    )

    relevance: Optional[float] = Field(
        default=None,
        description="""Final score from the gate sequence (0..1).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Enriches", "WisdomEnriches"]}},
    )
    trigger: Optional[str] = Field(
        default=None,
        description="""Which trigger fired (frequency_threshold, cluster_formed, ...).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Enriches", "WisdomEnriches"]}},
    )
    gate_verdicts: Optional[str] = Field(
        default=None,
        description="""JSON array of per-gate verdicts {plugin, score, outcome}.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Enriches", "WisdomEnriches"]}},
    )
    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class WisdomEnriches(Relation):
    """
    Wisdom → KnowledgeNode variant of Enriches. Separate subclass because Kuzu's REL DDL requires FROM/TO types declared per REL table. Future consumer subclasses (HypothesisEnriches etc.) follow the same pattern (ADR 011 v2 multi-pair decision).
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {
            "from_schema": "https://extended-thinking.dev/schema",
            "slot_usage": {
                "source_id": {"name": "source_id", "range": "Wisdom"},
                "target_id": {"name": "target_id", "range": "KnowledgeNode"},
            },
        }
    )

    relevance: Optional[float] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Enriches", "WisdomEnriches"]}}
    )
    trigger: Optional[str] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Enriches", "WisdomEnriches"]}}
    )
    gate_verdicts: Optional[str] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["Enriches", "WisdomEnriches"]}}
    )
    relation_type: str = Field(
        default=...,
        description="""The type of relation (has_part, contains, depends_on, etc.). Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    source_id: str = Field(
        default=...,
        description="""ID of the source entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation", "Session"]}},
    )
    target_id: str = Field(
        default=...,
        description="""ID of the target entity.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    strength: Optional[float] = Field(
        default=None,
        description="""Weight or confidence of the relation (0.0 to 1.0).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Relation"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


class EnrichmentRun(Event, Describable):
    """
    Telemetry for a single enrichment invocation (ADR 011 v2). One node per trigger-fire-per-source-per-concept. Queryable via et_shift(node_types=[\"EnrichmentRun\"]) so users and algorithms can tune thresholds based on real data rather than guesswork.
    """

    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta(
        {"from_schema": "https://extended-thinking.dev/schema", "mixins": ["Describable"]}
    )

    trigger_name: str = Field(default=..., json_schema_extra={"linkml_meta": {"domain_of": ["EnrichmentRun"]}})
    source_kind: str = Field(
        default=..., json_schema_extra={"linkml_meta": {"domain_of": ["KnowledgeNode", "EnrichmentRun"]}}
    )
    concept_id: str = Field(
        default=...,
        description="""The user-facing node whose context fired the enrichment.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["EnrichmentRun"]}},
    )
    candidates_returned: Optional[int] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["EnrichmentRun"]}}
    )
    candidates_accepted: Optional[int] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["EnrichmentRun"]}}
    )
    gate_trace: Optional[str] = Field(
        default=None,
        description="""JSON: per-gate {counts_in, counts_out, score_histogram}.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["EnrichmentRun"]}},
    )
    duration_ms: Optional[int] = Field(
        default=None, json_schema_extra={"linkml_meta": {"domain_of": ["EnrichmentRun"]}}
    )
    error: Optional[str] = Field(
        default=None,
        description="""Non-empty if the run failed (e.g. Wikipedia 5xx). Enables retry queries.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["EnrichmentRun"]}},
    )
    description: Optional[str] = Field(
        default=None,
        description="""Free-text description.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="""Arbitrary classification tags.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Describable"]}},
    )
    event_type: str = Field(
        default=...,
        description="""Classification of the event. Domain projects should constrain this to an enum.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    occurred_at: Optional[datetime] = Field(
        default=None,
        description="""When the event happened (instantaneous events).""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="""When a duration event began.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    ended_at: Optional[datetime] = Field(
        default=None,
        description="""When a duration event ended.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    caused_by: Optional[str] = Field(
        default=None,
        description="""ID of the event or agent that caused this event. Follows PROV-O wasInformedBy / wasAssociatedWith pattern.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Event"]}},
    )
    id: str = Field(
        default=...,
        description="""Globally unique identifier.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    name: Optional[str] = Field(
        default=None,
        description="""Human-readable name.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Identifiable"]}},
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="""When this was created.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="""When this was last modified.""",
        json_schema_extra={"linkml_meta": {"domain_of": ["Temporal"]}},
    )


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Identifiable.model_rebuild()
Temporal.model_rebuild()
Describable.model_rebuild()
Statusable.model_rebuild()
Agent.model_rebuild()
Entity.model_rebuild()
Event.model_rebuild()
Signal.model_rebuild()
Relation.model_rebuild()
Source.model_rebuild()
Session.model_rebuild()
Fragment.model_rebuild()
Chunk.model_rebuild()
Concept.model_rebuild()
Insight.model_rebuild()
Wisdom.model_rebuild()
Suggestion.model_rebuild()
Rationale.model_rebuild()
RelatesTo.model_rebuild()
InformedBy.model_rebuild()
HasProvenance.model_rebuild()
Supersedes.model_rebuild()
ProposalBy.model_rebuild()
KnowledgeNode.model_rebuild()
Enriches.model_rebuild()
WisdomEnriches.model_rebuild()
EnrichmentRun.model_rebuild()
