"""GraphitiProvider. Reads from a Graphiti temporal knowledge graph.

Graphiti is bitemporal from the ground up, which is the closest structural
match to ET's own KG. Episodes map to MemoryChunks, entity nodes map to
Entities, and edges map to Facts with valid_from / valid_to.

Graphiti's Python API is async and backed by Neo4j. This provider wraps the
async calls in asyncio.run for protocol compatibility. In hot paths a caller
should invoke these methods from a dedicated thread, not the event loop.

Stub status: protocol-compliant, matches the documented Graphiti API. Full
coverage of edge types and episode metadata is landed as real use demands.

Requires: pip install extended-thinking[graphiti]
         Plus a running Neo4j instance.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from extended_thinking.providers.protocol import Entity, Fact, KnowledgeGraphView, MemoryChunk

logger = logging.getLogger(__name__)


class GraphitiProvider:
    """Memory provider backed by a Graphiti instance.

    Graphiti requires Neo4j connection details. Defaults follow Graphiti's
    own documentation (bolt://localhost:7687, neo4j / neo4j).
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "neo4j",
        group_id: str | None = None,
    ):
        self._uri = uri
        self._user = user
        self._password = password
        self._group_id = group_id
        self._client = None

    @property
    def name(self) -> str:
        return "graphiti"

    def _get_client(self):
        if self._client is None:
            try:
                from graphiti_core import Graphiti
            except ImportError as e:
                raise ImportError(
                    "GraphitiProvider requires the graphiti-core package. "
                    "Install with: pip install extended-thinking[graphiti]"
                ) from e
            self._client = Graphiti(self._uri, self._user, self._password)
        return self._client

    def _run(self, coro):
        """Execute an async Graphiti call synchronously. Callers from an
        existing event loop should run the provider in a worker thread."""
        try:
            return asyncio.run(coro)
        except RuntimeError as e:
            if "already running" in str(e).lower():
                raise RuntimeError(
                    "GraphitiProvider cannot run from inside an event loop. "
                    "Invoke via asyncio.to_thread(provider.method, ...)."
                ) from e
            raise

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        try:
            client = self._get_client()
            edges = self._run(client.search(query=query, num_results=limit))
            return [self._edge_to_chunk(e) for e in edges or []]
        except Exception as e:
            logger.error("Graphiti search failed: %s", e)
            return []

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        try:
            client = self._get_client()
            kwargs = {"last_n": limit}
            if self._group_id:
                kwargs["group_ids"] = [self._group_id]
            episodes = self._run(client.retrieve_episodes(**kwargs))
            chunks = [self._episode_to_chunk(ep) for ep in episodes or []]
            if since:
                chunks = [c for c in chunks if c.timestamp >= since]
            chunks.sort(key=lambda c: c.timestamp, reverse=True)
            return chunks[:limit]
        except Exception as e:
            logger.error("Graphiti get_recent failed: %s", e)
            return []

    def get_entities(self) -> list[Entity]:
        try:
            client = self._get_client()
            # Graphiti exposes entity nodes via the driver session.
            # Keep the Cypher minimal. Real use will want pagination.
            query = "MATCH (n:Entity) RETURN n.name AS name, n.summary AS summary, labels(n) AS labels LIMIT 500"
            records = self._run(_run_cypher(client, query))
            return [
                Entity(
                    name=r.get("name", ""),
                    entity_type=_pick_entity_type(r.get("labels", [])),
                    properties={"summary": r.get("summary", "")},
                )
                for r in records or []
                if r.get("name")
            ]
        except Exception as e:
            logger.error("Graphiti get_entities failed: %s", e)
            return []

    def store_insight(self, title: str, description: str, related_concepts: list[str]) -> str:
        try:
            client = self._get_client()
            now = datetime.now(timezone.utc)
            body = f"# {title}\n\n{description}\n\nRelated: {', '.join(related_concepts)}"
            result = self._run(
                client.add_episode(
                    name=f"extended-thinking:{title}",
                    episode_body=body,
                    reference_time=now,
                    source_description="extended-thinking insight",
                    group_id=self._group_id,
                )
            )
            if result and hasattr(result, "uuid"):
                return str(result.uuid)
            return hashlib.sha256(f"{title}{now.isoformat()}".encode()).hexdigest()[:16]
        except Exception as e:
            logger.error("Graphiti store_insight failed: %s", e)
            return ""

    def get_insights(self) -> list[MemoryChunk]:
        try:
            chunks = self.get_recent(limit=200)
            return [c for c in chunks if c.metadata.get("source_description") == "extended-thinking insight"]
        except Exception as e:
            logger.error("Graphiti get_insights failed: %s", e)
            return []

    def get_stats(self) -> dict:
        try:
            client = self._get_client()
            cypher = (
                "MATCH (e:Episodic) WITH count(e) AS episodes "
                "MATCH (n:Entity) WITH episodes, count(n) AS entities "
                "RETURN episodes, entities"
            )
            records = self._run(_run_cypher(client, cypher))
            row = records[0] if records else {}
            return {
                "total_memories": int(row.get("episodes", 0) or 0),
                "total_entities": int(row.get("entities", 0) or 0),
                "last_updated": None,
                "provider": self.name,
                "uri": self._uri,
            }
        except Exception as e:
            logger.error("Graphiti get_stats failed: %s", e)
            return {"total_memories": 0, "provider": self.name}

    def get_knowledge_graph(self) -> KnowledgeGraphView | None:
        try:
            return GraphitiKGView(self)
        except Exception as e:
            logger.error("Failed to create Graphiti KG view: %s", e)
            return None

    def _episode_to_chunk(self, episode) -> MemoryChunk:
        get = _getter(episode)
        return MemoryChunk(
            id=str(get("uuid", "")),
            content=get("content") or get("episode_body") or "",
            source=get("source_description", "graphiti"),
            timestamp=_iso(get("valid_at") or get("created_at")),
            metadata={
                "group_id": get("group_id", ""),
                "source_description": get("source_description", ""),
            },
        )

    def _edge_to_chunk(self, edge) -> MemoryChunk:
        get = _getter(edge)
        fact = get("fact") or ""
        return MemoryChunk(
            id=str(get("uuid", "")),
            content=str(fact),
            source="graphiti-edge",
            timestamp=_iso(get("valid_at") or get("created_at")),
            metadata={
                "source_node": str(get("source_node_uuid", "")),
                "target_node": str(get("target_node_uuid", "")),
                "name": get("name", ""),
            },
        )


class GraphitiKGView:
    """Read-only view of a Graphiti KG for the UnifiedGraph layer."""

    def __init__(self, provider: GraphitiProvider):
        self._provider = provider

    def facts(self, subject: str | None = None) -> list[Fact]:
        try:
            client = self._provider._get_client()
            if subject:
                cypher = (
                    "MATCH (s:Entity {name: $name})-[r]->(t:Entity) "
                    "RETURN s.name AS subject, type(r) AS predicate, t.name AS object, "
                    "r.valid_at AS valid_from, r.invalid_at AS valid_to LIMIT 500"
                )
                params = {"name": subject}
            else:
                cypher = (
                    "MATCH (s:Entity)-[r]->(t:Entity) "
                    "RETURN s.name AS subject, type(r) AS predicate, t.name AS object, "
                    "r.valid_at AS valid_from, r.invalid_at AS valid_to LIMIT 500"
                )
                params = {}
            records = self._provider._run(_run_cypher(client, cypher, params))
            return [
                Fact(
                    subject=r.get("subject", ""),
                    predicate=r.get("predicate", ""),
                    object=r.get("object", ""),
                    valid_from=_iso(r.get("valid_from")),
                    valid_to=_iso_or_none(r.get("valid_to")),
                    source="graphiti",
                )
                for r in records or []
            ]
        except Exception as e:
            logger.error("Graphiti facts() failed: %s", e)
            return []

    def entities(self) -> list[Entity]:
        return self._provider.get_entities()

    def predicates(self) -> list[str]:
        try:
            client = self._provider._get_client()
            cypher = "MATCH ()-[r]->() RETURN DISTINCT type(r) AS predicate"
            records = self._provider._run(_run_cypher(client, cypher))
            return [r["predicate"] for r in records or [] if r.get("predicate")]
        except Exception as e:
            logger.error("Graphiti predicates() failed: %s", e)
            return []

    def neighbors(self, entity_id: str) -> list[str]:
        try:
            client = self._provider._get_client()
            cypher = (
                "MATCH (n:Entity {name: $name})--(m:Entity) "
                "RETURN DISTINCT m.name AS name LIMIT 500"
            )
            records = self._provider._run(_run_cypher(client, cypher, {"name": entity_id}))
            return [r["name"] for r in records or [] if r.get("name")]
        except Exception as e:
            logger.error("Graphiti neighbors() failed: %s", e)
            return []


async def _run_cypher(client, cypher: str, params: dict | None = None) -> list[dict]:
    """Execute a raw Cypher query against Graphiti's Neo4j driver.

    Graphiti exposes its driver as `client.driver`. This helper stays small
    because the exact surface can drift between Graphiti versions; callers
    should not rely on it beyond the stubs in this file."""
    driver = getattr(client, "driver", None)
    if driver is None:
        return []
    async with driver.session() as session:
        result = await session.run(cypher, params or {})
        return [dict(record) async for record in result]


def _getter(obj):
    """Return a uniform .get() across dicts and Pydantic models."""
    if isinstance(obj, dict):
        return obj.get
    def get(key, default=None):
        return getattr(obj, key, default)
    return get


def _iso(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _iso_or_none(value) -> str | None:
    s = _iso(value)
    return s or None


def _pick_entity_type(labels: list[str]) -> str:
    """Graphiti labels nodes with :Entity plus optional type labels."""
    for label in labels or []:
        if label and label not in {"Entity", "Episodic"}:
            return label
    return "concept"
