"""ADR 013 C7: algorithm write-back (ProposalBy edges).

When an algorithm is invoked with `write_back=True`, its proposals land
as `ProposalBy` edges carrying the algorithm name, parameters, score,
and invocation timestamp. Consumers get an audit trail of
"at time T, algorithm X said Y -> Z with score S" without
re-running the algorithm.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.storage.graph_store import GraphStore
from schema.generated import models as m


@pytest.fixture
def kg():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "kg")


def _seed_concepts(kg):
    """Two concepts to connect via proposals."""
    kg.insert(m.Concept(id="c-a", name="alpha",
                        category=m.ConceptCategory.topic),
              namespace="research")
    kg.insert(m.Concept(id="c-b", name="beta",
                        category=m.ConceptCategory.topic),
              namespace="research")
    kg.insert(m.Concept(id="c-c", name="gamma",
                        category=m.ConceptCategory.topic),
              namespace="research")


# ── GraphStore.record_proposal primitive ──────────────────────────────

class TestRecordProposal:

    def test_writes_proposal_edge(self, kg):
        _seed_concepts(kg)
        eid = kg.record_proposal(
            algorithm="embedding_similarity",
            source_id="c-a", target_id="c-b",
            score=0.82,
            parameters={"threshold": 0.5},
            namespace="research",
            et_source="test",
        )
        assert eid.startswith("prop-")

        row = kg._query_one(
            "MATCH (a:Concept {id: 'c-a'})-[r:ProposalBy]->(b:Concept {id: 'c-b'}) "
            "RETURN r.algorithm, r.score, r.parameters_json, r.namespace, r.et_source"
        )
        assert row[0] == "embedding_similarity"
        assert row[1] == pytest.approx(0.82)
        params = json.loads(row[2])
        assert params == {"threshold": 0.5}
        assert row[3] == "research"
        assert row[4] == "test"

    def test_invoked_at_populated(self, kg):
        _seed_concepts(kg)
        eid = kg.record_proposal(
            algorithm="x", source_id="c-a", target_id="c-b",
        )
        row = kg._query_one(
            "MATCH ()-[r:ProposalBy {id: $id}]->() RETURN r.invoked_at",
            {"id": eid},
        )
        assert row[0]  # non-empty ISO timestamp

    def test_missing_source_raises(self, kg):
        _seed_concepts(kg)
        with pytest.raises(ValueError, match="source not found"):
            kg.record_proposal(
                algorithm="x", source_id="does-not-exist", target_id="c-b",
            )

    def test_missing_target_raises(self, kg):
        _seed_concepts(kg)
        with pytest.raises(ValueError, match="target not found"):
            kg.record_proposal(
                algorithm="x", source_id="c-a", target_id="does-not-exist",
            )

    def test_multiple_proposals_coexist(self, kg):
        """Two algorithms can each propose the same pair — neither clobbers
        the other. The audit trail captures both invocations."""
        _seed_concepts(kg)
        kg.record_proposal(
            algorithm="embedding_similarity",
            source_id="c-a", target_id="c-b", score=0.8,
        )
        kg.record_proposal(
            algorithm="textual_similarity",
            source_id="c-a", target_id="c-b", score=0.6,
        )
        rows = kg._query_all(
            "MATCH ()-[r:ProposalBy]->() RETURN r.algorithm"
        )
        algos = {r[0] for r in rows}
        assert algos == {"embedding_similarity", "textual_similarity"}


# ── MCP et_run_algorithm tool ─────────────────────────────────────────

class TestEtRunAlgorithmTool:

    def test_tool_registered(self):
        from extended_thinking.mcp_server import TOOLS
        names = {t["name"] for t in TOOLS}
        assert "et_run_algorithm" in names

    def test_tool_requires_algorithm(self):
        from extended_thinking.mcp_server import TOOLS
        tool = next(t for t in TOOLS if t["name"] == "et_run_algorithm")
        assert "algorithm" in tool["inputSchema"]["required"]

    async def test_unknown_algorithm_reports_error(self, tmp_path, monkeypatch):
        """Don't silently no-op an unknown plugin name — fail loudly so the
        caller fixes the mistake."""
        import extended_thinking.mcp_server as srv
        from extended_thinking.processing.pipeline_v2 import Pipeline
        from extended_thinking.providers import get_provider
        from extended_thinking.storage import StorageLayer

        data_dir = tmp_path / "d"
        data_dir.mkdir()
        cached = Pipeline.from_storage(get_provider(),
                                       StorageLayer.default(data_dir))
        monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)

        result = await srv.handle_tool_call("et_run_algorithm", {
            "algorithm": "nonexistent_algo",
        })
        assert result.startswith("error:")
        assert "unknown algorithm" in result

    async def test_write_back_true_persists_proposals(self, tmp_path, monkeypatch):
        """The real thing: run a link-prediction plugin, write_back=True,
        verify ProposalBy edges land in Kuzu."""
        import extended_thinking.mcp_server as srv
        from extended_thinking.processing.pipeline_v2 import Pipeline
        from extended_thinking.providers import get_provider
        from extended_thinking.storage import StorageLayer

        data_dir = tmp_path / "d"
        data_dir.mkdir()
        storage = StorageLayer.default(data_dir)
        cached = Pipeline.from_storage(get_provider(), storage)
        monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)

        # Seed a few concepts with overlapping names so textual_similarity
        # finds candidate pairs.
        for cid, name in (
            ("c-1", "sparse attention mechanism"),
            ("c-2", "sparse attention kernel"),  # near to c-1
            ("c-3", "unrelated concept"),
        ):
            cached.store.insert(m.Concept(
                id=cid, name=name, category=m.ConceptCategory.topic,
            ), namespace="research")

        result = await srv.handle_tool_call("et_run_algorithm", {
            "algorithm": "textual_similarity",
            "params": {"threshold": 0.3, "top_k": 10},
            "namespace": "research",
            "write_back": True,
        })
        data = json.loads(result)
        assert data["algorithm"] == "textual_similarity"
        assert data["write_back"] is True
        assert data["proposals_written"] >= 1

        rows = cached.store._query_all(
            "MATCH (a:Concept)-[r:ProposalBy]->(b:Concept) "
            "RETURN a.id, b.id, r.algorithm"
        )
        assert any(r[2] == "textual_similarity" for r in rows)

    async def test_write_back_false_does_not_persist(self, tmp_path, monkeypatch):
        """Read-only invocation: no ProposalBy edges should land."""
        import extended_thinking.mcp_server as srv
        from extended_thinking.processing.pipeline_v2 import Pipeline
        from extended_thinking.providers import get_provider
        from extended_thinking.storage import StorageLayer

        data_dir = tmp_path / "d"
        data_dir.mkdir()
        storage = StorageLayer.default(data_dir)
        cached = Pipeline.from_storage(get_provider(), storage)
        monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)

        for cid, name in (("c-1", "a"), ("c-2", "a plus")):
            cached.store.insert(m.Concept(
                id=cid, name=name, category=m.ConceptCategory.topic,
            ), namespace="research")

        await srv.handle_tool_call("et_run_algorithm", {
            "algorithm": "textual_similarity",
            "params": {"threshold": 0.3},
            "namespace": "research",
            # write_back omitted → default False
        })
        rows = cached.store._query_all(
            "MATCH ()-[r:ProposalBy]->() RETURN count(r)"
        )
        assert rows[0][0] == 0

    async def test_algorithm_failure_reports_without_crashing(self, tmp_path, monkeypatch):
        """If the plugin raises, surface the error rather than crashing."""
        import extended_thinking.mcp_server as srv
        from extended_thinking.algorithms.registry import register, _registry
        from extended_thinking.algorithms.protocol import AlgorithmMeta
        from extended_thinking.processing.pipeline_v2 import Pipeline
        from extended_thinking.providers import get_provider
        from extended_thinking.storage import StorageLayer

        # Register an always-raising plugin for this test
        class _Boom:
            meta = AlgorithmMeta(
                name="_boom",
                family="test",
                description="raises on purpose",
                paper_citation="n/a",
            )
            def run(self, ctx):
                raise RuntimeError("boom")
        try:
            register(_Boom)
            data_dir = tmp_path / "d"
            data_dir.mkdir()
            cached = Pipeline.from_storage(get_provider(),
                                           StorageLayer.default(data_dir))
            monkeypatch.setattr(srv, "_get_pipeline", lambda: cached)
            result = await srv.handle_tool_call("et_run_algorithm", {
                "algorithm": "_boom",
            })
            assert result.startswith("error:")
            assert "boom" in result
        finally:
            _registry.pop("_boom", None)
