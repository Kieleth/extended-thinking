"""ADR 011 v2 Phase B — the four MVP plugins.

  B.2 frequency_threshold trigger
  B.3 embedding_cosine_gate relevance gate
  B.4 time_to_refresh cache policy
  B.1 wikipedia source (unit-tested against mocked HTTP; live integration
      lives in a separate skipped-by-default test)
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from extended_thinking.algorithms.enrichment.protocol import Candidate, GateVerdict
from extended_thinking.algorithms.protocol import AlgorithmContext
from extended_thinking.algorithms.registry import get_by_name
from extended_thinking.storage.graph_store import GraphStore


@pytest.fixture
def kg():
    with tempfile.TemporaryDirectory() as tmp:
        yield GraphStore(Path(tmp) / "kg")


# ── B.2 frequency_threshold ───────────────────────────────────────────

class TestFrequencyThresholdTrigger:

    def test_registered(self):
        plugin = get_by_name("frequency_threshold")
        assert plugin is not None
        assert plugin.meta.family == "enrichment.triggers"

    def test_fires_above_threshold(self, kg):
        # Seed concepts with different frequencies
        kg.add_concept("c-rare", "rare", "topic", "")   # freq=1
        kg.add_concept("c-common", "common", "topic", "")
        kg.add_concept("c-common", "common", "topic", "")
        kg.add_concept("c-common", "common", "topic", "")  # freq=3
        kg.add_concept("c-top", "top", "topic", "")
        kg.add_concept("c-top", "top", "topic", "")
        kg.add_concept("c-top", "top", "topic", "")
        kg.add_concept("c-top", "top", "topic", "")       # freq=4

        from extended_thinking.algorithms.enrichment.triggers.frequency_threshold import (
            FrequencyThresholdTrigger,
        )
        trigger = FrequencyThresholdTrigger(min_frequency=3)
        ctx = AlgorithmContext(kg=kg, namespace="memory")
        fired = trigger.fired_concepts(ctx)
        ids = {cid for cid, _ in fired}
        assert ids == {"c-common", "c-top"}
        # Reason strings carry the threshold comparison
        assert all("frequency=" in r and ">=" in r for _, r in fired)

    def test_max_concepts_per_run_caps_output(self, kg):
        for i in range(30):
            for _ in range(5):  # freq=5 each
                kg.add_concept(f"c-{i}", f"c{i}", "topic", "")

        from extended_thinking.algorithms.enrichment.triggers.frequency_threshold import (
            FrequencyThresholdTrigger,
        )
        trigger = FrequencyThresholdTrigger(
            min_frequency=3, max_concepts_per_run=5,
        )
        fired = trigger.fired_concepts(
            AlgorithmContext(kg=kg, namespace="memory"),
        )
        assert len(fired) == 5

    def test_respects_namespace_scope(self, kg):
        kg.add_concept("c-mem-1", "mem", "topic", "")
        kg.add_concept("c-mem-1", "mem", "topic", "")
        kg.add_concept("c-mem-1", "mem", "topic", "")
        # And a research concept — shouldn't surface in namespace=memory
        from extended_thinking._schema import models as m
        kg.insert(m.Concept(id="c-res-1", name="research",
                            category=m.ConceptCategory.topic, frequency=10),
                  namespace="research")

        from extended_thinking.algorithms.enrichment.triggers.frequency_threshold import (
            FrequencyThresholdTrigger,
        )
        trigger = FrequencyThresholdTrigger(min_frequency=3)
        ctx = AlgorithmContext(kg=kg, namespace="memory")
        ids = {cid for cid, _ in trigger.fired_concepts(ctx)}
        assert ids == {"c-mem-1"}


# ── B.4 time_to_refresh cache ─────────────────────────────────────────

class TestTimeToRefreshCache:

    def test_registered(self):
        plugin = get_by_name("time_to_refresh")
        assert plugin is not None
        assert plugin.meta.family == "enrichment.cache"

    def test_wikipedia_90d_default(self):
        from extended_thinking.algorithms.enrichment.cache.time_to_refresh import (
            TimeToRefreshCache,
        )
        cache = TimeToRefreshCache()
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=30)
        old = now - timedelta(days=100)
        ctx = AlgorithmContext(kg=None, now=now)

        # 30 days old — still fresh
        assert cache.is_stale(
            external_id="Q1", last_fetched=recent,
            source_kind="wikipedia", context=ctx,
        ) is False
        # 100 days old — stale
        assert cache.is_stale(
            external_id="Q1", last_fetched=old,
            source_kind="wikipedia", context=ctx,
        ) is True

    def test_arxiv_never_stale(self):
        """arXiv ids are immutable; the plugin's per-source default is None
        (never refresh)."""
        from extended_thinking.algorithms.enrichment.cache.time_to_refresh import (
            TimeToRefreshCache,
        )
        cache = TimeToRefreshCache()
        ctx = AlgorithmContext(
            kg=None, now=datetime.now(timezone.utc),
        )
        way_old = datetime(2010, 1, 1, tzinfo=timezone.utc)
        assert cache.is_stale(
            external_id="2301.00001",
            last_fetched=way_old,
            source_kind="arxiv", context=ctx,
        ) is False

    def test_unknown_source_uses_default(self):
        """Sources not in the override table fall back to 30d default."""
        from extended_thinking.algorithms.enrichment.cache.time_to_refresh import (
            TimeToRefreshCache,
        )
        cache = TimeToRefreshCache(refresh_after_days=30)
        now = datetime.now(timezone.utc)
        ctx = AlgorithmContext(kg=None, now=now)
        assert cache.is_stale(
            external_id="x",
            last_fetched=now - timedelta(days=45),
            source_kind="custom_canon",
            context=ctx,
        ) is True

    def test_user_override_wins_over_default(self):
        """Per-source override in config overrides the built-in default."""
        from extended_thinking.algorithms.enrichment.cache.time_to_refresh import (
            TimeToRefreshCache,
        )
        cache = TimeToRefreshCache(per_source_days={"wikipedia": 365})
        now = datetime.now(timezone.utc)
        ctx = AlgorithmContext(kg=None, now=now)
        # 300 days old — default 90d would be stale, override 365d isn't.
        assert cache.is_stale(
            external_id="Q1",
            last_fetched=now - timedelta(days=300),
            source_kind="wikipedia",
            context=ctx,
        ) is False


# ── B.3 embedding_cosine_gate ─────────────────────────────────────────

class TestEmbeddingCosineGate:

    def test_registered(self):
        plugin = get_by_name("embedding_cosine_gate")
        assert plugin is not None
        assert plugin.meta.family == "enrichment.relevance_gates"

    def test_soft_accept_without_vector_store(self):
        """No VectorStore → soft-accept (score 0.5), let next gate decide."""
        from extended_thinking.algorithms.enrichment.relevance_gates.embedding_cosine import (
            EmbeddingCosineGate,
        )
        gate = EmbeddingCosineGate()
        ctx = AlgorithmContext(kg=None, vectors=None)
        verdict = gate.judge(
            concept={"name": "x", "description": ""},
            candidate=Candidate(
                external_id="a", title="T", abstract="A",
                source_kind="stub",
            ),
            context=ctx,
        )
        assert verdict.outcome == "accept"
        assert verdict.score == 0.5

    def test_reject_below_floor(self):
        """Orthogonal vectors → cosine=0 → below any positive floor."""
        from extended_thinking.algorithms.enrichment.relevance_gates.embedding_cosine import (
            EmbeddingCosineGate,
        )
        vectors = MagicMock()
        vectors.embed.return_value = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        gate = EmbeddingCosineGate(min_similarity=0.5, auto_accept=0.9)
        ctx = AlgorithmContext(kg=None, vectors=vectors)
        verdict = gate.judge(
            concept={"name": "topic-A", "description": "description A"},
            candidate=Candidate(
                external_id="a", title="T", abstract="A",
                source_kind="stub",
            ),
            context=ctx,
        )
        assert verdict.outcome == "reject"
        assert verdict.score == pytest.approx(0.0, abs=1e-6)

    def test_auto_accept_above_ceiling(self):
        from extended_thinking.algorithms.enrichment.relevance_gates.embedding_cosine import (
            EmbeddingCosineGate,
        )
        vectors = MagicMock()
        vectors.embed.return_value = [[1.0, 0.0], [0.99, 0.141]]
        gate = EmbeddingCosineGate(min_similarity=0.5, auto_accept=0.9)
        ctx = AlgorithmContext(kg=None, vectors=vectors)
        verdict = gate.judge(
            concept={"name": "x", "description": "y"},
            candidate=Candidate(
                external_id="a", title="T", abstract="A",
                source_kind="stub",
            ),
            context=ctx,
        )
        assert verdict.outcome == "auto_accept"
        assert verdict.score > 0.9

    def test_accept_mid_range(self):
        from extended_thinking.algorithms.enrichment.relevance_gates.embedding_cosine import (
            EmbeddingCosineGate,
        )
        vectors = MagicMock()
        # cos ~ 0.8 — between the floor and the ceiling
        vectors.embed.return_value = [[1.0, 0.0], [0.8, 0.6]]
        gate = EmbeddingCosineGate(min_similarity=0.5, auto_accept=0.95)
        ctx = AlgorithmContext(kg=None, vectors=vectors)
        verdict = gate.judge(
            concept={"name": "x", "description": "y"},
            candidate=Candidate(
                external_id="a", title="T", abstract="A",
                source_kind="stub",
            ),
            context=ctx,
        )
        assert verdict.outcome == "accept"
        assert 0.5 <= verdict.score < 0.95

    def test_embed_failure_soft_accepts(self):
        """If embed() raises, we don't drop the candidate — pass to next gate."""
        from extended_thinking.algorithms.enrichment.relevance_gates.embedding_cosine import (
            EmbeddingCosineGate,
        )
        vectors = MagicMock()
        vectors.embed.side_effect = RuntimeError("network dead")
        gate = EmbeddingCosineGate()
        ctx = AlgorithmContext(kg=None, vectors=vectors)
        verdict = gate.judge(
            concept={"name": "x", "description": ""},
            candidate=Candidate(
                external_id="a", title="T", abstract="A",
                source_kind="stub",
            ),
            context=ctx,
        )
        assert verdict.outcome == "accept"
        assert verdict.score == 0.5


# ── B.1 Wikipedia source (unit tests with mocked HTTP) ────────────────

class TestWikipediaSourceUnit:

    def test_registered(self):
        plugin = get_by_name("wikipedia")
        assert plugin is not None
        assert plugin.meta.family == "enrichment.sources"

    def test_source_kind(self):
        from extended_thinking.algorithms.enrichment.sources.wikipedia import (
            WikipediaSource,
        )
        assert WikipediaSource().source_kind() == "wikipedia"

    def test_search_empty_query(self, monkeypatch):
        from extended_thinking.algorithms.enrichment.sources.wikipedia import (
            WikipediaSource,
        )
        src = WikipediaSource()
        out = src.search(
            concept_id="c-1", concept_name="", concept_description="",
            context=AlgorithmContext(kg=None),
        )
        assert out == []

    def test_search_builds_candidate_from_mocked_http(self, monkeypatch):
        from extended_thinking.algorithms.enrichment.sources import wikipedia as wp

        opensearch_response = [
            "sparse attention",
            ["Sparse attention"],
            ["Sparse attention is ..."],
            ["https://en.wikipedia.org/wiki/Sparse_attention"],
        ]
        summary_response = {
            "title": "Sparse attention",
            "extract": "Sparse attention is an efficient attention variant.",
            "description": "Neural-network attention technique",
            "content_urls": {
                "desktop": {"page": "https://en.wikipedia.org/wiki/Sparse_attention"},
            },
        }

        # Patch the HTTP helper to return canned payloads per URL shape
        def _fake_get(self, url, params=None):
            if "opensearch" in (params or {}).get("action", "") or "api.php" in url:
                return opensearch_response
            return summary_response

        monkeypatch.setattr(wp.WikipediaSource, "_http_get", _fake_get)
        # Force theme_classifier off so we don't hit an LLM in this test
        src = wp.WikipediaSource(theme_classifier="off", max_per_concept=1)
        out = src.search(
            concept_id="c-1",
            concept_name="sparse attention",
            concept_description="efficient attention",
            context=AlgorithmContext(kg=None),
        )
        assert len(out) == 1
        cand = out[0]
        assert cand.source_kind == "wikipedia"
        assert cand.external_id == "Sparse_attention"
        assert "Sparse attention" in cand.title
        assert cand.abstract.startswith("Sparse attention is")
        assert cand.url.startswith("https://en.wikipedia.org/wiki/")

    def test_opensearch_failure_returns_empty_list(self, monkeypatch):
        from extended_thinking.algorithms.enrichment.sources import wikipedia as wp

        def _raise(self, url, params=None):
            raise RuntimeError("network error")

        monkeypatch.setattr(wp.WikipediaSource, "_http_get", _raise)
        src = wp.WikipediaSource(theme_classifier="off")
        out = src.search(
            concept_id="c-1", concept_name="anything",
            concept_description="",
            context=AlgorithmContext(kg=None),
        )
        # Must NOT raise — the runner expects [] and a telemetry row.
        assert out == []

    def test_raw_categories_mode(self, monkeypatch):
        """theme_classifier=raw_categories passes through the Wikipedia
        'description' field as a single theme."""
        from extended_thinking.algorithms.enrichment.sources import wikipedia as wp

        summary = {
            "title": "X",
            "extract": "Some text",
            "description": "A category-ish string",
            "content_urls": {"desktop": {"page": "https://x/"}},
        }
        responses = {"opensearch": [
            "q", ["X"], ["desc"], ["url"],
        ], "summary": summary}

        def _fake_get(self, url, params=None):
            if params and params.get("action") == "opensearch":
                return responses["opensearch"]
            return responses["summary"]

        monkeypatch.setattr(wp.WikipediaSource, "_http_get", _fake_get)
        src = wp.WikipediaSource(theme_classifier="raw_categories")
        out = src.search(
            concept_id="c", concept_name="x", concept_description="",
            context=AlgorithmContext(kg=None),
        )
        assert out[0].themes == ["A category-ish string"]


# ── Live Wikipedia integration (opt-in) ───────────────────────────────

class TestWikipediaSourceLive:
    """Hits real Wikipedia. Skipped unless WIKIPEDIA_LIVE=1 because CI
    shouldn't depend on wikipedia.org availability."""

    @pytest.mark.skipif(
        __import__("os").environ.get("WIKIPEDIA_LIVE") != "1",
        reason="requires WIKIPEDIA_LIVE=1 (hits real Wikipedia)",
    )
    def test_real_fetch_sparse_attention(self):
        from extended_thinking.algorithms.enrichment.sources.wikipedia import (
            WikipediaSource,
        )
        src = WikipediaSource(theme_classifier="off", max_per_concept=1)
        out = src.search(
            concept_id="c-1",
            concept_name="sparse attention neural network",
            concept_description="transformer efficiency technique",
            context=AlgorithmContext(kg=None),
        )
        assert out, "expected at least one Wikipedia result"
        assert out[0].title
        assert out[0].abstract
        assert out[0].url.startswith("https://en.wikipedia.org/")
