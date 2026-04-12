"""Tests for FolderProvider — reads .md/.txt files from a directory."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.providers.protocol import MemoryChunk, MemoryProvider
from extended_thinking.providers.folder import FolderProvider


@pytest.fixture
def sample_dir():
    """Temp directory with test files."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        (d / "auth-notes.md").write_text(
            "# Auth Design\n\n"
            "We decided to use JWT tokens for authentication.\n"
            "Session-based auth was considered but rejected for scalability.\n"
        )

        (d / "deployment.md").write_text(
            "# Deployment\n\n"
            "Using Docker with nginx reverse proxy.\n"
            "Shelob handles DNS and SSL certificates.\n"
        )

        (d / "ideas.txt").write_text(
            "Build a knowledge graph for personal thinking.\n"
            "Use Silk as the storage engine.\n"
        )

        # Non-text file — should be ignored
        (d / "image.png").write_bytes(b"\x89PNG\r\n")

        yield d


class TestFolderProvider:

    def test_is_memory_provider(self, sample_dir):
        provider = FolderProvider(sample_dir)
        assert isinstance(provider, MemoryProvider)
        assert provider.name == "folder"

    def test_search_finds_content(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.search("JWT tokens")
        assert len(results) >= 1
        assert any("JWT" in r.content for r in results)

    def test_search_case_insensitive(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.search("jwt tokens")
        assert len(results) >= 1

    def test_search_respects_limit(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.search("the", limit=1)
        assert len(results) <= 1

    def test_search_no_results(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.search("quantum computing")
        assert len(results) == 0

    def test_get_recent(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.get_recent()
        assert len(results) == 3  # 3 text files, png ignored
        # All are MemoryChunk
        assert all(isinstance(r, MemoryChunk) for r in results)

    def test_get_recent_with_limit(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.get_recent(limit=2)
        assert len(results) == 2

    def test_get_recent_with_since(self, sample_dir):
        provider = FolderProvider(sample_dir)
        # All files are just created, so "since tomorrow" returns nothing
        results = provider.get_recent(since="2099-01-01T00:00:00Z")
        assert len(results) == 0

    def test_chunks_have_provenance(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.get_recent()
        for chunk in results:
            assert chunk.id  # non-empty
            assert chunk.source  # file path
            assert chunk.timestamp  # ISO timestamp
            assert Path(chunk.source).exists()

    def test_ignores_non_text_files(self, sample_dir):
        provider = FolderProvider(sample_dir)
        results = provider.get_recent()
        sources = [r.source for r in results]
        assert not any("image.png" in s for s in sources)

    def test_store_insight(self, sample_dir):
        provider = FolderProvider(sample_dir)
        insight_id = provider.store_insight(
            title="Test Insight",
            description="You tend toward simplicity.",
            related_concepts=["simplicity", "architecture"],
        )
        assert insight_id

        # Insight should be retrievable
        insights = provider.get_insights()
        assert len(insights) >= 1
        assert any("simplicity" in i.content for i in insights)

    def test_get_stats(self, sample_dir):
        provider = FolderProvider(sample_dir)
        stats = provider.get_stats()
        assert stats["total_memories"] == 3
        assert "last_updated" in stats

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FolderProvider(Path(tmp))
            assert provider.get_recent() == []
            assert provider.search("anything") == []
            assert provider.get_stats()["total_memories"] == 0

    def test_get_entities_returns_empty(self, sample_dir):
        """FolderProvider has no entity extraction."""
        provider = FolderProvider(sample_dir)
        assert provider.get_entities() == []
