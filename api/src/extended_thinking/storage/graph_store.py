"""GraphStore — Kuzu-backed knowledge graph.

Replaces ConceptStore's SQLite tables with a proper graph database.
Same public API so callers don't break. Internals are Cypher queries.

Kuzu is embedded (single directory, no server), like SQLite but for graphs.
Supports: variable-length paths, pattern matching, property filters,
undirected traversal, atomic updates, aggregations.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import kuzu

logger = logging.getLogger(__name__)


class GraphStore:
    """Kuzu-backed knowledge graph, ontology-driven (ADR 013).

    Schema comes from an Ontology object (produced by
    `scripts/gen_kuzu.py` from the LinkML + malleus imports). The
    constructor accepts an optional override so consumers (autoresearch-ET,
    etc.) can layer their own typed schema on top of ET's default.

    Bitemporal (ADR 002): every node and edge carries
    `t_valid_from`, `t_valid_to` (world time),
    `t_created`, `t_expired` (transaction time), and
    `t_superseded_by` (pointer to the record that replaced this one).
    These are system columns injected by the codegen.
    """

    def __init__(self, db_path: Path, ontology=None, vectors=None):
        """
        vectors: optional VectorStore. When provided, `insert()` also
        indexes typed nodes into it so `find_similar_typed()` (ADR 013 C6)
        can retrieve them. `vectors_pending` on the Kuzu row tracks
        indexing state — false after a successful vector write, true if
        it failed or no vector store was configured.
        """
        from extended_thinking.storage.ontology import (
            Ontology,
            default_ontology,
        )
        self._db_path = db_path
        self._db = kuzu.Database(str(db_path))
        self._conn = kuzu.Connection(self._db)
        self._ontology: Ontology = ontology or default_ontology()
        self._vectors = vectors
        self._apply_ontology()

    def _apply_ontology(self):
        """Apply every CREATE statement from the ontology. Idempotent via
        IF NOT EXISTS — safe to call against an existing database."""
        for stmt in self._ontology.ddl:
            self._exec_safe(stmt)

    def _exec_safe(self, query: str):
        """Execute query, ignore 'already exists' errors."""
        try:
            self._conn.execute(query)
        except RuntimeError as e:
            if "already exists" in str(e).lower():
                pass
            else:
                raise

    # ── Typed write path (ADR 013 C1) ────────────────────────────────

    def insert(self, instance, *, namespace: str = "default",
               source: str = "") -> str:
        """Write a typed Pydantic instance — node or edge — into Kuzu.

        Dispatches to_kuzu_row / edge_endpoints for serialization, then
        executes the matching Cypher. Returns the row id.

        This is the typed path (ADR 013 C1). The legacy domain methods
        (`add_concept`, `add_wisdom`, `add_relationship`, `mark_chunk_processed`,
        `add_provenance`) remain as ergonomic helpers for the memory
        pipeline; they write to the same ontology-driven tables.
        """
        from extended_thinking._schema.kuzu_types import (
            EDGE_TYPES,
            KUZU_TABLE,
            NODE_TYPES,
            edge_endpoints,
            to_kuzu_row,
        )
        cls = type(instance)
        if cls not in KUZU_TABLE:
            raise ValueError(
                f"{cls.__name__} is not in the ontology. Add it to "
                f"schema/extended_thinking.yaml (or a merged consumer ontology) "
                f"and regenerate with `make schema-kuzu`."
            )
        table = KUZU_TABLE[cls]

        if cls in NODE_TYPES:
            row = to_kuzu_row(instance, namespace=namespace, source=source)
            props = ", ".join(f"{k}: ${k}" for k in row)
            self._conn.execute(
                f"CREATE (:{table} {{{props}}})", parameters=row,
            )
            # ADR 013 C6: index into VectorStore when available. Failure is
            # non-fatal — vectors_pending stays true so a future retry can
            # finish the indexing.
            if self._vectors is not None:
                self._index_node_vector(row["id"], table, namespace,
                                        source, instance)
            return row["id"]

        if cls in EDGE_TYPES:
            # Re-serialize with the caller's namespace/source so those
            # aren't lost; edge_endpoints will pop the id-fields out.
            row = to_kuzu_row(instance, namespace=namespace, source=source)
            src_id = row.pop("source_id")
            tgt_id = row.pop("target_id")
            props = row
            # Resolve endpoint node types by walking every registered type
            # until we find the one holding each id. Kuzu REQUIRES a label
            # in MATCH — unlabeled matches are rejected.
            src_type = self._find_node_type(src_id)
            tgt_type = self._find_node_type(tgt_id)
            if src_type is None:
                raise ValueError(f"source node not found: {src_id!r}")
            if tgt_type is None:
                raise ValueError(f"target node not found: {tgt_id!r}")

            ph = ", ".join(f"{k}: ${k}" for k in props)
            params = {**props, "_src": src_id, "_tgt": tgt_id}
            self._conn.execute(
                f"MATCH (a:{src_type} {{id: $_src}}), (b:{tgt_type} {{id: $_tgt}}) "
                f"CREATE (a)-[:{table} {{{ph}}}]->(b)",
                parameters=params,
            )
            return props["id"]

        raise ValueError(f"{cls.__name__} classifies as neither node nor edge")

    def _find_node_type(self, node_id: str) -> str | None:
        """Return the Kuzu label of a node by id, or None if no match.

        Walks every registered node table. Used by `insert()` to resolve
        endpoint labels for typed edges.
        """
        for table in self._ontology.node_tables:
            row = self._query_one(
                f"MATCH (n:{table} {{id: $id}}) RETURN n.id", {"id": node_id},
            )
            if row:
                return table
        return None

    # ── ADR 013 C6: typed vector similarity ──────────────────────────

    @staticmethod
    def _extract_indexable_text(instance) -> str:
        """Pull a text representation of a typed node for vector indexing.

        Concatenates common content-bearing fields (name/title/text/description)
        so both short labels and long descriptions influence retrieval.
        Falls back to the id if nothing textual is available.
        """
        parts: list[str] = []
        for attr in ("name", "title", "text", "description"):
            val = getattr(instance, attr, None)
            if val:
                parts.append(str(val))
        if not parts:
            return str(getattr(instance, "id", ""))
        # Dedup while preserving order (title and name are often identical)
        seen: set[str] = set()
        out: list[str] = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return ". ".join(out)

    def _index_node_vector(self, node_id: str, node_type: str,
                           namespace: str, source: str, instance) -> None:
        """Add a typed node to the VectorStore with ET metadata.

        Metadata keys use `et_` prefix so they don't collide with any
        domain metadata a consumer's VectorStore might already contain.
        """
        text = self._extract_indexable_text(instance)
        if not text:
            return
        try:
            self._vectors.add(
                id=node_id,
                text=text,
                metadata={
                    "et_node_type": node_type,
                    "et_namespace": namespace,
                    "et_source": source,
                    "source_type": "typed_node",
                },
            )
        except Exception as e:
            logger.warning(
                "vector index failed for %s %s (vectors_pending stays true): %s",
                node_type, node_id, e,
            )
            return
        # Clear the pending flag on success.
        try:
            self._conn.execute(
                f"MATCH (n:{node_type} {{id: $id}}) SET n.vectors_pending = false",
                parameters={"id": node_id},
            )
        except RuntimeError as e:
            logger.warning("could not clear vectors_pending on %s: %s", node_id, e)

    def record_proposal(
        self,
        algorithm: str,
        source_id: str,
        target_id: str,
        *,
        score: float = 0.0,
        parameters: dict | None = None,
        namespace: str = "default",
        et_source: str = "",
    ) -> str:
        """Persist a `ProposalBy` edge (ADR 013 C7).

        A proposal is what an algorithm said, not what's committed. Consumers
        inspect these to rebuild "at time T, algorithm X said Y relates to Z
        with score S" without re-running the algorithm.
        """
        import json as _json
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        now = _dt.now(_tz.utc).isoformat()
        edge_id = f"prop-{_uuid.uuid4().hex[:12]}"

        src_type = self._find_node_type(source_id)
        tgt_type = self._find_node_type(target_id)
        if src_type is None:
            raise ValueError(f"proposal source not found: {source_id!r}")
        if tgt_type is None:
            raise ValueError(f"proposal target not found: {target_id!r}")

        params = {
            "_src": source_id, "_tgt": target_id,
            "id": edge_id, "name": "",
            "algorithm": algorithm,
            "parameters_json": _json.dumps(parameters or {}),
            "invoked_at": now,
            "score": float(score),
            "relation_type": "proposal",
            "strength": float(score),
            "created_at": now, "updated_at": now,
            "t_valid_from": now, "t_valid_to": "",
            "t_created": now, "t_expired": "", "t_superseded_by": "",
            "namespace": namespace, "et_source": et_source,
        }
        ph = ", ".join(f"{k}: ${k}" for k in params if not k.startswith("_"))
        self._conn.execute(
            f"MATCH (a:{src_type} {{id: $_src}}), (b:{tgt_type} {{id: $_tgt}}) "
            f"CREATE (a)-[:ProposalBy {{{ph}}}]->(b)",
            parameters=params,
        )
        return edge_id

    def find_similar_typed(
        self,
        query: str,
        node_type: str,
        *,
        threshold: float = 0.5,
        k: int = 10,
        namespace: str | None = None,
        require_indexed: bool = True,
    ) -> list[tuple[str, float]]:
        """Vector similarity over one typed-node class (ADR 013 C6).

        Returns up to k (node_id, similarity) pairs with similarity >=
        threshold, sorted descending. Empty list when no vectors are
        configured or nothing clears the threshold.

        namespace: scope results to one namespace; None spans all.
        require_indexed: if True (default), only return nodes with
            vectors_pending=false. Set False to include nodes indexing
            is still catching up on (useful for admin/debug).
        """
        if self._vectors is None:
            return []
        if node_type not in self._ontology.node_tables:
            raise ValueError(
                f"find_similar_typed: unknown node_type {node_type!r}. "
                f"Registered: {sorted(self._ontology.node_tables)}"
            )
        # ChromaDB wants a single filter or an explicit $and — build both
        # forms so this works against ChromaDB's validator and against
        # simpler in-memory fakes in tests.
        if namespace is not None:
            where: dict = {"$and": [
                {"et_node_type": node_type},
                {"et_namespace": namespace},
            ]}
        else:
            where = {"et_node_type": node_type}

        raw = self._vectors.search(query, limit=max(k * 3, k), where=where)
        scored: list[tuple[str, float]] = []
        for r in raw:
            if r.score < threshold:
                continue
            if require_indexed:
                # Confirm the row is actually indexed (vectors_pending=false).
                check = self._query_one(
                    f"MATCH (n:{node_type} {{id: $id}}) RETURN n.vectors_pending",
                    {"id": r.id},
                )
                if check and check[0] is True:
                    continue
            scored.append((r.id, r.score))
            if len(scored) >= k:
                break
        return scored


    def _query_one(self, query: str, params: dict | None = None) -> list | None:
        result = self._conn.execute(query, parameters=params or {})
        return result.get_next() if result.has_next() else None

    def _query_all(self, query: str, params: dict | None = None) -> list[list]:
        result = self._conn.execute(query, parameters=params or {})
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    # ── Concepts ─────────────────────────────────────────────────────

    def add_concept(self, concept_id: str, name: str, category: str,
                    description: str, source_quote: str = "",
                    *, namespace: str = "memory") -> None:
        """Add or merge a concept. If exists, increment frequency.

        `namespace` (ADR 013 C2) stamps the concept's tenancy column.
        Per-folder / per-project callers pass e.g. `"memory:notes"` to
        keep concepts isolated. Caller is responsible for scoping
        concept_id so same-name concepts in different namespaces don't
        collide at the Kuzu primary-key level (Pipeline.sync prefixes
        the id with the namespace for non-default namespaces).
        """
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_concept(concept_id)

        if existing:
            new_freq = existing["frequency"] + 1
            new_desc = description if len(description) > len(existing["description"]) else existing["description"]
            self._conn.execute(
                "MATCH (c:Concept {id: $id}) SET "
                "c.frequency = $freq, c.last_seen = $now, "
                "c.updated_at = $now, c.desc_text = $dsc",
                parameters={"id": concept_id, "freq": new_freq, "now": now, "dsc": new_desc},
            )
        else:
            # Insert a full row per the generated ontology DDL. Values not
            # supplied by the caller default to empty / zero so every column
            # lands.
            self._conn.execute(
                "CREATE (:Concept {"
                "id: $id, name: $name, category: $cat, desc_text: $dsc, "
                "source_quote: $quote, frequency: $freq, "
                "first_seen: $now, last_seen: $now, "
                "created_at: $now, updated_at: $now, "
                "status: $status, tags: $tags, "
                "canonical_id: $cid, access_count: $ac, last_accessed: $la, "
                "t_valid_from: $now, t_valid_to: $empty, "
                "t_created: $now, t_expired: $empty, t_superseded_by: $empty, "
                "namespace: $ns, et_source: $src, vectors_pending: $vp"
                "})",
                parameters={
                    "id": concept_id, "name": name, "cat": category,
                    "dsc": description, "quote": source_quote, "freq": 1,
                    "now": now, "empty": "", "status": "",
                    "tags": "", "cid": "", "ac": 0, "la": "",
                    "ns": namespace, "src": "", "vp": True,
                },
            )

    def get_concept(self, concept_id: str,
                    *, namespace: str | None = None) -> dict | None:
        """Fetch a concept by id. Optional `namespace` filter (ADR 013 C2)."""
        params: dict = {"id": concept_id}
        if namespace is not None:
            where = "WHERE c.namespace = $ns"
            params["ns"] = namespace
        else:
            where = ""
        row = self._query_one(
            f"MATCH (c:Concept {{id: $id}}) {where} RETURN c", params,
        )
        if not row:
            return None
        return self._concept_row_to_dict(row[0])

    def list_concepts(self, order_by: str = "name", limit: int = 100,
                      as_of: str | None = None,
                      *, namespace: str | None = None) -> list[dict]:
        """List concepts.

        `as_of` (ISO date) restricts to concepts valid at that point.
        `namespace` (ADR 013 C2) restricts to one namespace; `None` spans all.
        """
        order = "c.frequency DESC" if order_by == "frequency" else "c.name ASC"
        clauses: list[str] = []
        params: dict = {"limit": limit}
        if as_of:
            clauses.append(
                "(c.t_valid_from <= $as_of AND (c.t_expired = '' OR c.t_expired > $as_of))"
            )
            params["as_of"] = as_of
        if namespace is not None:
            clauses.append("c.namespace = $ns")
            params["ns"] = namespace
        where = "WHERE " + " AND ".join(clauses) + " " if clauses else ""
        rows = self._query_all(
            f"MATCH (c:Concept) {where}RETURN c ORDER BY {order} LIMIT $limit",
            params,
        )
        return [self._concept_row_to_dict(r[0]) for r in rows]

    # ── Relationships ────────────────────────────────────────────────

    def add_relationship(self, source_id: str, target_id: str,
                         weight: float = 1.0, context: str = "",
                         t_valid_from: str | None = None) -> None:
        """Create or update edge between concepts.

        t_valid_from: world-time when the relation became true. Defaults to
        now — but callers should pass the source chunk's timestamp so old
        conversations produce edges with accurate valid_from (enables
        source-age-aware decay). t_created always tracks ingest time.
        """
        existing = self._query_one(
            "MATCH (a:Concept {id: $src})-[r:RelatesTo]->(b:Concept {id: $tgt}) RETURN r.weight",
            {"src": source_id, "tgt": target_id},
        )
        if existing:
            new_weight = existing[0] + weight
            self._conn.execute(
                "MATCH (a:Concept {id: $src})-[r:RelatesTo]->(b:Concept {id: $tgt}) "
                "SET r.weight = $w, r.context = $ctx",
                parameters={"src": source_id, "tgt": target_id, "w": new_weight, "ctx": context},
            )
        else:
            now = datetime.now(timezone.utc).isoformat()
            vf = t_valid_from or now
            edge_id = f"rel-{uuid.uuid4().hex[:12]}"
            self._conn.execute(
                "MATCH (a:Concept {id: $src}), (b:Concept {id: $tgt}) "
                "CREATE (a)-[:RelatesTo {"
                "id: $eid, name: $empty, "
                "weight: $w, context: $ctx, edge_type: $etype, "
                "access_count: $ac, last_accessed: $empty, "
                "relation_type: $rtype, strength: $strength, "
                "created_at: $now, updated_at: $now, "
                "t_valid_from: $vf, t_valid_to: $empty, "
                "t_created: $now, t_expired: $empty, t_superseded_by: $empty, "
                "namespace: $ns, et_source: $empty"
                "}]->(b)",
                parameters={"src": source_id, "tgt": target_id,
                            "eid": edge_id, "empty": "", "w": weight,
                            "ctx": context, "etype": "RelatesTo",
                            "ac": 0, "rtype": "relates_to", "strength": weight,
                            "now": now, "vf": vf, "ns": "memory"},
            )

    def get_relationships(self, concept_id: str,
                          as_of: str | None = None) -> list[dict]:
        """Get all edges touching this concept.

        Default: current state only (t_expired is empty).
        `as_of`: world-time filter — edges valid at that point in time.
        """
        params: dict = {"id": concept_id}
        if as_of:
            where = ("WHERE r.t_valid_from <= $as_of "
                     "AND (r.t_valid_to = '' OR r.t_valid_to > $as_of) ")
            params["as_of"] = as_of
        else:
            where = "WHERE r.t_expired IS NULL OR r.t_expired = '' "
        rows = self._query_all(
            "MATCH (a:Concept {id: $id})-[r:RelatesTo]-(b:Concept) "
            + where +
            "RETURN a.id, b.id, r.weight, r.context, r.edge_type, "
            "r.t_valid_from, r.t_valid_to, r.access_count, r.last_accessed",
            params,
        )
        results = []
        for r in rows:
            src = concept_id
            tgt = r[1] if r[0] == concept_id else r[0]
            results.append({
                "id": f"rel-{src}-{tgt}",
                "source_id": src,
                "target_id": tgt,
                "weight": r[2],
                "context": r[3] or "",
                "edge_type": r[4] or "RelatesTo",
                "valid_from": r[5] or "",
                "valid_to": r[6],
                "access_count": r[7] or 0,
                "last_accessed": r[8] or "",
            })
        return results

    # ── Wisdoms ──────────────────────────────────────────────────────

    def add_wisdom(self, title: str, description: str, wisdom_type: str = "wisdom",
                   based_on_sessions: int = 0, based_on_concepts: int = 0,
                   related_concept_ids: list[str] | None = None) -> str:
        wisdom_id = f"wisdom-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        ids_json = json.dumps(related_concept_ids or [])

        # Wisdom IS_A Signal per malleus. Signal requires signal_type and
        # bearer_id. For ET's legacy wisdom path: signal_type is the wisdom
        # subtype ("wisdom" / "nothing_novel"), bearer_id is the graph-wide
        # anchor (empty string for the whole graph; consumers can override).
        self._conn.execute(
            "CREATE (:Wisdom {"
            "id: $id, name: $title, title: $title, desc_text: $dsc, tags: $empty, "
            "wisdom_type: $wtype, status: $status, "
            "based_on_sessions: $sessions, based_on_concepts: $concepts, "
            "related_concept_ids: $ids, "
            "signal_type: $wtype, bearer_id: $empty, value: $zero, "
            "algorithm: $empty, perspective: $empty, computed_at: $now, "
            "created_at: $now, updated_at: $now, "
            "t_valid_from: $now, t_valid_to: $empty, "
            "t_created: $now, t_expired: $empty, t_superseded_by: $empty, "
            "namespace: $ns, et_source: $empty, vectors_pending: $vp"
            "})",
            parameters={"id": wisdom_id, "title": title, "dsc": description,
                        "wtype": wisdom_type, "status": "pending",
                        "sessions": based_on_sessions,
                        "concepts": based_on_concepts, "ids": ids_json,
                        "now": now, "empty": "", "zero": 0.0,
                        "ns": "memory", "vp": True},
        )

        # Create InformedBy edges (bitemporal + system columns per ontology)
        for cid in (related_concept_ids or []):
            self._conn.execute(
                "MATCH (w:Wisdom {id: $wid}), (c:Concept {id: $cid}) "
                "CREATE (w)-[:InformedBy {"
                "name: $empty, "
                "relation_type: $rtype, strength: $strength, "
                "created_at: $now, updated_at: $now, "
                "t_valid_from: $now, t_valid_to: $empty, "
                "t_created: $now, t_expired: $empty, t_superseded_by: $empty, "
                "namespace: $ns, et_source: $empty"
                "}]->(c)",
                parameters={"wid": wisdom_id, "cid": cid, "now": now,
                            "empty": "", "rtype": "informed_by",
                            "strength": 1.0, "ns": "memory"},
            )

        return wisdom_id

    def get_wisdom(self, wisdom_id: str) -> dict | None:
        row = self._query_one(
            "MATCH (w:Wisdom {id: $id}) RETURN w", {"id": wisdom_id}
        )
        if not row:
            return None
        d = self._wisdom_row_to_dict(row[0])
        d["feedback"] = []
        return d

    def list_wisdoms(self, status: str | None = None, limit: int = 50,
                     *, namespace: str | None = None) -> list[dict]:
        """List wisdoms. Optional `namespace` filter (ADR 013 C2)."""
        clauses: list[str] = []
        params: dict = {"limit": limit}
        if status:
            clauses.append("w.status = $status")
            params["status"] = status
        if namespace is not None:
            clauses.append("w.namespace = $ns")
            params["ns"] = namespace
        where = "WHERE " + " AND ".join(clauses) + " " if clauses else ""
        rows = self._query_all(
            f"MATCH (w:Wisdom) {where}RETURN w ORDER BY w.t_created DESC LIMIT $limit",
            params,
        )
        return [self._wisdom_row_to_dict(r[0]) for r in rows]

    def update_wisdom_status(self, wisdom_id: str, status: str) -> None:
        self._conn.execute(
            "MATCH (w:Wisdom {id: $id}) SET w.status = $status",
            parameters={"id": wisdom_id, "status": status},
        )

    # ── Feedback ─────────────────────────────────────────────────────

    def add_feedback(self, wisdom_id: str, content: str) -> str:
        feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
        # Store as property on wisdom (simplified from separate table)
        wisdom = self.get_wisdom(wisdom_id)
        if wisdom:
            existing = wisdom.get("feedback", [])
            existing.append({"id": feedback_id, "content": content,
                             "created_at": datetime.now(timezone.utc).isoformat()})
            # Kuzu doesn't have nested arrays natively, store as JSON in description
            # For now, append to description
            self._conn.execute(
                "MATCH (w:Wisdom {id: $id}) SET w.desc_text = w.desc_text + $fb",
                parameters={"id": wisdom_id, "fb": f"\n\n[Feedback] {content}"},
            )
        return feedback_id

    # ── Chunk tracking ───────────────────────────────────────────────

    def mark_chunk_processed(self, chunk_id: str, source: str = "",
                              source_type: str = "", t_source_created: str = "") -> None:
        """Mark a chunk as processed.

        t_source_created: when the user wrote it (from source metadata).
        t_ingested: when ET saw it (always now).
        """
        now = datetime.now(timezone.utc).isoformat()
        src_created = t_source_created or now  # fallback if not provided
        self._conn.execute(
            "MERGE (c:Chunk {id: $id}) "
            "ON CREATE SET c.name = $id, c.source = $src, c.source_type = $stype, "
            "c.t_source_created = $tsc, c.t_ingested = $now, "
            "c.created_at = $now, c.updated_at = $now, "
            "c.desc_text = $empty, c.tags = $empty, "
            "c.t_valid_from = $now, c.t_valid_to = $empty, "
            "c.t_created = $now, c.t_expired = $empty, c.t_superseded_by = $empty, "
            "c.namespace = $ns, c.et_source = $empty, c.vectors_pending = $vp "
            "ON MATCH SET c.source = $src, c.source_type = $stype, c.updated_at = $now",
            parameters={"id": chunk_id, "now": now, "src": source,
                        "stype": source_type, "tsc": src_created, "empty": "",
                        "ns": "memory", "vp": True},
        )

    def is_chunk_processed(self, chunk_id: str) -> bool:
        row = self._query_one("MATCH (c:Chunk {id: $id}) RETURN c.id", {"id": chunk_id})
        return row is not None

    def filter_unprocessed(self, chunk_ids: list[str]) -> list[str]:
        if not chunk_ids:
            return []
        processed = set()
        for cid in chunk_ids:
            if self.is_chunk_processed(cid):
                processed.add(cid)
        return [cid for cid in chunk_ids if cid not in processed]

    # ── Access tracking + provenance ─────────────────────────────────

    def record_access(self, entity_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "MATCH (c:Concept {id: $id}) SET c.access_count = c.access_count + 1, c.last_accessed = $now",
            parameters={"id": entity_id, "now": now},
        )

    def record_edge_access(self, source_id: str, target_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._exec_safe(
            f"MATCH (a:Concept {{id: '{source_id}'}})-[r:RelatesTo]->(b:Concept {{id: '{target_id}'}}) "
            f"SET r.access_count = r.access_count + 1, r.last_accessed = '{now}'"
        )

    def add_provenance(self, entity_id: str, source_provider: str,
                       source_chunk_id: str = "", llm_model: str = "",
                       source: str = "", source_type: str = "") -> str:
        prov_id = f"prov-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        if source_chunk_id:
            # Upsert chunk node with source info (may already exist)
            self._conn.execute(
                "MERGE (ch:Chunk {id: $cid}) "
                "ON CREATE SET ch.name = $cid, ch.source = $src, ch.source_type = $stype, "
                "ch.t_source_created = $now, ch.t_ingested = $now, "
                "ch.created_at = $now, ch.updated_at = $now, "
                "ch.desc_text = $empty, ch.tags = $empty, "
                "ch.t_valid_from = $now, ch.t_valid_to = $empty, "
                "ch.t_created = $now, ch.t_expired = $empty, ch.t_superseded_by = $empty, "
                "ch.namespace = $ns, ch.et_source = $empty, ch.vectors_pending = $vp",
                parameters={"cid": source_chunk_id, "now": now,
                            "src": source, "stype": source_type, "empty": "",
                            "ns": "memory", "vp": True},
            )
            # Create provenance edge (full system columns per ontology DDL)
            self._conn.execute(
                "MATCH (c:Concept {id: $eid}), (ch:Chunk {id: $cid}) "
                "CREATE (c)-[:HasProvenance {"
                "id: $eid_edge, name: $empty, "
                "source_provider: $prov, llm_model: $llm, "
                "relation_type: $rtype, strength: $strength, "
                "created_at: $now, updated_at: $now, "
                "t_valid_from: $now, t_valid_to: $empty, "
                "t_created: $now, t_expired: $empty, t_superseded_by: $empty, "
                "namespace: $ns, et_source: $empty"
                "}]->(ch)",
                parameters={"eid": entity_id, "cid": source_chunk_id,
                            "eid_edge": prov_id, "empty": "",
                            "prov": source_provider, "llm": llm_model,
                            "rtype": "has_provenance", "strength": 1.0,
                            "now": now, "ns": "memory"},
            )
        return prov_id

    def get_concept_sources(self, concept_id: str) -> list[dict]:
        """Return source info for each chunk that produced this concept.

        Each dict has: source (path), source_type, provider, timestamp.
        This is what Opus needs to reason about cross-system grounding.
        """
        rows = self._query_all(
            "MATCH (c:Concept {id: $id})-[p:HasProvenance]->(ch:Chunk) "
            "WHERE p.t_expired IS NULL OR p.t_expired = '' "
            "RETURN ch.source, ch.source_type, p.source_provider, p.t_created",
            {"id": concept_id},
        )
        return [
            {"source": r[0] or "", "source_type": r[1] or "",
             "provider": r[2] or "", "timestamp": r[3] or ""}
            for r in rows
        ]

    def get_provenance(self, entity_id: str) -> list[dict]:
        rows = self._query_all(
            "MATCH (c:Concept {id: $id})-[p:HasProvenance]->(ch:Chunk) "
            "WHERE p.t_expired IS NULL OR p.t_expired = '' "
            "RETURN ch.id, p.source_provider, p.llm_model, p.t_created",
            {"id": entity_id},
        )
        return [
            {"entity_id": entity_id, "source_chunk_id": r[0],
             "source_provider": r[1], "llm_model": r[2], "created_at": r[3]}
            for r in rows
        ]

    # ── Entity resolution + co-occurrence ────────────────────────────

    def find_similar_concept(self, name: str, threshold: float = 0.85) -> dict | None:
        from difflib import SequenceMatcher
        normalized = name.lower().strip()
        norm_id = name.lower().strip().replace(" ", "-").replace("/", "-")[:60]

        for c in self.list_concepts(limit=1000):
            if norm_id == c["id"]:
                return c
            score = SequenceMatcher(None, normalized, c["name"].lower().strip()).ratio()
            if score >= threshold:
                return c
        return None

    def merge_concept(self, source_id: str, target_id: str) -> None:
        source = self.get_concept(source_id)
        target = self.get_concept(target_id)
        if not source or not target:
            return

        new_freq = target["frequency"] + source["frequency"]
        self._conn.execute(
            "MATCH (c:Concept {id: $id}) SET c.frequency = $freq",
            parameters={"id": target_id, "freq": new_freq},
        )
        self._conn.execute(
            "MATCH (c:Concept {id: $id}) SET c.canonical_id = $canonical",
            parameters={"id": source_id, "canonical": target_id},
        )

    def add_co_occurrence(self, chunk_id: str, concept_ids: list[str],
                          context: str = "") -> str:
        # Store as a property on chunk node (simplified)
        cooccur_id = f"cooccur-{uuid.uuid4().hex[:12]}"
        return cooccur_id

    def get_co_occurrences(self, concept_id: str) -> list[dict]:
        return []  # TODO: implement with hyperedge pattern

    # ── Living graph ─────────────────────────────────────────────────

    def effective_weight(self, source_id: str, target_id: str,
                         decay_rate: float = 0.95,
                         source_age_aware: bool = True) -> float:
        """Read-time decayed edge weight. Delegates to the physarum plugin.

        source_age_aware: when True, decay uses max(idle_since_access,
        age_since_valid_from) so edges from old evidence stay decayed
        even after a fresh sync touches last_accessed.
        """
        row = self._query_one(
            "MATCH (a:Concept {id: $src})-[r:RelatesTo]->(b:Concept {id: $tgt}) "
            "RETURN r.weight, r.last_accessed, r.t_valid_from",
            {"src": source_id, "tgt": target_id},
        )
        if not row:
            return 0.0
        base = row[0] or 1.0
        last = row[1] or ""
        vf = row[2] or ""
        from extended_thinking.algorithms.decay.physarum import PhysarumDecay
        decay = PhysarumDecay(decay_rate=decay_rate, source_age_aware=source_age_aware)
        return decay.compute_effective_weight(
            base_weight=base, last_accessed=last, t_valid_from=vf
        )

    def spread_activation(self, seed_ids: list[str], depth: int = 3,
                          decay_per_hop: float = 0.7,
                          budget: int = 100) -> list[tuple[str, float]]:
        """Spreading activation via Cypher variable-length paths."""
        scores: dict[str, float] = {sid: 1.0 for sid in seed_ids}

        # Get all edges with weights
        rows = self._query_all(
            "MATCH (a:Concept)-[r:RelatesTo]-(b:Concept) RETURN a.id, b.id, r.weight"
        )
        adj: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            adj.setdefault(r[0], []).append((r[1], r[2] or 1.0))

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

        results = [(cid, score) for cid, score in scores.items() if cid not in seed_ids]
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def active_nodes(self, k: int = 10, *,
                     namespace: str | None = None) -> list[dict]:
        """Top-k concepts by activity score.

        Delegates to the active activity_score plugin (default:
        recency_weighted). Ships as a plugin so users can swap scoring via
        `[algorithms.activity_score.*]` in config.

        `namespace` (ADR 013 C2) scopes the ranking.
        """
        from extended_thinking.algorithms import (
            AlgorithmContext,
            build_config_from_settings,
            get_active,
        )
        from extended_thinking.config import settings
        config = build_config_from_settings(settings.algorithms)
        algs = get_active("activity_score", config)
        if not algs:
            return []
        ctx = AlgorithmContext(kg=self, namespace=namespace, params={"top_k": k})
        return algs[0].run(ctx)

    # ── Graph queries (native Cypher) ────────────────────────────────

    def get_graph_overview(self) -> dict:
        """Overview using Cypher for clusters, bridges, isolated."""
        concepts = self.list_concepts(limit=500)

        # Connected components via BFS (same logic, but could use Cypher paths)
        all_rels = self._query_all(
            "MATCH (a:Concept)-[:RelatesTo]-(b:Concept) RETURN DISTINCT a.id, b.id"
        )
        adj: dict[str, set[str]] = {}
        for c in concepts:
            adj[c["id"]] = set()
        for r in all_rels:
            adj.setdefault(r[0], set()).add(r[1])
            adj.setdefault(r[1], set()).add(r[0])

        visited: set[str] = set()
        clusters: list[dict] = []
        concept_map = {c["id"]: c for c in concepts}

        for c in concepts:
            if c["id"] in visited:
                continue
            component: list[str] = []
            queue = [c["id"]]
            while queue:
                nid = queue.pop(0)
                if nid in visited:
                    continue
                visited.add(nid)
                component.append(nid)
                for neighbor in adj.get(nid, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            cluster_concepts = [concept_map[nid] for nid in component if nid in concept_map]
            if cluster_concepts:
                clusters.append({"size": len(cluster_concepts), "concepts": cluster_concepts})

        clusters.sort(key=lambda c: c["size"], reverse=True)

        connected = set()
        for r in all_rels:
            connected.add(r[0])
            connected.add(r[1])
        isolated = [c for c in concepts if c["id"] not in connected]

        # Bridges: delegate to the bridges plugin (ADR 009).
        # The plugin returns enriched data; we strip to bare concepts for
        # backward-compat with get_graph_overview's legacy consumers.
        bridges = self._compute_bridges_via_plugin(concept_map)

        total_rels = self._query_one("MATCH ()-[r:RelatesTo]->() RETURN count(r)")
        rel_count = total_rels[0] if total_rels else 0

        return {
            "total_concepts": len(concepts),
            "total_relationships": rel_count,
            "total_wisdoms": len(self.list_wisdoms(limit=1000)),
            "clusters": clusters,
            "bridges": bridges,
            "isolated": isolated,
        }

    def _compute_bridges_via_plugin(self, concept_map: dict) -> list[dict]:
        """Delegate bridge detection to the bridges plugin family.

        Returns bare concept dicts (matches legacy get_graph_overview contract).
        If no plugin is registered (e.g., algorithms module not imported), falls
        back to an empty list.
        """
        try:
            from extended_thinking.algorithms import AlgorithmContext, get_active
        except ImportError:
            return []

        algs = get_active("bridges")
        if not algs:
            return []

        # Use the first active plugin (typically top_percentile).
        ctx = AlgorithmContext(kg=self)
        results = algs[0].run(ctx)
        return [r["concept"] for r in results if r.get("concept")]

    def find_path(self, from_id: str, to_id: str) -> list[dict] | None:
        """Shortest path via Cypher variable-length traversal."""
        if from_id == to_id:
            c = self.get_concept(from_id)
            return [c] if c else None

        # Try increasing lengths, return nodes(p) directly
        for max_len in [2, 4, 8, 16]:
            rows = self._query_all(
                f"MATCH p = (a:Concept {{id: $src}})-[:RelatesTo*1..{max_len}]-(b:Concept {{id: $tgt}}) "
                f"RETURN nodes(p) LIMIT 1",
                {"src": from_id, "tgt": to_id},
            )
            if rows:
                path_nodes = rows[0][0]
                return [self._concept_row_to_dict(n) for n in path_nodes]
        return None

    def get_neighborhood(self, concept_id: str) -> dict | None:
        concept = self.get_concept(concept_id)
        if not concept:
            return None

        connections = []
        rows = self._query_all(
            "MATCH (a:Concept {id: $id})-[r:RelatesTo]-(b:Concept) "
            "RETURN b, r.weight, r.context",
            {"id": concept_id},
        )
        for r in rows:
            other = self._concept_row_to_dict(r[0])
            other["weight"] = r[1]
            other["context"] = r[2]
            connections.append(other)

        # Related wisdoms
        related_wisdoms = []
        for w in self.list_wisdoms(limit=10):
            if concept_id in w.get("related_concept_ids", []):
                related_wisdoms.append(w)

        return {
            "concept": concept,
            "connections": connections,
            "related_wisdoms": related_wisdoms,
        }

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self, as_of: str | None = None,
                  *, namespace: str | None = None) -> dict:
        """Graph-wide counts.

        `as_of` restricts to state at that point in time.
        `namespace` (ADR 013 C2) restricts to one namespace; `None` spans all.
        """
        params: dict = {}
        c_clauses: list[str] = []
        r_clauses: list[str] = []
        w_clauses: list[str] = []
        if as_of:
            params["as_of"] = as_of
            c_clauses.append("c.t_valid_from <= $as_of "
                             "AND (c.t_expired = '' OR c.t_expired > $as_of)")
            r_clauses.append("r.t_valid_from <= $as_of "
                             "AND (r.t_valid_to = '' OR r.t_valid_to > $as_of)")
            w_clauses.append("w.t_created <= $as_of")
        else:
            r_clauses.append("(r.t_expired IS NULL OR r.t_expired = '')")
        if namespace is not None:
            params["ns"] = namespace
            c_clauses.append("c.namespace = $ns")
            r_clauses.append("r.namespace = $ns")
            w_clauses.append("w.namespace = $ns")

        def _where(cs: list[str]) -> str:
            return "WHERE " + " AND ".join(cs) + " " if cs else ""

        concepts = self._query_one(
            f"MATCH (c:Concept) {_where(c_clauses)}RETURN count(c)", params,
        )
        rels = self._query_one(
            f"MATCH ()-[r:RelatesTo]->() {_where(r_clauses)}RETURN count(r)", params,
        )
        wisdoms = self._query_one(
            f"MATCH (w:Wisdom) {_where(w_clauses)}RETURN count(w)", params,
        )
        return {
            "total_concepts": concepts[0] if concepts else 0,
            "total_relationships": rels[0] if rels else 0,
            "total_wisdoms": wisdoms[0] if wisdoms else 0,
        }

    def diff(
        self,
        date_from: str,
        date_to: str,
        *,
        node_types: list[str] | None = None,
        edge_types: list[str] | None = None,
        property_match: dict | None = None,
        namespace: str | None = None,
    ) -> dict:
        """Return what changed between two points in time (ADR 013 C5).

        Default (no filters): every registered node type is scanned, every
        registered edge type is scanned, unscoped. Pass `node_types` /
        `edge_types` to restrict; pass `namespace` to scope to one slice;
        pass `property_match` to restrict by column equality on the node
        side (edges ignore it for now — keep queries legible).

        Result shape:
            {
              "window": {"from", "to"},
              "filters": {...echo of filters...},
              "nodes_added":    [{_type, ...columns...}, ...]
              "nodes_expired":  [...]
              "edges_added":    [{_type, source_id, target_id, t_created, ...}]
              "edges_expired":  [...]
            }

        Legacy keys `concepts_added` / `concepts_deprecated` /
        `edges_created` / `edges_expired` remain for backwards-compat with
        callers that predate the generic version.
        """
        node_tables = list(node_types) if node_types else list(self._ontology.node_tables)
        edge_tables = list(edge_types) if edge_types else list(self._ontology.edge_tables)
        property_match = property_match or {}
        # Verify requested types exist (fail loud, ADR 013 invariant #2).
        unknown_nodes = set(node_tables) - set(self._ontology.node_tables)
        if unknown_nodes:
            raise ValueError(
                f"diff: unknown node_types {sorted(unknown_nodes)!r}; "
                f"ontology has {sorted(self._ontology.node_tables)}"
            )
        unknown_edges = set(edge_tables) - set(self._ontology.edge_tables)
        if unknown_edges:
            raise ValueError(
                f"diff: unknown edge_types {sorted(unknown_edges)!r}; "
                f"ontology has {sorted(self._ontology.edge_tables)}"
            )

        base_params = {"d1": date_from, "d2": date_to}
        if namespace is not None:
            base_params["ns"] = namespace

        def _node_filters() -> str:
            clauses: list[str] = []
            if namespace is not None:
                clauses.append("n.namespace = $ns")
            for i, (k, _) in enumerate(property_match.items()):
                clauses.append(f"n.{k} = $p_{i}")
            return (" AND " + " AND ".join(clauses)) if clauses else ""

        def _edge_filters() -> str:
            if namespace is None:
                return ""
            return " AND r.namespace = $ns"

        prop_params = {f"p_{i}": v for i, (_, v) in enumerate(property_match.items())}

        # ── Nodes ────────────────────────────────────────────────────
        nodes_added: list[dict] = []
        nodes_expired: list[dict] = []
        for t in node_tables:
            add_rows = self._query_all(
                f"MATCH (n:{t}) WHERE n.t_valid_from > $d1 AND n.t_valid_from <= $d2"
                f"{_node_filters()} "
                f"RETURN n ORDER BY n.t_valid_from",
                {**base_params, **prop_params},
            )
            for r in add_rows:
                d = self._flatten_kuzu_node(r[0])
                d["_type"] = t
                nodes_added.append(d)

            exp_rows = self._query_all(
                f"MATCH (n:{t}) WHERE n.t_expired > $d1 AND n.t_expired <= $d2"
                f"{_node_filters()} "
                f"RETURN n ORDER BY n.t_expired",
                {**base_params, **prop_params},
            )
            for r in exp_rows:
                d = self._flatten_kuzu_node(r[0])
                d["_type"] = t
                nodes_expired.append(d)

        # ── Edges ────────────────────────────────────────────────────
        edges_added: list[dict] = []
        edges_expired: list[dict] = []
        for t in edge_tables:
            add_rows = self._query_all(
                f"MATCH (a)-[r:{t}]->(b) "
                f"WHERE r.t_created > $d1 AND r.t_created <= $d2"
                f"{_edge_filters()} "
                f"RETURN a.id, b.id, r.t_created, r.id",
                base_params,
            )
            for r in add_rows:
                edges_added.append({
                    "_type": t,
                    "source_id": r[0], "target_id": r[1],
                    "t_created": r[2], "id": r[3],
                })
            exp_rows = self._query_all(
                f"MATCH (a)-[r:{t}]->(b) "
                f"WHERE r.t_expired > $d1 AND r.t_expired <= $d2"
                f"{_edge_filters()} "
                f"RETURN a.id, b.id, r.t_expired, r.t_superseded_by, r.id",
                base_params,
            )
            for r in exp_rows:
                edges_expired.append({
                    "_type": t,
                    "source_id": r[0], "target_id": r[1],
                    "t_expired": r[2], "superseded_by": r[3] or "",
                    "id": r[4],
                })

        # Back-compat: legacy callers expect the Concept/RelatesTo-only keys.
        legacy_concepts_added = [n for n in nodes_added if n["_type"] == "Concept"]
        legacy_concepts_deprecated = [n for n in nodes_expired if n["_type"] == "Concept"]
        legacy_edges_created = [e for e in edges_added if e["_type"] == "RelatesTo"]
        legacy_edges_expired = [e for e in edges_expired if e["_type"] == "RelatesTo"]

        return {
            "window": {"from": date_from, "to": date_to},
            "filters": {
                "node_types": node_types,
                "edge_types": edge_types,
                "property_match": property_match or None,
                "namespace": namespace,
            },
            # Generic, ADR 013 C5 shape:
            "nodes_added": nodes_added,
            "nodes_expired": nodes_expired,
            "edges_added": edges_added,
            "edges_expired": edges_expired,
            # Legacy keys (pre-C5):
            "concepts_added": legacy_concepts_added,
            "concepts_deprecated": legacy_concepts_deprecated,
            "edges_created": legacy_edges_created,
            "edges_expired_legacy": legacy_edges_expired,
        }

    @staticmethod
    def _flatten_kuzu_node(node) -> dict:
        """Kuzu returns node rows as dicts already; strip internals."""
        if isinstance(node, dict):
            d = dict(node)
        else:
            d = dict(node) if hasattr(node, "__iter__") else {}
        d.pop("_id", None)
        d.pop("_label", None)
        if "desc_text" in d:
            d["description"] = d.pop("desc_text")
        return d

    def supersede_edge(self, source_id: str, target_id: str,
                       new_edge_ref: str = "", reason: str = "") -> bool:
        """Mark an existing edge as superseded. Sets t_valid_to, t_expired, t_superseded_by.

        Called by contradiction detection when a new edge replaces this one.
        Returns True if an edge was found and updated.
        """
        now = datetime.now(timezone.utc).isoformat()
        # Check if the edge exists and is currently live
        existing = self._query_one(
            "MATCH (a:Concept {id: $src})-[r:RelatesTo]->(b:Concept {id: $tgt}) "
            "WHERE r.t_expired IS NULL OR r.t_expired = '' "
            "RETURN r.weight",
            {"src": source_id, "tgt": target_id},
        )
        if not existing:
            return False
        self._conn.execute(
            "MATCH (a:Concept {id: $src})-[r:RelatesTo]->(b:Concept {id: $tgt}) "
            "WHERE r.t_expired IS NULL OR r.t_expired = '' "
            "SET r.t_valid_to = $now, r.t_expired = $now, r.t_superseded_by = $ref",
            parameters={"src": source_id, "tgt": target_id, "now": now, "ref": new_edge_ref},
        )
        return True

    @property
    def schema_version(self) -> int:
        return 3  # Kuzu store is always at latest

    # ── Private helpers ──────────────────────────────────────────────

    def _concept_row_to_dict(self, node: dict) -> dict:
        """Kuzu node struct → flat public dict.

        The ontology columns are the source of truth (ADR 013). The only
        translation this function does is desc_text → description to hide
        the Kuzu reserved-word escape behind the public name.
        """
        if isinstance(node, dict):
            d = dict(node)
        else:
            d = dict(node) if hasattr(node, '__iter__') else {}

        if "desc_text" in d:
            d["description"] = d.pop("desc_text")

        # Defaults so callers can read familiar keys without KeyError.
        d.setdefault("id", "")
        d.setdefault("name", "")
        d.setdefault("category", "")
        d.setdefault("description", "")
        d.setdefault("source_quote", "")
        d.setdefault("frequency", 1)
        d.setdefault("first_seen", "")
        d.setdefault("last_seen", "")
        d.setdefault("access_count", 0)
        d.setdefault("last_accessed", "")
        d.setdefault("canonical_id", "")
        d.setdefault("status", "")

        d.pop("_id", None)
        d.pop("_label", None)
        return d

    def _wisdom_row_to_dict(self, node: dict) -> dict:
        if isinstance(node, dict):
            d = dict(node)
        else:
            d = {}

        if "desc_text" in d:
            d["description"] = d.pop("desc_text")

        d.setdefault("id", "")
        d.setdefault("title", "")
        d.setdefault("description", "")
        d.setdefault("wisdom_type", "wisdom")
        d.setdefault("status", "pending")
        d.setdefault("based_on_sessions", 0)
        d.setdefault("based_on_concepts", 0)
        d.setdefault("created_at", "")

        # Parse related_concept_ids from JSON string
        ids_raw = d.get("related_concept_ids", "[]")
        if isinstance(ids_raw, str):
            try:
                d["related_concept_ids"] = json.loads(ids_raw)
            except (json.JSONDecodeError, TypeError):
                d["related_concept_ids"] = []
        d.pop("_id", None)
        d.pop("_label", None)

        return d


