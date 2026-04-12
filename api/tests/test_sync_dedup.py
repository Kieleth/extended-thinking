"""Tests for sync deduplication — don't re-process the same chunks."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from extended_thinking.providers.folder import FolderProvider
from extended_thinking.processing.concept_store import ConceptStore
from extended_thinking.processing.pipeline_v2 import Pipeline

MOCK_EXTRACTION = '[{"name": "test concept", "category": "topic", "description": "test", "source_quote": "test"}]'


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "notes.md").write_text("# Notes\nSome content about testing.\n")
        provider = FolderProvider(tmp_path)
        store = ConceptStore(tmp_path / "concepts.db")
        yield provider, store, tmp_path


class TestSyncDedup:

    @pytest.mark.asyncio
    async def test_second_sync_skips_processed_chunks(self, env):
        """Same chunks should not be re-processed on second sync."""
        provider, store, _ = env
        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(return_value=MOCK_EXTRACTION)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            pipeline = Pipeline(provider, store)

            result1 = await pipeline.sync()
            assert result1["chunks_processed"] >= 1

            result2 = await pipeline.sync()
            assert result2["chunks_processed"] == 0
            assert result2["status"] == "no_new_data"

    @pytest.mark.asyncio
    async def test_new_file_gets_processed(self, env):
        """Adding a new file should result in new chunks being processed."""
        provider, store, tmp_path = env
        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(return_value=MOCK_EXTRACTION)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            pipeline = Pipeline(provider, store)

            await pipeline.sync()

            # Add a new file
            (tmp_path / "new-notes.md").write_text("# New\nBrand new content.\n")
            # Clear provider cache
            provider._chunks_cache = None if hasattr(provider, '_chunks_cache') else None

            result = await pipeline.sync()
            assert result["chunks_processed"] >= 1

    @pytest.mark.asyncio
    async def test_dedup_survives_restart(self, env):
        """Processed chunk tracking should persist in SQLite."""
        provider, store, tmp_path = env
        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(return_value=MOCK_EXTRACTION)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            pipeline1 = Pipeline(provider, store)
            await pipeline1.sync()

            # Simulate restart — new Pipeline instance, same store
            pipeline2 = Pipeline(provider, store)
            result = await pipeline2.sync()
            assert result["chunks_processed"] == 0
