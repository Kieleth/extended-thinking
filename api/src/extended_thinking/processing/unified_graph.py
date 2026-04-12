"""UnifiedGraph — federated queries across ConceptStore + provider KG.

One traversal crosses both systems transparently. ET concepts and
provider entities live in the same graph, connected by edges from
both systems.

Node namespace:
  et:{id}   — from ConceptStore (concepts, wisdom)
  mp:{id}   — from provider KG (entities)

Design: no data copying. Queries are virtual — each store is read
at query time and results are merged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from extended_thinking.processing.concept_store import ConceptStore
from extended_thinking.providers.protocol import Entity, Fact, KnowledgeGraphView


@dataclass
class GraphNode:
    """Unified node from any knowledge source."""
    id: str
    label: str
    node_type: str          # "concept", "entity", "wisdom"
    category: str           # "topic", "theme", "decision", "project", "unknown"
    source_system: str      # "et" or "mempalace"
    properties: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    """Unified edge from any knowledge source."""
    source_id: str
    target_id: str
    edge_type: str          # "RelatesTo", "has_bug", "implemented", etc.
    source_system: str      # "et" or "mempalace"
    weight: float = 1.0
    context: str = ""
    valid_from: str = ""
    valid_to: str | None = None


class UnifiedGraph:
    """Federated graph across ConceptStore + provider KG.

    Merges both into one queryable surface. Node IDs are prefixed
    to prevent collisions: et:{id} and mp:{id}.
    """

    def __init__(self, concept_store: ConceptStore,
                 kg_view: KnowledgeGraphView | None):
        self._et = concept_store
        self._kg = kg_view

    # ── Nodes ────────────────────────────────────────────────────────

    def all_nodes(self) -> list[GraphNode]:
        nodes = []

        # ET concepts
        for c in self._et.list_concepts(limit=1000):
            nodes.append(GraphNode(
                id=f"et:{c['id']}",
                label=c["name"],
                # ET Concepts are all `concept` at the unified-graph level;
                # the ontology category (topic/theme/etc.) rides along in properties.
                node_type="concept",
                category=c.get("category", ""),
                source_system="et",
                properties=c,
            ))

        # ET wisdom
        for w in self._et.list_wisdoms(limit=100):
            nodes.append(GraphNode(
                id=f"et:wisdom:{w['id']}",
                label=w["title"],
                node_type="wisdom",
                category="wisdom",
                source_system="et",
                properties=w,
            ))

        # Provider KG entities
        if self._kg:
            for e in self._kg.entities():
                eid = e.properties.get("id", e.name.lower().replace(" ", "_"))
                nodes.append(GraphNode(
                    id=f"mp:{eid}",
                    label=e.name,
                    node_type="entity",
                    category=e.entity_type,
                    source_system="mempalace",
                    properties=e.properties,
                ))

        return nodes

    # ── Edges ────────────────────────────────────────────────────────

    def _same_as_edges(self, nodes: list[GraphNode]) -> list[GraphEdge]:
        """Detect ET concepts and MP entities with matching names.

        Creates virtual SAME_AS edges to bridge the two subgraphs.
        This is the "anterior temporal lobe" equivalent: the transmodal
        hub that recognizes the same concept across different sources.
        """
        et_nodes = {n.label.lower().strip(): n for n in nodes if n.source_system == "et"}
        mp_nodes = {n.label.lower().strip(): n for n in nodes if n.source_system == "mempalace"}

        bridges = []
        for label, et_node in et_nodes.items():
            if label in mp_nodes:
                mp_node = mp_nodes[label]
                bridges.append(GraphEdge(
                    source_id=et_node.id,
                    target_id=mp_node.id,
                    edge_type="SAME_AS",
                    source_system="bridge",
                    weight=1.0,
                    context=f"Same concept across ET and provider: {et_node.label}",
                ))
        return bridges

    def all_edges(self) -> list[GraphEdge]:
        edges = []

        # ET relationships
        for c in self._et.list_concepts(limit=1000):
            for r in self._et.get_relationships(c["id"]):
                edge = GraphEdge(
                    source_id=f"et:{r['source_id']}",
                    target_id=f"et:{r['target_id']}",
                    edge_type=r.get("edge_type", "RelatesTo"),
                    source_system="et",
                    weight=r.get("weight", 1.0),
                    context=r.get("context", ""),
                    valid_from=r.get("valid_from", ""),
                    valid_to=r.get("valid_to"),
                )
                if edge not in edges:  # Simple dedup
                    edges.append(edge)

        # Provider KG triples
        if self._kg:
            for fact in self._kg.facts():
                edges.append(GraphEdge(
                    source_id=f"mp:{fact.subject}",
                    target_id=f"mp:{fact.object}",
                    edge_type=fact.predicate,
                    source_system="mempalace",
                    weight=fact.confidence,
                    context="",
                    valid_from=fact.valid_from,
                    valid_to=fact.valid_to,
                ))

        # SAME_AS bridges between ET and MP nodes with matching names
        edges.extend(self._same_as_edges(self.all_nodes()))

        return edges

    # ── Traversal ────────────────────────────────────────────────────

    def neighbors(self, node_id: str) -> list[GraphNode]:
        """All nodes connected to this node, across both stores."""
        adj = self._build_adjacency()
        neighbor_ids = adj.get(node_id, set())
        node_map = {n.id: n for n in self.all_nodes()}
        return [node_map[nid] for nid in neighbor_ids if nid in node_map]

    def find_path(self, from_id: str, to_id: str) -> list[GraphNode] | None:
        """BFS shortest path across the unified graph."""
        if from_id == to_id:
            node_map = {n.id: n for n in self.all_nodes()}
            return [node_map[from_id]] if from_id in node_map else None

        adj = self._build_adjacency()
        if from_id not in adj:
            return None

        visited: set[str] = {from_id}
        queue: list[list[str]] = [[from_id]]

        while queue:
            path = queue.pop(0)
            node = path[-1]

            for neighbor in adj.get(node, set()):
                if neighbor == to_id:
                    full_path = path + [neighbor]
                    node_map = {n.id: n for n in self.all_nodes()}
                    return [node_map[nid] for nid in full_path if nid in node_map]

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])

        return None

    def get_overview(self) -> dict:
        """Graph overview: clusters, bridges, isolated."""
        nodes = self.all_nodes()
        edges = self.all_edges()
        adj = self._build_adjacency()

        # Connected components via BFS
        visited: set[str] = set()
        clusters: list[dict] = []
        node_map = {n.id: n for n in nodes}

        for node in nodes:
            if node.id in visited:
                continue
            component: list[str] = []
            queue = [node.id]
            while queue:
                nid = queue.pop(0)
                if nid in visited:
                    continue
                visited.add(nid)
                component.append(nid)
                for neighbor in adj.get(nid, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)

            cluster_nodes = [node_map[nid] for nid in component if nid in node_map]
            if cluster_nodes:
                clusters.append({"size": len(cluster_nodes), "nodes": cluster_nodes})

        clusters.sort(key=lambda c: c["size"], reverse=True)

        # Isolated = degree 0
        connected = set()
        for e in edges:
            connected.add(e.source_id)
            connected.add(e.target_id)
        isolated = [n for n in nodes if n.id not in connected]

        # Bridges = degree >= 3
        degree = {nid: len(nbrs) for nid, nbrs in adj.items()}
        bridges = [node_map[nid] for nid, d in degree.items() if d >= 3 and nid in node_map]

        return {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "clusters": clusters,
            "bridges": bridges,
            "isolated": isolated,
        }

    def get_neighborhood(self, node_id: str) -> dict | None:
        """Node + all connections from both stores."""
        node_map = {n.id: n for n in self.all_nodes()}
        if node_id not in node_map:
            return None

        node = node_map[node_id]
        neighbor_nodes = self.neighbors(node_id)

        # Get edges for context
        all_edges = self.all_edges()
        relevant_edges = [
            e for e in all_edges
            if e.source_id == node_id or e.target_id == node_id
        ]

        return {
            "node": node,
            "neighbors": neighbor_nodes,
            "edges": relevant_edges,
        }

    # ── Private ──────────────────────────────────────────────────────

    def _build_adjacency(self) -> dict[str, set[str]]:
        """Build adjacency list from all edges."""
        adj: dict[str, set[str]] = {}
        for node in self.all_nodes():
            adj.setdefault(node.id, set())

        for edge in self.all_edges():
            adj.setdefault(edge.source_id, set()).add(edge.target_id)
            adj.setdefault(edge.target_id, set()).add(edge.source_id)

        return adj
