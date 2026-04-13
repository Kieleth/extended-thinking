"""Typed Kuzu accessors generated from schema/extended_thinking.yaml (ADR 013).

DO NOT EDIT. Regenerate with:
    python scripts/gen_kuzu_types.py

Bridges pydantic domain types (`extended_thinking._schema.models`) with Kuzu's
storage layer. Pydantic stays the single source of truth for shape and
validation; this module adds Kuzu-specific serialization (renames,
system columns, enum coercion).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeVar

from extended_thinking._schema import models as _m


T = TypeVar("T")


# ── Generated registries ──────────────────────────────────────────────

NODE_TYPES: tuple[type, ...] = (_m.Chunk, _m.Concept, _m.EnrichmentRun, _m.Fragment, _m.Insight, _m.KnowledgeNode, _m.Rationale, _m.Session, _m.Source, _m.Suggestion, _m.Wisdom,)


EDGE_TYPES: tuple[type, ...] = (_m.Enriches, _m.HasProvenance, _m.InformedBy, _m.ProposalBy, _m.RelatesTo, _m.Supersedes, _m.WisdomEnriches,)


KUZU_TABLE: dict[type, str] = {
    _m.Chunk: 'Chunk',
    _m.Concept: 'Concept',
    _m.EnrichmentRun: 'EnrichmentRun',
    _m.Fragment: 'Fragment',
    _m.Insight: 'Insight',
    _m.KnowledgeNode: 'KnowledgeNode',
    _m.Rationale: 'Rationale',
    _m.Session: 'Session',
    _m.Source: 'Source',
    _m.Suggestion: 'Suggestion',
    _m.Wisdom: 'Wisdom',
    _m.Enriches: 'Enriches',
    _m.HasProvenance: 'HasProvenance',
    _m.InformedBy: 'InformedBy',
    _m.ProposalBy: 'ProposalBy',
    _m.RelatesTo: 'RelatesTo',
    _m.Supersedes: 'Supersedes',
    _m.WisdomEnriches: 'WisdomEnriches',
}


# LinkML slot name → Kuzu column name. Present only for classes that have
# at least one renamed slot (e.g., Kuzu's reserved `description`).
COLUMN_RENAMES: dict[type, dict[str, str]] = {
    _m.Chunk: {'description': 'desc_text'},
    _m.Concept: {'description': 'desc_text'},
    _m.EnrichmentRun: {'description': 'desc_text'},
    _m.Fragment: {'description': 'desc_text'},
    _m.Insight: {'description': 'desc_text'},
    _m.KnowledgeNode: {'description': 'desc_text'},
    _m.Rationale: {'description': 'desc_text'},
    _m.Session: {'description': 'desc_text'},
    _m.Source: {'description': 'desc_text'},
    _m.Suggestion: {'description': 'desc_text'},
    _m.Wisdom: {'description': 'desc_text'},
}


SYSTEM_FIELDS_NODE: tuple[str, ...] = ('t_valid_from', 't_valid_to', 't_created', 't_expired', 't_superseded_by', 'namespace', 'et_source', 'vectors_pending')
SYSTEM_FIELDS_EDGE: tuple[str, ...] = ('t_valid_from', 't_valid_to', 't_created', 't_expired', 't_superseded_by', 'namespace', 'et_source')


# ── Serialization helpers ─────────────────────────────────────────────

def _scalarize(v: Any) -> Any:
    """Coerce Python values Pydantic hands us into Kuzu-friendly scalars.

    - datetime/date → ISO-8601 string (Kuzu columns for these are STRING)
    - Enum          → its string value
    - list          → JSON-encoded string (Kuzu has no array column type
                      for arbitrary Python objects; the schema-level list
                      slots get serialized here)
    """
    import json as _json
    if v is None:
        return v
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, datetime):
        # Preserve tz info when present
        return v.isoformat()
    if hasattr(v, "isoformat") and callable(v.isoformat):
        return v.isoformat()
    if isinstance(v, list):
        return _json.dumps([_scalarize(x) for x in v])
    return v


def _unscalarize(value: Any, target_type: Any) -> Any:
    """Best-effort reverse of `_scalarize` when reading from Kuzu."""
    if value is None or value == "":
        return None
    # Enum coercion: if the pydantic field is an Enum subclass, restore it.
    # Unknown enum values fall through to the raw string — no silent swallow,
    # the return is the semantic outcome of a failed enum round-trip.
    try:
        if isinstance(target_type, type) and issubclass(target_type, Enum):
            return target_type(value)
    except (ValueError, TypeError):
        return value
    return value


def to_kuzu_row(
    obj,
    *,
    namespace: str = "default",
    source: str = "",
) -> dict[str, Any]:
    """Serialize a typed domain instance to a dict ready for Kuzu insert.

    Adds ET system columns (bitemporal timestamps, namespace, source,
    vectors_pending for nodes). Applies column renames. Coerces Python
    types to Kuzu-storable scalars.
    """
    cls = type(obj)
    if cls not in KUZU_TABLE:
        raise ValueError(
            f"{cls.__name__} is not a registered typed-node or typed-edge class; "
            f"only classes under schema/extended_thinking.yaml participate."
        )

    # pydantic v2 — .model_dump() emits a dict with field names.
    raw = obj.model_dump(exclude_none=False)

    out: dict[str, Any] = {}
    renames = COLUMN_RENAMES.get(cls, {})
    for k, v in raw.items():
        kuzu_k = renames.get(k, k)
        out[kuzu_k] = _scalarize(v)

    now = datetime.now(timezone.utc).isoformat()
    system_defaults = {
        "t_valid_from": now,
        "t_valid_to": "",
        "t_created": now,
        "t_expired": "",
        "t_superseded_by": "",
        "namespace": namespace,
        "et_source": source,
    }
    if cls in NODE_TYPES:
        system_defaults["vectors_pending"] = True

    # Don't overwrite a user-provided value for system fields.
    for k, v in system_defaults.items():
        out.setdefault(k, v)

    return out


def from_kuzu_row(cls: type[T], row: dict) -> T:
    """Parse a Kuzu query result row back into a domain instance.

    Drops system columns, reverses renames, reconstructs enums. The caller
    must pass the correct `cls` — Kuzu doesn't label rows with their
    pydantic type.
    """
    if cls not in KUZU_TABLE:
        raise ValueError(f"{cls.__name__} is not a registered typed class")
    renames = COLUMN_RENAMES.get(cls, {})
    reverse = {v: k for k, v in renames.items()}

    d = dict(row)
    # Drop Kuzu internal labels
    d.pop("_id", None)
    d.pop("_label", None)
    # Drop system columns
    for sys in SYSTEM_FIELDS_NODE + SYSTEM_FIELDS_EDGE:
        d.pop(sys, None)
    # Reverse renames
    for kuzu_k, ll_k in reverse.items():
        if kuzu_k in d:
            d[ll_k] = d.pop(kuzu_k)

    # Pydantic handles the remainder: enums, datetime parsing, required checks.
    return cls.model_validate(d)


def edge_endpoints(obj) -> tuple[str, str, dict[str, Any]]:
    """For edge instances: return (source_id, target_id, property_dict).

    The property dict is suitable for a Cypher CREATE ... {props} clause;
    `source_id` and `target_id` are used in the MATCH clause instead.
    """
    cls = type(obj)
    if cls not in EDGE_TYPES:
        raise ValueError(f"{cls.__name__} is not a registered edge type")
    props = to_kuzu_row(obj)
    src = props.pop("source_id")
    tgt = props.pop("target_id")
    return src, tgt, props
