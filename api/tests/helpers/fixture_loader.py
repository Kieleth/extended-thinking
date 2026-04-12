"""Load JSON fixtures into a GraphStore for algorithm-only tests.

Used by tests that want a known graph shape without running the
extraction pipeline. The JSON format is intentionally minimal:

    {
      "concepts": [
        {"id": "kuzu", "name": "Kuzu", "category": "entity",
         "description": "...", "source_quote": "..."},
        ...
      ],
      "relationships": [
        {"source": "kuzu", "target": "bitemporal_kg",
         "weight": 2.0, "context": "...", "t_valid_from": "2026-04-11"},
        ...
      ]
    }

All timestamps are ISO 8601 strings. Concept IDs must be stable across runs,
which is why they are authored, not derived from content hashes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from extended_thinking.storage.graph_store import GraphStore


def load_graph_from_json(store: GraphStore, path: str | Path) -> dict[str, Any]:
    """Populate `store` with concepts and relationships from a JSON fixture.

    Returns a summary dict with `concepts` and `relationships` counts and
    the set of concept IDs that were loaded. Good for sanity-checks in tests.
    """
    data = json.loads(Path(path).read_text())
    return load_graph_from_dict(store, data)


def load_graph_from_dict(store: GraphStore, data: dict[str, Any]) -> dict[str, Any]:
    concepts = data.get("concepts", [])
    relationships = data.get("relationships", [])

    concept_ids: set[str] = set()
    for c in concepts:
        cid = c["id"]
        store.add_concept(
            concept_id=cid,
            name=c["name"],
            category=c.get("category", "concept"),
            description=c.get("description", ""),
            source_quote=c.get("source_quote", ""),
        )
        concept_ids.add(cid)

    rel_count = 0
    for r in relationships:
        src, tgt = r["source"], r["target"]
        if src not in concept_ids or tgt not in concept_ids:
            raise ValueError(
                f"fixture relationship references unknown concept: {src} -> {tgt}. "
                "Add the concept first or fix the fixture."
            )
        store.add_relationship(
            source_id=src,
            target_id=tgt,
            weight=float(r.get("weight", 1.0)),
            context=r.get("context", ""),
            t_valid_from=r.get("t_valid_from"),
        )
        rel_count += 1

    return {
        "concepts": len(concept_ids),
        "relationships": rel_count,
        "concept_ids": concept_ids,
    }
