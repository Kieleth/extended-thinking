#!/usr/bin/env python3
"""LinkML → Kuzu DDL codegen (ADR 013, Phase 0).

Reads `schema/extended_thinking.yaml` via LinkML SchemaView (imports resolved
against `schema/imports/malleus.yaml`), emits Kuzu CREATE TABLE statements
for every class in the ET ontology, and writes them to
`schema/generated/kuzu_ddl.py`.

Usage:
    python scripts/gen_kuzu.py

Output module exports:
    EXTENDED_THINKING_DDL : list[str]
        Ordered CREATE NODE TABLE / CREATE REL TABLE statements.
    NODE_TABLES / EDGE_TABLES : list[str]
        Names of generated tables.
    COLUMN_MAPPING : dict[str, dict[str, str]]
        Per-table LinkML slot name → Kuzu column name. Non-empty only for
        tables that had slots renamed to dodge Kuzu reserved words.

Design notes:
  - Reserved words we remap: `description` → `desc_text`. The set is a
    runtime-extensible dict (_KUZU_RENAME_MAP) so new collisions land as
    one-line additions.
  - System columns (bitemporal + namespace + source + vectors_pending)
    are appended to every table. User slots may not collide with them;
    collision raises at generation time with a pointer to the yaml.
  - Edge tables resolve FROM/TO from `slot_usage.source_id.range` and
    `slot_usage.target_id.range`. An edge subclass with no such pinning
    is an error — ET edges are typed directed, not free-form.
  - Primary keys: nodes use `id` (from Identifiable mixin). Edge primary
    keys are implicit in Kuzu.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

from linkml_runtime.utils.schemaview import SchemaView


# ── Paths ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "extended_thinking.yaml"
OUTPUT_PATH = REPO_ROOT / "schema" / "generated" / "kuzu_ddl.py"


# ── Type + name maps ─────────────────────────────────────────────────

# LinkML base types → Kuzu column types.
# Everything not listed (enums, class references, unknown) is stored as STRING.
_LINKML_TO_KUZU: dict[str, str] = {
    "string": "STRING",
    "integer": "INT64",
    "float": "DOUBLE",
    "double": "DOUBLE",
    "decimal": "DOUBLE",
    "boolean": "BOOL",
    "datetime": "STRING",
    "date": "STRING",
    "time": "STRING",
    "uri": "STRING",
    "uriorcurie": "STRING",
    "curie": "STRING",
}

# Slots whose name collides with a Kuzu reserved word. Extend if the parser
# rejects more names.
_KUZU_RENAME_MAP: dict[str, str] = {
    "description": "desc_text",
}

# System columns every typed node gets. ADR 002 (bitemporal) + ADR 013 (
# namespace, vectors_pending, et_source).
#
# `et_source` records which consumer wrote the row (e.g. "autoresearch-et",
# "memory-pipeline"). Named with the `et_` prefix to dodge collision with
# domain slots (Relation has `source_id`; Source is a class name; user
# classes could declare a `source` slot). Kuzu-level only — the pydantic
# domain types don't see it.
_NODE_SYSTEM_COLUMNS: dict[str, str] = {
    "t_valid_from": "STRING",
    "t_valid_to": "STRING",
    "t_created": "STRING",
    "t_expired": "STRING",
    "t_superseded_by": "STRING",
    "namespace": "STRING",
    "et_source": "STRING",
    "vectors_pending": "BOOL",
}

# System columns on edges. Same bitemporal + namespace + et_source axes,
# no `vectors_pending` (edges are not independently vector-indexed).
_EDGE_SYSTEM_COLUMNS: dict[str, str] = {
    "t_valid_from": "STRING",
    "t_valid_to": "STRING",
    "t_created": "STRING",
    "t_expired": "STRING",
    "t_superseded_by": "STRING",
    "namespace": "STRING",
    "et_source": "STRING",
}


# ── Classification ───────────────────────────────────────────────────

def _ancestors(sv: SchemaView, class_name: str) -> list[str]:
    """Full ancestor chain (excluding self)."""
    return [a for a in sv.class_ancestors(class_name) if a != class_name]


def _is_node_class(sv: SchemaView, class_name: str) -> bool:
    """A node descends from Entity, Event, or Signal."""
    roots = {"Entity", "Event", "Signal"}
    return any(a in roots for a in _ancestors(sv, class_name))


def _is_edge_class(sv: SchemaView, class_name: str) -> bool:
    """An edge descends from Relation."""
    return "Relation" in _ancestors(sv, class_name)


def _is_abstract_or_mixin(sv: SchemaView, class_name: str) -> bool:
    cls = sv.get_class(class_name)
    return bool(getattr(cls, "mixin", False) or getattr(cls, "abstract", False))


# ── Column generation ────────────────────────────────────────────────

def _kuzu_type_for_slot(sv: SchemaView, slot) -> str:
    """Map a LinkML slot's range to a Kuzu column type."""
    rng = slot.range or "string"
    if rng in _LINKML_TO_KUZU:
        return _LINKML_TO_KUZU[rng]
    # Enums → STRING. Class references → STRING (holds id).
    return "STRING"


def _kuzu_col_name(slot_name: str) -> str:
    """Rename slots whose names collide with Kuzu reserved words."""
    return _KUZU_RENAME_MAP.get(slot_name, slot_name)


def _user_columns_for_class(sv: SchemaView, class_name: str) -> list[tuple[str, str]]:
    """List of (kuzu_col_name, kuzu_type) from all induced slots.

    Excludes slots whose names would collide with system columns — those are
    a hard error; fix the yaml.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for slot in sv.class_induced_slots(class_name):
        kuzu_name = _kuzu_col_name(slot.name)
        if kuzu_name in _NODE_SYSTEM_COLUMNS or kuzu_name in _EDGE_SYSTEM_COLUMNS:
            raise ValueError(
                f"{class_name}.{slot.name} collides with a system column "
                f"(Kuzu name: {kuzu_name!r}). Rename the slot in the LinkML "
                f"or extend _KUZU_RENAME_MAP."
            )
        if kuzu_name in seen:
            continue  # mixin overlap — keep first occurrence
        seen.add(kuzu_name)
        out.append((kuzu_name, _kuzu_type_for_slot(sv, slot)))
    return out


def _column_renames(sv: SchemaView, class_name: str) -> dict[str, str]:
    """LinkML slot name → Kuzu column name, only for renamed slots."""
    renames: dict[str, str] = {}
    for slot in sv.class_induced_slots(class_name):
        kuzu = _kuzu_col_name(slot.name)
        if kuzu != slot.name:
            renames[slot.name] = kuzu
    return renames


# ── DDL emitters ─────────────────────────────────────────────────────

def _emit_node_table(sv: SchemaView, class_name: str) -> str:
    user_cols = _user_columns_for_class(sv, class_name)
    sys_cols = list(_NODE_SYSTEM_COLUMNS.items())

    # Kuzu DDL parser rejects inline comments, so the system-column divider
    # stays outside the statement string.
    lines = [f"    {n} {t}" for n, t in user_cols + sys_cols]
    lines += ["    PRIMARY KEY(id)"]
    body = ",\n".join(lines)
    return (
        f"CREATE NODE TABLE IF NOT EXISTS {class_name}(\n"
        f"{body}\n"
        f")"
    )


def _resolve_edge_endpoints(sv: SchemaView, class_name: str) -> tuple[str, str]:
    """Read slot_usage.source_id.range and slot_usage.target_id.range."""
    cls = sv.get_class(class_name)
    if not cls or not cls.slot_usage:
        raise ValueError(
            f"edge class {class_name!r} must pin source_id/target_id "
            f"ranges via slot_usage to define FROM/TO"
        )
    src = cls.slot_usage.get("source_id")
    tgt = cls.slot_usage.get("target_id")
    if not src or not src.range:
        raise ValueError(
            f"edge class {class_name!r} is missing slot_usage.source_id.range"
        )
    if not tgt or not tgt.range:
        raise ValueError(
            f"edge class {class_name!r} is missing slot_usage.target_id.range"
        )
    return src.range, tgt.range


def _emit_edge_table(sv: SchemaView, class_name: str) -> str:
    src_type, tgt_type = _resolve_edge_endpoints(sv, class_name)

    # Exclude source_id / target_id from edge columns: they become the
    # FROM / TO endpoints, not stored as regular scalar columns.
    user_cols = [
        (n, t) for n, t in _user_columns_for_class(sv, class_name)
        if n not in ("source_id", "target_id")
    ]
    sys_cols = list(_EDGE_SYSTEM_COLUMNS.items())

    body_lines = [f"    FROM {src_type} TO {tgt_type}"]
    body_lines += [f"    {n} {t}" for n, t in user_cols + sys_cols]
    body = ",\n".join(body_lines)
    return (
        f"CREATE REL TABLE IF NOT EXISTS {class_name}(\n"
        f"{body}\n"
        f")"
    )


# ── Orchestration ────────────────────────────────────────────────────

def generate(sv: SchemaView) -> dict:
    """Walk the schema, emit DDL + metadata."""
    node_ddl: list[str] = []
    edge_ddl: list[str] = []
    node_tables: list[str] = []
    edge_tables: list[str] = []
    column_mapping: dict[str, dict[str, str]] = {}

    # Order: node tables first (so edges can reference them), alphabetical
    # within each group for stable output.
    local_classes = sorted(sv.all_classes(imports=False))

    for cname in local_classes:
        if _is_abstract_or_mixin(sv, cname):
            continue
        if _is_node_class(sv, cname):
            node_ddl.append(_emit_node_table(sv, cname))
            node_tables.append(cname)
            rn = _column_renames(sv, cname)
            if rn:
                column_mapping[cname] = rn
        elif _is_edge_class(sv, cname):
            # Defer — append after nodes.
            pass

    for cname in local_classes:
        if _is_abstract_or_mixin(sv, cname):
            continue
        if _is_edge_class(sv, cname):
            edge_ddl.append(_emit_edge_table(sv, cname))
            edge_tables.append(cname)
            rn = _column_renames(sv, cname)
            if rn:
                column_mapping[cname] = rn

    return {
        "ddl": node_ddl + edge_ddl,
        "nodes": node_tables,
        "edges": edge_tables,
        "column_mapping": column_mapping,
    }


# ── File writer ──────────────────────────────────────────────────────

def _render_py_module(bundle: dict) -> str:
    ddl_literals = [
        f'    """{stmt}""",'.replace('\\', '\\\\')
        for stmt in bundle["ddl"]
    ]
    ddl_block = "\n".join(ddl_literals)

    return f'''"""Kuzu DDL generated from schema/extended_thinking.yaml (ADR 013).

DO NOT EDIT. Regenerate with:
    python scripts/gen_kuzu.py

Every typed node table inherits a set of system columns (bitemporal timestamps,
namespace, vectors_pending). Every edge table pins FROM/TO to specific node
types per its LinkML slot_usage. Columns whose LinkML name collides with a
Kuzu reserved word are renamed; see COLUMN_MAPPING below for round-tripping
back to the ontology name.
"""

from __future__ import annotations


EXTENDED_THINKING_DDL: list[str] = [
{ddl_block}
]


NODE_TABLES: list[str] = {bundle["nodes"]!r}


EDGE_TABLES: list[str] = {bundle["edges"]!r}


# LinkML slot name → Kuzu column name, only listed where renamed.
# Round-trip using this map when reading Kuzu rows back into ontology types.
COLUMN_MAPPING: dict[str, dict[str, str]] = {bundle["column_mapping"]!r}
'''


def main() -> int:
    if not SCHEMA_PATH.exists():
        print(f"schema not found: {SCHEMA_PATH}", file=sys.stderr)
        return 2

    sv = SchemaView(str(SCHEMA_PATH))
    bundle = generate(sv)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(_render_py_module(bundle), encoding="utf-8")
    print(
        f"wrote {OUTPUT_PATH}  "
        f"({len(bundle['nodes'])} node tables, {len(bundle['edges'])} edge tables)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
