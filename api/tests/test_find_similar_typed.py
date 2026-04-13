"""ADR 013 C6: typed vector similarity.

`GraphStore.find_similar_typed(query, node_type, namespace, threshold, k)`
lets a programmatic consumer (autoresearch-ET) ask "have we seen
something close to this before?" across any registered node type, scoped
by namespace.

Design notes under test:
  - Indexing happens automatically on `GraphStore.insert` when a
    VectorStore is attached.
  - Metadata uses `et_` prefix (`et_node_type`, `et_namespace`,
    `et_source`) to avoid colliding with consumer metadata.
  - `vectors_pending` is flipped to false after a successful index.
  - `require_indexed=True` (default) excludes rows the indexer hasn't
    caught up on.
  - Indexing failure is non-fatal — vectors_pending stays true so a
    future retry can finish the work (ADR 013's honest eventual-
    consistency stance).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from extended_thinking.storage.graph_store import GraphStore
from extended_thinking.storage.vector_protocol import VectorResult
from extended_thinking._schema import models as m


# ── A deterministic in-memory VectorStore stand-in ────────────────────

@dataclass
class DictVectorStore:
    """Tiny in-memory VectorStore used by tests.

    Scores are substring overlap (good enough to drive deterministic
    rankings without a real embedder). Supports metadata `where` filters
    the same way ChromaDB does.
    """
    items: dict[str, dict] = field(default_factory=dict)
    _add_should_fail: bool = False

    def add(self, id: str, text: str, metadata: dict) -> None:
        if self._add_should_fail:
            raise RuntimeError("simulated vector index failure")
        self.items[id] = {"text": text, "metadata": dict(metadata)}

    def search(self, query: str, limit: int = 20,
               where: dict | None = None) -> list[VectorResult]:
        where = where or {}
        # Flatten ChromaDB's $and-of-single-key dicts into a simple dict.
        flat: dict = {}
        if "$and" in where:
            for clause in where["$and"]:
                flat.update(clause)
        else:
            flat = where
        q = query.lower()
        out: list[VectorResult] = []
        for nid, row in self.items.items():
            md = row["metadata"]
            if any(md.get(k) != v for k, v in flat.items()):
                continue
            text = row["text"].lower()
            # Toy score: fraction of query tokens present in the text
            tokens = [t for t in q.split() if t]
            if not tokens:
                score = 0.0
            else:
                hits = sum(1 for t in tokens if t in text)
                score = hits / len(tokens)
            out.append(VectorResult(id=nid, content=row["text"],
                                    score=score, metadata=md))
        out.sort(key=lambda r: r.score, reverse=True)
        return out[:limit]

    def delete(self, ids):
        for nid in ids:
            self.items.pop(nid, None)

    def count(self) -> int:
        return len(self.items)

    def embed(self, texts):  # not used by C6 tests
        return [[0.0] for _ in texts]


@pytest.fixture
def vstore():
    return DictVectorStore()


@pytest.fixture
def kg(vstore):
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "kg", vectors=vstore)


# ── Indexing on insert ────────────────────────────────────────────────

class TestIndexOnInsert:

    def test_insert_also_indexes(self, kg, vstore):
        c = m.Concept(
            id="c-1", name="sparse attention",
            category=m.ConceptCategory.topic,
            description="transformer technique",
        )
        kg.insert(c, namespace="research", source="unit-test")
        assert "c-1" in vstore.items

    def test_indexed_metadata_carries_et_prefix(self, kg, vstore):
        c = m.Concept(id="c-1", name="x", category=m.ConceptCategory.topic,
                      description="y")
        kg.insert(c, namespace="research", source="writer-x")
        md = vstore.items["c-1"]["metadata"]
        assert md["et_node_type"] == "Concept"
        assert md["et_namespace"] == "research"
        assert md["et_source"] == "writer-x"
        assert md["source_type"] == "typed_node"

    def test_indexed_text_combines_name_and_description(self, kg, vstore):
        c = m.Concept(id="c-1", name="sparse attention",
                      category=m.ConceptCategory.topic,
                      description="reduces transformer quadratic cost")
        kg.insert(c)
        txt = vstore.items["c-1"]["text"]
        assert "sparse attention" in txt
        assert "reduces transformer quadratic cost" in txt

    def test_vectors_pending_flips_to_false_after_index(self, kg):
        c = m.Concept(id="c-1", name="x", category=m.ConceptCategory.topic,
                      description="y")
        kg.insert(c)
        row = kg._query_one(
            "MATCH (n:Concept {id: 'c-1'}) RETURN n.vectors_pending"
        )
        assert row == [False]

    def test_index_failure_leaves_vectors_pending_true(self, vstore):
        """A simulated VectorStore failure must not abort the insert; Kuzu
        stays consistent, and vectors_pending stays true so a retry can
        finish later."""
        vstore._add_should_fail = True
        with tempfile.TemporaryDirectory() as tmp:
            kg = GraphStore(Path(tmp) / "kg", vectors=vstore)
            c = m.Concept(id="c-1", name="x",
                          category=m.ConceptCategory.topic, description="y")
            nid = kg.insert(c)  # should not raise
            assert nid == "c-1"
            row = kg._query_one(
                "MATCH (n:Concept {id: 'c-1'}) RETURN n.vectors_pending"
            )
            assert row == [True]

    def test_edges_not_indexed(self, kg, vstore):
        """Typed edges live only in Kuzu; C6 is node-level retrieval."""
        kg.insert(m.Concept(id="a", name="A",
                            category=m.ConceptCategory.topic))
        kg.insert(m.Concept(id="b", name="B",
                            category=m.ConceptCategory.topic))
        e = m.RelatesTo(id="e-1", source_id="a", target_id="b",
                        relation_type="semantic", weight=0.9)
        kg.insert(e)
        assert "e-1" not in vstore.items

    def test_without_vectors_insert_still_works(self):
        """No VectorStore configured → insert just writes to Kuzu.
        vectors_pending stays at its to_kuzu_row default (true)."""
        with tempfile.TemporaryDirectory() as tmp:
            kg = GraphStore(Path(tmp) / "kg")  # no vectors
            c = m.Concept(id="c-1", name="x",
                          category=m.ConceptCategory.topic, description="y")
            kg.insert(c)
            row = kg._query_one(
                "MATCH (n:Concept {id: 'c-1'}) RETURN n.vectors_pending"
            )
            assert row == [True]


# ── find_similar_typed ────────────────────────────────────────────────

class TestFindSimilarTyped:

    def _seed(self, kg):
        kg.insert(m.Concept(
            id="sa", name="sparse attention",
            category=m.ConceptCategory.topic,
            description="efficient transformer variant",
        ), namespace="research")
        kg.insert(m.Concept(
            id="fa", name="flash attention",
            category=m.ConceptCategory.topic,
            description="memory-efficient attention kernel",
        ), namespace="research")
        kg.insert(m.Concept(
            id="mem", name="my grocery list",
            category=m.ConceptCategory.topic,
            description="memory-side unrelated concept",
        ), namespace="memory")

    def test_basic_retrieval(self, kg):
        self._seed(kg)
        hits = kg.find_similar_typed("sparse attention", "Concept",
                                     threshold=0.1, k=5)
        ids = [h[0] for h in hits]
        assert "sa" in ids

    def test_namespace_scopes_results(self, kg):
        self._seed(kg)
        r_hits = kg.find_similar_typed(
            "attention", "Concept",
            threshold=0.1, k=10, namespace="research",
        )
        m_hits = kg.find_similar_typed(
            "memory grocery", "Concept",
            threshold=0.1, k=10, namespace="memory",
        )
        r_ids = {h[0] for h in r_hits}
        m_ids = {h[0] for h in m_hits}
        assert r_ids.issubset({"sa", "fa"})
        assert m_ids.issubset({"mem"})
        assert not (r_ids & m_ids)

    def test_threshold_filters_low_scores(self, kg):
        self._seed(kg)
        # A query that overlaps nothing should return no hits
        hits = kg.find_similar_typed("pineapple upside-down cake", "Concept",
                                     threshold=0.5, k=5)
        assert hits == []

    def test_k_limits_result_count(self, kg):
        self._seed(kg)
        hits = kg.find_similar_typed("attention", "Concept",
                                     threshold=0.0, k=1, namespace="research")
        assert len(hits) <= 1

    def test_unknown_node_type_raises(self, kg):
        with pytest.raises(ValueError, match="unknown node_type"):
            kg.find_similar_typed("x", "NotRegistered", threshold=0.1)

    def test_require_indexed_excludes_pending(self, kg, vstore):
        """When vectors_pending=true (indexing hasn't cleared), the
        default require_indexed=True hides the row from the result set."""
        self._seed(kg)
        # Force one row back into pending state without re-indexing
        kg._conn.execute(
            "MATCH (n:Concept {id: 'sa'}) SET n.vectors_pending = true"
        )
        hits = kg.find_similar_typed("sparse attention", "Concept",
                                     threshold=0.1, k=5,
                                     require_indexed=True)
        assert all(h[0] != "sa" for h in hits)

        # With require_indexed=False the same query should return it
        hits2 = kg.find_similar_typed("sparse attention", "Concept",
                                      threshold=0.1, k=5,
                                      require_indexed=False)
        assert any(h[0] == "sa" for h in hits2)

    def test_without_vector_store_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            kg = GraphStore(Path(tmp) / "kg")  # no vectors
            # even with data, no vectors → no hits
            kg.insert(m.Concept(id="c-1", name="x",
                                category=m.ConceptCategory.topic,
                                description="y"))
            assert kg.find_similar_typed("x", "Concept") == []


# ── MCP tool wiring ───────────────────────────────────────────────────

class TestEtFindSimilarTool:

    def test_tool_registered(self):
        from extended_thinking.mcp_server import TOOLS
        names = {t["name"] for t in TOOLS}
        assert "et_find_similar" in names

    def test_tool_required_fields(self):
        from extended_thinking.mcp_server import TOOLS
        tool = next(t for t in TOOLS if t["name"] == "et_find_similar")
        required = tool["inputSchema"]["required"]
        assert "query" in required
        assert "node_type" in required
