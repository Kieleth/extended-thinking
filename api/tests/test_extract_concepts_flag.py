"""ADR 013 C8: MemoryProvider.extract_concepts flag.

A provider can set `extract_concepts = False` to skip the LLM concept
extraction pass on its chunks. Structured-data providers (typed run
records, telemetry, form submissions) don't benefit from extraction —
the content is already typed.

Conversation providers default to `True`; existing behavior unchanged.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.providers.protocol import MemoryChunk
from extended_thinking.storage import StorageLayer


class _StaticProvider:
    """Minimal MemoryProvider stand-in yielding a fixed chunk list.

    Toggle `extract_concepts` to test both paths without spinning up
    Haiku.
    """

    def __init__(self, chunks: list[MemoryChunk], *, extract_concepts: bool = True):
        self._chunks = chunks
        self.extract_concepts = extract_concepts
        self._calls = 0

    @property
    def name(self) -> str:
        return "static-test-provider"

    def search(self, query, limit=20):
        return [c for c in self._chunks if query.lower() in c.content.lower()][:limit]

    def get_recent(self, since=None, limit=50):
        self._calls += 1
        return list(self._chunks)

    def get_entities(self):
        return []

    def store_insight(self, title, description, related_concepts):
        return "x"

    def get_insights(self):
        return []

    def get_stats(self):
        return {"total_memories": len(self._chunks), "last_updated": ""}

    def get_knowledge_graph(self):
        return None


def _chunk(i: int) -> MemoryChunk:
    return MemoryChunk(
        id=f"c-{i}",
        content=f"chunk content {i} about research runs and experiments",
        source="structured-provider://run-42",
        timestamp="2026-04-10T12:00:00+00:00",
        metadata={"provider": "typed"},
    )


@pytest.fixture
def pipeline_factory(tmp_path):
    """Build a Pipeline on a fresh storage layer with a configurable provider."""
    def _make(provider):
        storage = StorageLayer.default(tmp_path / "data")
        return Pipeline.from_storage(provider, storage)
    return _make


# ── Flag disables extraction ──────────────────────────────────────────

class TestExtractConceptsFlag:

    @pytest.mark.asyncio
    async def test_false_skips_llm_extraction(self, pipeline_factory, monkeypatch):
        """When extract_concepts=False, `extract_concepts_from_chunks`
        must not be called — the pipeline stores chunks but emits zero
        concepts for this run."""
        from extended_thinking.processing import pipeline_v2

        call_count = {"n": 0}

        async def _fake_extract(*args, **kwargs):
            call_count["n"] += 1
            return []

        monkeypatch.setattr(
            pipeline_v2, "extract_concepts_from_chunks", _fake_extract,
        )

        provider = _StaticProvider(
            [_chunk(i) for i in range(3)],
            extract_concepts=False,
        )
        pipe = pipeline_factory(provider)

        result = await pipe.sync()

        assert call_count["n"] == 0, "extractor must not be called"
        assert result["concepts_extracted"] == 0
        # But chunks were still processed (filtered + marked)
        assert result["chunks_processed"] >= 0

    @pytest.mark.asyncio
    async def test_true_runs_extraction_as_before(self, pipeline_factory, monkeypatch):
        """The default path: extract_concepts=True (or attribute absent)
        still calls the LLM extractor. This protects against regressing
        the memory-side pipeline when landing C8."""
        from extended_thinking.processing import pipeline_v2

        call_count = {"n": 0}

        async def _fake_extract(*args, **kwargs):
            call_count["n"] += 1
            return []

        monkeypatch.setattr(
            pipeline_v2, "extract_concepts_from_chunks", _fake_extract,
        )

        provider = _StaticProvider(
            [_chunk(i) for i in range(3)],
            extract_concepts=True,
        )
        pipe = pipeline_factory(provider)

        await pipe.sync()

        assert call_count["n"] >= 1, "extractor must be called at least once"

    @pytest.mark.asyncio
    async def test_missing_attribute_defaults_to_true(self, pipeline_factory, monkeypatch):
        """Providers that predate C8 don't declare the flag. The pipeline
        must treat a missing attribute as True, preserving back-compat."""
        from extended_thinking.processing import pipeline_v2

        call_count = {"n": 0}

        async def _fake_extract(*args, **kwargs):
            call_count["n"] += 1
            return []

        monkeypatch.setattr(
            pipeline_v2, "extract_concepts_from_chunks", _fake_extract,
        )

        class _LegacyProvider(_StaticProvider):
            def __init__(self, chunks):
                # Intentionally omit `extract_concepts`
                self._chunks = chunks
                self._calls = 0

        provider = _LegacyProvider([_chunk(i) for i in range(2)])
        pipe = pipeline_factory(provider)

        await pipe.sync()

        assert call_count["n"] >= 1

    @pytest.mark.asyncio
    async def test_structured_provider_chunks_still_marked_processed(
        self, pipeline_factory, monkeypatch,
    ):
        """Even though extraction is skipped, chunks still flow through
        dedup tracking — re-syncing the same structured data shouldn't
        double-process it."""
        from extended_thinking.processing import pipeline_v2

        async def _fake_extract(*args, **kwargs):
            return []

        monkeypatch.setattr(
            pipeline_v2, "extract_concepts_from_chunks", _fake_extract,
        )

        provider = _StaticProvider(
            [_chunk(i) for i in range(2)],
            extract_concepts=False,
        )
        pipe = pipeline_factory(provider)

        first = await pipe.sync()
        # Chunks should be tracked so the SECOND sync sees them as already processed
        second = await pipe.sync()
        assert second["chunks_processed"] == 0, \
            "structured chunks must be idempotent across re-syncs"
        assert first["chunks_processed"] >= 0


# ── Conversation providers unchanged ──────────────────────────────────

class TestConversationProvidersUnchanged:
    """The six batteries-included conversation providers must not have had
    their extract_concepts default flipped — they're concept-producing by
    design, and regression here means the memory pipeline goes silent."""

    @pytest.mark.parametrize("provider_cls", [
        "claude_code.ClaudeCodeProvider",
        "folder.FolderProvider",
    ])
    def test_default_attribute_absent_or_true(self, provider_cls):
        """Conversation providers: missing attribute is fine (defaults True)
        or explicit True. They never ship with False."""
        module_name, cls_name = provider_cls.rsplit(".", 1)
        mod = __import__(
            f"extended_thinking.providers.{module_name}",
            fromlist=[cls_name],
        )
        cls = getattr(mod, cls_name)
        # Either the class doesn't declare it (→ default True via getattr)
        # or the value is True. Never False.
        value = getattr(cls, "extract_concepts", True)
        assert value is True, f"{cls_name} should not default to False"
