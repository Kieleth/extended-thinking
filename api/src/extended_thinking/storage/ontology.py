"""Ontology abstraction (ADR 013).

A materialized set of Kuzu DDL + metadata, produced by the LinkML codegen
(`scripts/gen_kuzu.py`). GraphStore takes one at construction time and
uses it to create every typed table. Multiple ontologies can be merged
so a consumer (e.g. autoresearch-ET) can layer its own typed schema on
top of ET's base without forking.

See docs/ADR/013-research-backbone-audience.md §C1 and malleus's
KNOWLEDGE_GRAPH_PROTOCOL.md for why this is constitutive: the ontology
is a constructor parameter, not a post-hoc validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType


@dataclass
class Ontology:
    """Materialized ontology ready to apply to a Kuzu database.

    Produced by loading a generated kuzu_ddl module (see
    `scripts/gen_kuzu.py`) via `Ontology.from_module`. Consumers compose
    multiple ontologies by `.merged_with()`.
    """

    name: str
    ddl: list[str] = field(default_factory=list)
    node_tables: list[str] = field(default_factory=list)
    edge_tables: list[str] = field(default_factory=list)
    column_renames: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_module(cls, module: ModuleType, *, name: str | None = None) -> "Ontology":
        """Load from a module shaped like `extended_thinking._schema.kuzu_ddl`.

        The module must export EXTENDED_THINKING_DDL, NODE_TABLES,
        EDGE_TABLES, COLUMN_MAPPING (the names our codegen emits).
        """
        return cls(
            name=name or module.__name__,
            ddl=list(module.EXTENDED_THINKING_DDL),
            node_tables=list(module.NODE_TABLES),
            edge_tables=list(module.EDGE_TABLES),
            column_renames={k: dict(v) for k, v in module.COLUMN_MAPPING.items()},
        )

    def merged_with(self, other: "Ontology") -> "Ontology":
        """Union two ontologies — consumer schema merged onto ET's base.

        Tables and DDL are concatenated (Kuzu's IF NOT EXISTS means the
        first declaration wins; consumers must not redefine ET's tables).
        Column renames merge table-by-table; collisions raise.
        """
        renames: dict[str, dict[str, str]] = dict(self.column_renames)
        for table, rn in other.column_renames.items():
            if table in renames:
                for k, v in rn.items():
                    if renames[table].get(k, v) != v:
                        raise ValueError(
                            f"column rename collision for {table}.{k}: "
                            f"{renames[table][k]!r} vs {v!r}"
                        )
                renames[table] = {**renames[table], **rn}
            else:
                renames[table] = dict(rn)
        return Ontology(
            name=f"{self.name}+{other.name}",
            ddl=[*self.ddl, *other.ddl],
            node_tables=[*self.node_tables, *other.node_tables],
            edge_tables=[*self.edge_tables, *other.edge_tables],
            column_renames=renames,
        )


def default_ontology() -> Ontology:
    """The canonical ET ontology — imports the generated kuzu_ddl module."""
    from extended_thinking._schema import kuzu_ddl
    return Ontology.from_module(kuzu_ddl, name="extended-thinking")
