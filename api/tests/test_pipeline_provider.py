"""Tests for the provider-based DIKW pipeline."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from extended_thinking.providers.claude_code import ClaudeCodeProvider
from extended_thinking.processing.concept_store import ConceptStore
from extended_thinking.processing.pipeline_v2 import Pipeline


MOCK_EXTRACTION = """[
  {"name": "cognitive architecture", "category": "topic", "description": "System for capturing thinking", "source_quote": "build a knowledge graph"},
  {"name": "simplicity preference", "category": "theme", "description": "User prefers simple solutions", "source_quote": "I want to simplify"}
]"""

MOCK_WISDOM = """{
  "type": "wisdom",
  "title": "You think in systems but build in patches",
  "why": "Your sessions show architectural thinking but incremental fixes",
  "action": "Before your next fix, write the invariant it should enforce",
  "related_concepts": ["cognitive architecture", "simplicity preference"]
}"""


@pytest.fixture
def test_env():
    """Complete test environment: provider + concept store."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Create a Claude Code session
        projects = tmp_path / "projects"
        project = projects / "-Users-test-Projects-myapp"
        project.mkdir(parents=True)
        (project / "sess-001.jsonl").write_text("\n".join([
            json.dumps({"type": "user", "message": {"role": "user", "content": "I want to build a knowledge graph for thinking."}, "timestamp": "2026-03-28T10:00:00Z", "sessionId": "sess-001", "slug": "kg-design"}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Great idea. Consider using a CRDT graph store."}]}, "timestamp": "2026-03-28T10:00:10Z"}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "I want to simplify the architecture."}, "timestamp": "2026-03-28T10:01:00Z", "sessionId": "sess-001"}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Consider a monolith first."}]}, "timestamp": "2026-03-28T10:01:10Z"}),
        ]))

        provider = ClaudeCodeProvider(projects)
        store = ConceptStore(tmp_path / "concepts.db")

        yield provider, store


class TestPipeline:

    @pytest.mark.asyncio
    async def test_sync_extracts_concepts(self, test_env):
        """Sync pulls from provider, extracts concepts, stores them."""
        provider, store = test_env

        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(return_value=MOCK_EXTRACTION)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            pipeline = Pipeline(provider, store)
            result = await pipeline.sync()

        assert result["chunks_processed"] >= 1
        assert result["concepts_extracted"] == 2
        assert store.get_stats()["total_concepts"] == 2

    @pytest.mark.asyncio
    async def test_sync_is_idempotent(self, test_env):
        """Running sync twice doesn't duplicate concepts."""
        provider, store = test_env

        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(return_value=MOCK_EXTRACTION)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            pipeline = Pipeline(provider, store)
            await pipeline.sync()
            await pipeline.sync()

        # Concepts should be merged (frequency incremented), not duplicated
        concepts = store.list_concepts()
        names = [c["name"] for c in concepts]
        assert names.count("cognitive architecture") == 1

    @pytest.mark.asyncio
    async def test_generate_wisdom(self, test_env):
        """Generate wisdom from extracted concepts."""
        provider, store = test_env

        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(side_effect=[MOCK_EXTRACTION, MOCK_WISDOM])

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            with patch("extended_thinking.processing.pipeline_v2.get_ai_provider", return_value=mock_ai):
                pipeline = Pipeline(provider, store)
                await pipeline.sync()
                wisdom = await pipeline.generate_wisdom(force=True)

        assert wisdom is not None
        assert wisdom["title"] == "You think in systems but build in patches"
        assert store.get_stats()["total_wisdoms"] == 1

    @pytest.mark.asyncio
    async def test_no_wisdom_without_concepts(self, test_env):
        """Can't generate wisdom with zero concepts."""
        provider, store = test_env

        pipeline = Pipeline(provider, store)

        with patch("extended_thinking.processing.pipeline_v2.get_ai_provider"):
            wisdom = await pipeline.generate_wisdom()

        assert wisdom is None

    @pytest.mark.asyncio
    async def test_get_insight_full_flow(self, test_env):
        """Full flow: sync + wisdom + return insight."""
        provider, store = test_env

        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(side_effect=[MOCK_EXTRACTION, MOCK_WISDOM])

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            with patch("extended_thinking.processing.pipeline_v2.get_ai_provider", return_value=mock_ai):
                pipeline = Pipeline(provider, store)
                insight = await pipeline.get_insight()

        assert insight is not None
        assert insight["type"] in ("wisdom", "nothing_new")

    def test_pipeline_stats(self, test_env):
        provider, store = test_env
        pipeline = Pipeline(provider, store)
        stats = pipeline.get_stats()

        assert "provider" in stats
        assert "concepts" in stats
        assert stats["provider"]["total_memories"] >= 1
