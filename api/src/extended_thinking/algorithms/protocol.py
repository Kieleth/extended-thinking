"""Algorithm protocol for K+W layer plugins (ADR 003).

Every pluggable algorithm implements this protocol. The contract is
deliberately minimal: metadata for discovery, a context for inputs,
a run() method that returns a family-specific result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable


@dataclass
class AlgorithmMeta:
    """Static metadata about an algorithm. Used by registry + et_catalog."""

    name: str                      # unique identifier, e.g. "physarum"
    family: str                    # e.g. "decay", "bow_tie", "recombination"
    description: str               # one-line purpose
    paper_citation: str            # required research reference
    parameters: dict[str, Any] = field(default_factory=dict)  # defaults + types
    temporal_aware: bool = False   # honors as_of?


@dataclass
class AlgorithmContext:
    """Inputs available to every algorithm at run time.

    Algorithms are free to ignore fields they don't need (e.g., an entity
    resolver may not care about `vectors` or `as_of`).

    ADR 013 C2 — `namespace` scopes queries and writes to a subset of the
    graph. `None` means unscoped (admin/debug). Built-in memory-side
    algorithms default to `"memory"`; programmatic consumers pass their
    own namespace.
    """

    kg: Any                        # GraphStore instance
    vectors: Any = None            # VectorStore | None
    as_of: str | None = None       # ISO date for point-in-time queries (ADR 002)
    namespace: str | None = None   # ADR 013 C2 — scope for this run
    params: dict[str, Any] = field(default_factory=dict)  # merged config + overrides
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@runtime_checkable
class Algorithm(Protocol):
    """Contract every K+W plugin implements.

    Name and meta are class attributes so discovery doesn't require instantiation.
    `run()` is instance-level so algorithms can hold configured state.
    """

    meta: AlgorithmMeta

    def run(self, context: AlgorithmContext) -> Any:
        """Execute the algorithm against the given context.

        Return type is family-specific (each family documents its shape).
        Common patterns:
          - decay: returns None (mutates edge weights in kg)
          - activation: returns list[tuple[concept_id, score]]
          - bow_tie: returns list of core concept dicts
          - recombination: returns list of candidate cross-cluster connections
        """
        ...
