"""AT: research-loop narrative (ADR 013 C2 / C6 / C7).

Scenario (matches what autoresearch-ET does, using ET's generic Concept
as a stand-in for Hypothesis/Variant since that's all the shipped
ontology has — consumer projects add their own typed classes on top):

    1. A research namespace accumulates Concepts over time.
    2. Vector similarity surfaces candidate neighbours for a new idea
       ("have we tried something close?").
    3. An algorithm runs with write_back=True; its proposals land as
       ProposalBy edges with full provenance (algorithm + params +
       invoked_at + score).
    4. Namespace boundaries hold — a memory-side concept with the same
       shape never enters the research slice's results or stats.

Complements the unit-test layer by running the full story on real
ChromaDB + real Kuzu, with real embeddings and a real link-prediction
plugin.
"""

from __future__ import annotations

import json

import pytest

from extended_thinking.mcp_server import handle_tool_call
from extended_thinking.storage import StorageLayer
from schema.generated import models as m

pytestmark = pytest.mark.acceptance


@pytest.fixture
def research_kg(tmp_data_dir, monkeypatch):
    """Full StorageLayer (Kuzu + ChromaDB) wired into mcp_server so the
    MCP tools and GraphStore share one database."""
    import extended_thinking.mcp_server as srv
    from extended_thinking.processing.pipeline_v2 import Pipeline
    from extended_thinking.providers import get_provider

    storage = StorageLayer.default(tmp_data_dir / "rl")
    cached = Pipeline.from_storage(get_provider(), storage)
    monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)
    return cached


async def _mcp(name, args):
    return await handle_tool_call(name, args)


def _seed_research_graph(kg):
    """Three plausible research-shaped concepts + 1 memory-side noise."""
    seeds = [
        ("h-sparse", "sparse attention reduces inference latency",
         "top-k sparsity over attention scores; skip low scores"),
        ("h-flash", "flash attention uses tiling for memory efficiency",
         "fuse Q/K/V matmuls; avoid materializing full attention matrix"),
        ("h-linear", "linear attention approximates softmax",
         "kernel-based reformulation; linear in sequence length"),
    ]
    for cid, name, desc in seeds:
        kg.insert(
            m.Concept(
                id=cid, name=name,
                category=m.ConceptCategory.topic,
                description=desc,
                frequency=5,  # high enough to pass trigger thresholds
            ),
            namespace="research",
            source="autoresearch-et",
        )
    # Memory-side noise — same shape, different namespace
    kg.add_concept(
        "m-grocery", "my grocery list",
        "topic", "unrelated memory-side concept",
    )


# ── C2: namespace isolation ──────────────────────────────────────────

class TestNamespaceIsolation:
    """A research-loop consumer's reads, vector queries, and algorithm
    runs must not leak memory-side noise. The shipped ADR 013 C2
    machinery guarantees it; this AT exercises the full read stack."""

    def test_list_concepts_scopes_to_research(self, research_kg):
        _seed_research_graph(research_kg.store)
        rows = research_kg.store.list_concepts(namespace="research")
        assert {c["id"] for c in rows} == {"h-sparse", "h-flash", "h-linear"}
        assert "m-grocery" not in {c["id"] for c in rows}

    def test_stats_scope_correctly(self, research_kg):
        _seed_research_graph(research_kg.store)
        research = research_kg.store.get_stats(namespace="research")
        memory = research_kg.store.get_stats(namespace="memory")
        all_ns = research_kg.store.get_stats()

        assert research["total_concepts"] == 3
        assert memory["total_concepts"] == 1
        assert all_ns["total_concepts"] == 4


# ── C6: typed vector similarity ──────────────────────────────────────

class TestTypedVectorSimilarity:
    """Auto-indexed on insert; retrieval scoped by node_type + namespace
    pre-cosine so scores stay meaningful."""

    def test_close_neighbour_surfaces_from_research_namespace(self, research_kg):
        _seed_research_graph(research_kg.store)

        hits = research_kg.store.find_similar_typed(
            "sparsity for faster attention",
            "Concept",
            namespace="research",
            threshold=0.2,
            k=3,
        )
        assert hits, "expected at least one match"
        top_id = hits[0][0]
        # The semantically closest of the three
        assert top_id == "h-sparse", (
            f"expected 'h-sparse' top; got {top_id} in {hits}"
        )
        # Memory-side noise must not appear
        ids = {nid for nid, _ in hits}
        assert "m-grocery" not in ids

    def test_namespace_filter_excludes_memory(self, research_kg):
        """Even a query that closely matches the memory concept must
        return nothing when scoped to research."""
        _seed_research_graph(research_kg.store)
        hits = research_kg.store.find_similar_typed(
            "my grocery list",
            "Concept",
            namespace="research",
            threshold=0.05,
            k=5,
        )
        assert all(nid.startswith("h-") for nid, _ in hits), (
            f"memory noise leaked into research namespace: {hits}"
        )


# ── C7: algorithm write-back ─────────────────────────────────────────

class TestAlgorithmWriteBack:
    """Running an algorithm with write_back=True persists its proposals
    as ProposalBy edges. The audit trail ("what did the algorithm say
    at T?") becomes a graph query, not a re-run."""

    @pytest.mark.asyncio
    async def test_link_prediction_writeback_persists_proposals(self, research_kg):
        _seed_research_graph(research_kg.store)

        result = await _mcp("et_run_algorithm", {
            "algorithm": "textual_similarity",
            "params": {"threshold": 0.1, "top_k": 10},
            "namespace": "research",
            "write_back": True,
        })
        data = json.loads(result)
        assert data["algorithm"] == "textual_similarity"
        assert data["write_back"] is True
        assert data["proposals_written"] >= 1, data

        # The audit row survives with algorithm + parameters + score
        rows = research_kg.store._query_all(
            "MATCH (a:Concept)-[r:ProposalBy]->(b:Concept) "
            "WHERE r.namespace = 'research' "
            "RETURN r.algorithm, r.score, r.parameters_json, r.invoked_at"
        )
        assert rows, "ProposalBy edges should exist after write_back"
        algo, score, params_json, invoked_at = rows[0]
        assert algo == "textual_similarity"
        assert 0.0 <= score <= 1.0
        params = json.loads(params_json)
        assert params.get("threshold") == 0.1
        assert invoked_at  # non-empty ISO timestamp

    @pytest.mark.asyncio
    async def test_read_only_run_does_not_persist(self, research_kg):
        """write_back=False (default) runs the algorithm but leaves the
        graph untouched — for callers who just want the answer, not an
        audit row."""
        _seed_research_graph(research_kg.store)

        await _mcp("et_run_algorithm", {
            "algorithm": "textual_similarity",
            "params": {"threshold": 0.1},
            "namespace": "research",
            # write_back omitted → default False
        })
        rows = research_kg.store._query_all(
            "MATCH ()-[r:ProposalBy]->() RETURN count(r)"
        )
        assert rows[0][0] == 0

    @pytest.mark.asyncio
    async def test_proposal_provenance_queryable_after_fact(self, research_kg):
        """The audit-trail use case: minutes/hours/days later, someone
        asks 'what did textual_similarity say about h-sparse at time T?'
        — that's a graph query, no re-run."""
        _seed_research_graph(research_kg.store)
        await _mcp("et_run_algorithm", {
            "algorithm": "textual_similarity",
            "params": {"threshold": 0.1},
            "namespace": "research",
            "write_back": True,
        })

        rows = research_kg.store._query_all(
            "MATCH (a:Concept {id: 'h-sparse'})-[r:ProposalBy]-(b:Concept) "
            "WHERE r.algorithm = 'textual_similarity' "
            "AND r.namespace = 'research' "
            "RETURN b.id, r.score, r.invoked_at "
            "ORDER BY r.score DESC"
        )
        # We don't assert the exact target — score-based ranking may
        # prefer h-flash or h-linear depending on text — but there
        # must be at least one provenance record linked to h-sparse.
        assert rows, (
            "expected at least one ProposalBy edge touching h-sparse; "
            "got none"
        )
        for b_id, score, invoked_at in rows:
            assert b_id in {"h-flash", "h-linear"}
            assert invoked_at


# ── Combined: "a consumer session" ──────────────────────────────────

class TestFullConsumerSession:
    """End-to-end: a consumer writes, queries, runs an algorithm, and
    reads back the audit. Mirrors what autoresearch-ET does in one
    iteration of its research loop."""

    @pytest.mark.asyncio
    async def test_research_loop_iteration(self, research_kg):
        _seed_research_graph(research_kg.store)

        # 1. "Have we seen something like this?"
        hits = research_kg.store.find_similar_typed(
            "attention efficiency",
            "Concept",
            namespace="research",
            threshold=0.2,
            k=3,
        )
        assert hits

        # 2. "Run link prediction and keep the audit"
        await _mcp("et_run_algorithm", {
            "algorithm": "textual_similarity",
            "params": {"threshold": 0.1},
            "namespace": "research",
            "write_back": True,
        })

        # 3. "What's new in my slice in the last 24h?"
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        diff = research_kg.store.diff(
            past, future,
            node_types=["Concept"],
            edge_types=["ProposalBy"],
            namespace="research",
        )
        ids = {n["id"] for n in diff["nodes_added"]}
        assert ids == {"h-sparse", "h-flash", "h-linear"}
        # At least one ProposalBy edge created within window
        assert diff["edges_added"], "expected ProposalBy edges in the window"
        assert all(e["_type"] == "ProposalBy" for e in diff["edges_added"])
