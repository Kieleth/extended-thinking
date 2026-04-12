"""Internal concept store — where extended-thinking's analysis lives.

This is NOT the memory system (that's the MemoryProvider's job). This stores
the concepts, relationships, and wisdom that the DIKW pipeline generates.

SQLite-based. Simple, no lock issues, no Rust dependency, survives crashes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Schema migrations. Key = target version, value = list of SQL statements.
# Version 1 is the baseline (tables created in _create_tables).
# Future migrations add columns, tables, indexes. Never rename or remove.
MIGRATIONS: dict[int, list[str]] = {
    2: [
        # Concepts: typed entities, provenance, access tracking
        "ALTER TABLE concepts ADD COLUMN entity_type TEXT DEFAULT 'concept'",
        "ALTER TABLE concepts ADD COLUMN provider_source TEXT DEFAULT ''",
        "ALTER TABLE concepts ADD COLUMN access_count INTEGER DEFAULT 0",
        "ALTER TABLE concepts ADD COLUMN last_accessed TEXT DEFAULT ''",
        # Relationships: typed edges, temporal validity, provenance, access tracking
        "ALTER TABLE relationships ADD COLUMN edge_type TEXT DEFAULT 'RelatesTo'",
        "ALTER TABLE relationships ADD COLUMN valid_from TEXT DEFAULT ''",
        "ALTER TABLE relationships ADD COLUMN valid_to TEXT",
        "ALTER TABLE relationships ADD COLUMN provenance TEXT DEFAULT ''",
        "ALTER TABLE relationships ADD COLUMN access_count INTEGER DEFAULT 0",
        "ALTER TABLE relationships ADD COLUMN last_accessed TEXT DEFAULT ''",
        # Provenance table: traces every entity/edge to its source
        """CREATE TABLE IF NOT EXISTS provenance (
            id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            source_provider TEXT NOT NULL,
            source_chunk_id TEXT DEFAULT '',
            llm_model TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_provenance_entity ON provenance(entity_id)",
    ],
    3: [
        # Entity resolution: track merged concepts
        "ALTER TABLE concepts ADD COLUMN canonical_id TEXT DEFAULT ''",
        # Co-occurrence groups: n-ary relationships from shared chunks
        """CREATE TABLE IF NOT EXISTS co_occurrences (
            id TEXT PRIMARY KEY,
            chunk_id TEXT NOT NULL,
            concept_ids TEXT NOT NULL,
            context TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_cooccur_chunk ON co_occurrences(chunk_id)",
    ],
}

CURRENT_SCHEMA_VERSION = 3


def _normalize_id_static(name: str) -> str:
    """Normalize a concept name to a stable ID. Must match pipeline_v2._normalize_id."""
    return name.lower().strip().replace(" ", "-").replace("/", "-")[:60]


class ConceptStore:
    """SQLite-backed store for concepts, relationships, and wisdom."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._run_migrations()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS concepts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT DEFAULT '',
                source_quote TEXT DEFAULT '',
                frequency INTEGER DEFAULT 1,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS relationships (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                context TEXT DEFAULT '',
                FOREIGN KEY (source_id) REFERENCES concepts(id),
                FOREIGN KEY (target_id) REFERENCES concepts(id)
            );

            CREATE TABLE IF NOT EXISTS wisdoms (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                wisdom_type TEXT DEFAULT 'wisdom',
                status TEXT DEFAULT 'pending',
                based_on_sessions INTEGER DEFAULT 0,
                based_on_concepts INTEGER DEFAULT 0,
                related_concept_ids TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                wisdom_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (wisdom_id) REFERENCES wisdoms(id)
            );

            CREATE INDEX IF NOT EXISTS idx_concepts_category ON concepts(category);
            CREATE INDEX IF NOT EXISTS idx_concepts_frequency ON concepts(frequency);
            CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id);
            CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id);
            CREATE INDEX IF NOT EXISTS idx_wisdoms_status ON wisdoms(status);

            CREATE TABLE IF NOT EXISTS processed_chunks (
                chunk_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def _run_migrations(self):
        """Apply pending schema migrations. Idempotent, safe to call on every startup."""
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()

        if not row:
            # Fresh DB: baseline is v1 (tables just created by _create_tables)
            current = 1
            self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (current,))
            self._conn.commit()
        else:
            current = row[0]

        for target_version in sorted(MIGRATIONS.keys()):
            if target_version > current:
                for sql in MIGRATIONS[target_version]:
                    self._conn.execute(sql)
                self._conn.execute("UPDATE schema_version SET version = ?", (target_version,))
                self._conn.commit()
                logger.info("Migrated ConceptStore schema to version %d", target_version)

    @property
    def schema_version(self) -> int:
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else CURRENT_SCHEMA_VERSION

    # ── Concepts ─────────────────────────────────────────────────────

    def add_concept(self, concept_id: str, name: str, category: str,
                    description: str, source_quote: str = "") -> None:
        """Add or merge a concept. If it exists, increment frequency and update."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_concept(concept_id)

        if existing:
            new_freq = existing["frequency"] + 1
            new_desc = description if len(description) > len(existing["description"]) else existing["description"]
            self._conn.execute(
                "UPDATE concepts SET frequency=?, last_seen=?, description=? WHERE id=?",
                (new_freq, now, new_desc, concept_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO concepts (id, name, category, description, source_quote, frequency, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (concept_id, name, category, description, source_quote, now, now),
            )
        self._conn.commit()

    def get_concept(self, concept_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM concepts WHERE id=?", (concept_id,)).fetchone()
        return dict(row) if row else None

    def list_concepts(self, order_by: str = "name", limit: int = 100) -> list[dict]:
        order = "frequency DESC" if order_by == "frequency" else "name ASC"
        rows = self._conn.execute(f"SELECT * FROM concepts ORDER BY {order} LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── Relationships ────────────────────────────────────────────────

    def add_relationship(self, source_id: str, target_id: str,
                         weight: float = 1.0, context: str = "") -> str:
        rel_id = f"rel-{source_id}-{target_id}"
        existing = self._conn.execute("SELECT id FROM relationships WHERE id=?", (rel_id,)).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE relationships SET weight=?, context=? WHERE id=?",
                (weight, context, rel_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO relationships (id, source_id, target_id, weight, context) VALUES (?, ?, ?, ?, ?)",
                (rel_id, source_id, target_id, weight, context),
            )
        self._conn.commit()
        return rel_id

    def get_relationships(self, concept_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM relationships WHERE source_id=? OR target_id=?",
            (concept_id, concept_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Wisdom ───────────────────────────────────────────────────────

    def add_wisdom(self, title: str, description: str, wisdom_type: str,
                   based_on_sessions: int = 0, based_on_concepts: int = 0,
                   related_concept_ids: list[str] | None = None) -> str:
        wisdom_id = f"wisdom-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO wisdoms (id, title, description, wisdom_type, status, based_on_sessions, based_on_concepts, related_concept_ids, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (wisdom_id, title, description, wisdom_type, based_on_sessions, based_on_concepts,
             json.dumps(related_concept_ids or []), now),
        )
        self._conn.commit()
        return wisdom_id

    def get_wisdom(self, wisdom_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM wisdoms WHERE id=?", (wisdom_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["related_concept_ids"] = json.loads(result.get("related_concept_ids", "[]"))
        # Attach feedback
        feedback_rows = self._conn.execute(
            "SELECT * FROM feedback WHERE wisdom_id=? ORDER BY created_at", (wisdom_id,)
        ).fetchall()
        result["feedback"] = [dict(f) for f in feedback_rows]
        return result

    def list_wisdoms(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM wisdoms WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM wisdoms ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["related_concept_ids"] = json.loads(d.get("related_concept_ids", "[]"))
            results.append(d)
        return results

    def update_wisdom_status(self, wisdom_id: str, status: str) -> None:
        self._conn.execute("UPDATE wisdoms SET status=? WHERE id=?", (status, wisdom_id))
        self._conn.commit()

    # ── Feedback ─────────────────────────────────────────────────────

    def add_feedback(self, wisdom_id: str, content: str) -> str:
        feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO feedback (id, wisdom_id, content, created_at) VALUES (?, ?, ?, ?)",
            (feedback_id, wisdom_id, content, now),
        )
        self._conn.commit()
        return feedback_id

    # ── Chunk tracking ────────────────────────────────────────────────

    def mark_chunk_processed(self, chunk_id: str, source: str = "",
                              source_type: str = "",
                              t_source_created: str = "") -> None:
        """Record that a chunk has been processed (for dedup).

        Legacy store ignores source/source_type/t_source_created
        (GraphStore uses them).
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO processed_chunks (chunk_id, processed_at) VALUES (?, ?)",
            (chunk_id, now),
        )
        self._conn.commit()

    def is_chunk_processed(self, chunk_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM processed_chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        return row is not None

    def filter_unprocessed(self, chunk_ids: list[str]) -> list[str]:
        """Return only chunk IDs that haven't been processed yet."""
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self._conn.execute(
            f"SELECT chunk_id FROM processed_chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        processed = {r[0] for r in rows}
        return [cid for cid in chunk_ids if cid not in processed]

    # ── Access tracking + provenance ────────────────────────────────

    def record_access(self, entity_id: str) -> None:
        """Record that an entity was accessed (traversed, explored, etc.)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE concepts SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
            (now, entity_id),
        )
        self._conn.commit()

    def record_edge_access(self, source_id: str, target_id: str) -> None:
        """Record that a relationship edge was traversed."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE relationships SET access_count = access_count + 1, last_accessed = ? "
            "WHERE source_id = ? AND target_id = ?",
            (now, source_id, target_id),
        )
        self._conn.commit()

    def add_provenance(self, entity_id: str, source_provider: str,
                       source_chunk_id: str = "", llm_model: str = "",
                       source: str = "", source_type: str = "") -> str:
        """Record where an entity came from. Legacy store ignores source/source_type."""
        prov_id = f"prov-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO provenance (id, entity_id, source_provider, source_chunk_id, llm_model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (prov_id, entity_id, source_provider, source_chunk_id, llm_model, now),
        )
        self._conn.commit()
        return prov_id

    def get_concept_sources(self, concept_id: str) -> list[dict]:
        """Stub for API parity with GraphStore. Legacy SQLite store doesn't track sources richly."""
        return []

    def supersede_edge(self, source_id: str, target_id: str,
                       new_edge_ref: str = "", reason: str = "") -> bool:
        """Stub for API parity with GraphStore. Legacy store doesn't support supersession."""
        return False

    def diff(self, date_from: str, date_to: str) -> dict:
        """Stub for API parity. Legacy store doesn't support temporal diff."""
        return {
            "window": {"from": date_from, "to": date_to},
            "concepts_added": [],
            "concepts_deprecated": [],
            "edges_created": [],
            "edges_expired": [],
        }

    def get_provenance(self, entity_id: str) -> list[dict]:
        """Get provenance records for an entity."""
        rows = self._conn.execute(
            "SELECT * FROM provenance WHERE entity_id = ? ORDER BY created_at",
            (entity_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Entity resolution + co-occurrence ──────────────────────────

    def find_similar_concept(self, name: str, threshold: float = 0.85) -> dict | None:
        """Find an existing concept similar to `name` by normalized string matching.

        Returns the best match above threshold, or None.
        Uses character-level similarity (SequenceMatcher) as a lightweight
        alternative to embedding comparison. At <1000 concepts, this is fast enough.
        """
        from difflib import SequenceMatcher
        normalized = name.lower().strip()
        best_match = None
        best_score = 0.0

        for c in self.list_concepts(limit=1000):
            existing = c["name"].lower().strip()
            # Exact match on normalized ID
            if _normalize_id_static(name) == c["id"]:
                return c
            score = SequenceMatcher(None, normalized, existing).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_match = c

        return best_match

    def merge_concept(self, source_id: str, target_id: str) -> None:
        """Merge source concept into target. Source gets canonical_id pointing to target.

        Relationships from source are re-pointed to target.
        Frequency is summed. Source remains in DB (for provenance) but marked as merged.
        """
        source = self.get_concept(source_id)
        target = self.get_concept(target_id)
        if not source or not target:
            return

        # Sum frequencies
        new_freq = target["frequency"] + source["frequency"]
        self._conn.execute("UPDATE concepts SET frequency = ? WHERE id = ?", (new_freq, target_id))

        # Mark source as merged
        self._conn.execute("UPDATE concepts SET canonical_id = ? WHERE id = ?", (target_id, source_id))

        # Re-point relationships
        self._conn.execute(
            "UPDATE relationships SET source_id = ? WHERE source_id = ?", (target_id, source_id)
        )
        self._conn.execute(
            "UPDATE relationships SET target_id = ? WHERE target_id = ?", (target_id, source_id)
        )

        # Re-point provenance
        self._conn.execute(
            "UPDATE provenance SET entity_id = ? WHERE entity_id = ?", (target_id, source_id)
        )
        self._conn.commit()

    def add_co_occurrence(self, chunk_id: str, concept_ids: list[str],
                          context: str = "") -> str:
        """Record that these concepts co-occurred in the same chunk."""
        cooccur_id = f"cooccur-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO co_occurrences (id, chunk_id, concept_ids, context, created_at) VALUES (?, ?, ?, ?, ?)",
            (cooccur_id, chunk_id, json.dumps(concept_ids), context, now),
        )
        self._conn.commit()
        return cooccur_id

    def get_co_occurrences(self, concept_id: str) -> list[dict]:
        """Get all co-occurrence groups containing this concept."""
        rows = self._conn.execute(
            "SELECT * FROM co_occurrences ORDER BY created_at DESC"
        ).fetchall()
        results = []
        for r in rows:
            ids = json.loads(r["concept_ids"])
            if concept_id in ids:
                d = dict(r)
                d["concept_ids"] = ids
                results.append(d)
        return results

    # ── Living graph: decay, activation, active set ────────────────

    def effective_weight(self, source_id: str, target_id: str,
                         decay_rate: float = 0.95) -> float:
        """Physarum-inspired edge weight: decays with time since last access.

        effective = base_weight * decay_rate ^ days_since_access
        """
        row = self._conn.execute(
            "SELECT weight, last_accessed FROM relationships WHERE source_id=? AND target_id=?",
            (source_id, target_id),
        ).fetchone()
        if not row:
            return 0.0

        base = row["weight"] or 1.0
        last = row["last_accessed"] or ""
        if not last:
            return base

        try:
            last_dt = datetime.fromisoformat(last)
            days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400
            return base * (decay_rate ** days)
        except (ValueError, TypeError):
            return base

    def spread_activation(self, seed_ids: list[str], depth: int = 3,
                          decay_per_hop: float = 0.7,
                          budget: int = 100) -> list[tuple[str, float]]:
        """Spreading activation from seed nodes through the graph.

        Returns (concept_id, activation_score) pairs sorted by score descending.
        Respects edge weights and decays with distance. Strictly better than
        BFS for "find related" queries.

        Based on: arxiv.org/html/2512.15922 (spreading activation for GraphRAG)
        """
        scores: dict[str, float] = {}
        for sid in seed_ids:
            scores[sid] = 1.0

        # Build adjacency from relationships
        all_rels = self._conn.execute("SELECT * FROM relationships").fetchall()
        adj: dict[str, list[tuple[str, float]]] = {}
        for r in all_rels:
            src, tgt = r["source_id"], r["target_id"]
            w = self.effective_weight(src, tgt)
            adj.setdefault(src, []).append((tgt, w))
            adj.setdefault(tgt, []).append((src, w))

        # Spread activation
        frontier = list(seed_ids)
        for _ in range(depth):
            next_frontier = []
            for node in frontier:
                node_score = scores.get(node, 0.0)
                if node_score < 0.01:
                    continue
                for neighbor, weight in adj.get(node, []):
                    spread = node_score * weight * decay_per_hop
                    if spread > 0.01:
                        old = scores.get(neighbor, 0.0)
                        scores[neighbor] = min(1.0, old + spread)
                        if neighbor not in frontier:
                            next_frontier.append(neighbor)
                    if len(scores) >= budget:
                        break
                if len(scores) >= budget:
                    break
            frontier = next_frontier
            if not frontier or len(scores) >= budget:
                break

        # Remove seeds from results, sort by score
        results = [(cid, score) for cid, score in scores.items() if cid not in seed_ids]
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def active_nodes(self, k: int = 10) -> list[dict]:
        """Sparse active set: top-k concepts by combined activity score.

        Score = frequency * recency_factor * sqrt(degree)
        Recency = 1 / (1 + days_since_access)

        Inspired by cortical sparse coding: only a few percent of neurons
        fire at any time. The active set is the signal, everything else is noise.
        """
        import math
        concepts = self.list_concepts(limit=500)
        now = datetime.now(timezone.utc)

        # Pre-compute degree for each concept
        all_rels = self._conn.execute("SELECT source_id, target_id FROM relationships").fetchall()
        degree: dict[str, int] = {}
        for r in all_rels:
            degree[r["source_id"]] = degree.get(r["source_id"], 0) + 1
            degree[r["target_id"]] = degree.get(r["target_id"], 0) + 1

        scored = []
        for c in concepts:
            freq = c.get("frequency", 1)
            last = c.get("last_accessed", "")
            deg = degree.get(c["id"], 0)

            # Recency factor: 1.0 if just accessed, decays toward 0
            recency = 0.1  # base for never-accessed
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    days = (now - last_dt).total_seconds() / 86400
                    recency = 1.0 / (1.0 + days)
                except (ValueError, TypeError):
                    recency = 0.1  # Unparseable timestamp, use base

            score = freq * recency * math.sqrt(max(deg, 1))
            scored.append((c, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:k]]

    # ── Graph queries ────────────────────────────────────────────────

    def get_graph_overview(self) -> dict:
        """Overview of the concept graph: clusters, bridges, isolated nodes."""
        concepts = self.list_concepts(limit=500)
        all_rels = self._conn.execute("SELECT * FROM relationships").fetchall()
        rels = [dict(r) for r in all_rels]

        # Build adjacency
        adj: dict[str, set[str]] = {}
        for c in concepts:
            adj[c["id"]] = set()
        for r in rels:
            adj.setdefault(r["source_id"], set()).add(r["target_id"])
            adj.setdefault(r["target_id"], set()).add(r["source_id"])

        # Find connected components (clusters) via BFS
        visited: set[str] = set()
        clusters: list[dict] = []

        for cid in adj:
            if cid in visited:
                continue
            component: list[str] = []
            queue = [cid]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                component.append(node)
                for neighbor in adj.get(node, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)

            cluster_concepts = [c for c in concepts if c["id"] in set(component)]
            if cluster_concepts:
                clusters.append({
                    "size": len(cluster_concepts),
                    "concepts": cluster_concepts,
                })

        # Isolated = in no relationship
        connected_ids = set()
        for r in rels:
            connected_ids.add(r["source_id"])
            connected_ids.add(r["target_id"])
        isolated = [c for c in concepts if c["id"] not in connected_ids]

        # Bridges = concepts with high degree (connected to 3+ others)
        degree = {cid: len(neighbors) for cid, neighbors in adj.items()}
        bridges = [c for c in concepts if degree.get(c["id"], 0) >= 3]

        return {
            "total_concepts": len(concepts),
            "total_relationships": len(rels),
            "total_wisdoms": self._conn.execute("SELECT COUNT(*) FROM wisdoms").fetchone()[0],
            "clusters": sorted(clusters, key=lambda c: c["size"], reverse=True),
            "bridges": bridges,
            "isolated": isolated,
        }

    def find_path(self, from_id: str, to_id: str) -> list[dict] | None:
        """BFS shortest path between two concepts. Returns list of concept dicts or None."""
        if from_id == to_id:
            concept = self.get_concept(from_id)
            return [concept] if concept else None

        # Build adjacency
        all_rels = self._conn.execute("SELECT * FROM relationships").fetchall()
        adj: dict[str, set[str]] = {}
        for r in all_rels:
            adj.setdefault(r["source_id"], set()).add(r["target_id"])
            adj.setdefault(r["target_id"], set()).add(r["source_id"])

        # BFS
        visited: set[str] = {from_id}
        queue: list[list[str]] = [[from_id]]

        while queue:
            path = queue.pop(0)
            node = path[-1]

            for neighbor in adj.get(node, set()):
                if neighbor == to_id:
                    full_path = path + [neighbor]
                    return [self.get_concept(cid) for cid in full_path if self.get_concept(cid)]

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])

        return None  # No path

    def get_neighborhood(self, concept_id: str) -> dict | None:
        """Get a concept with all its connections and related wisdom."""
        concept = self.get_concept(concept_id)
        if not concept:
            return None

        rels = self.get_relationships(concept_id)

        connections = []
        for r in rels:
            other_id = r["target_id"] if r["source_id"] == concept_id else r["source_id"]
            other = self.get_concept(other_id)
            if other:
                connections.append({
                    **other,
                    "weight": r["weight"],
                    "context": r["context"],
                })

        # Find related wisdom
        related_wisdoms = []
        for w in self.list_wisdoms(limit=20):
            if concept_id in w.get("related_concept_ids", []):
                related_wisdoms.append(w)

        return {
            "concept": concept,
            "connections": connections,
            "related_wisdoms": related_wisdoms,
        }

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        concepts = self._conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        wisdoms = self._conn.execute("SELECT COUNT(*) FROM wisdoms").fetchone()[0]
        relationships = self._conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
        return {
            "total_concepts": concepts,
            "total_wisdoms": wisdoms,
            "total_relationships": relationships,
        }
