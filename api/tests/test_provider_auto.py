"""Tests for AutoProvider — detects best available data source."""

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.providers.protocol import MemoryProvider
from extended_thinking.providers.auto import AutoProvider


@pytest.fixture
def env_with_claude_code():
    """Simulated environment with Claude Code sessions."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        projects = d / ".claude" / "projects" / "-Users-test-Projects-myapp"
        projects.mkdir(parents=True)
        jsonl = projects / "sess-001.jsonl"
        jsonl.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-20T10:00:00Z", "sessionId": "sess-001"}) + "\n"
            + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there."}]}, "timestamp": "2026-03-20T10:00:05Z", "sessionId": "sess-001"}) + "\n"
        )
        yield d


@pytest.fixture
def env_with_notes():
    """Simulated environment with only text files."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        notes = d / "Documents"
        notes.mkdir()
        (notes / "ideas.md").write_text("# Ideas\n\nBuild a knowledge graph.\n")
        yield d


@pytest.fixture
def env_empty():
    """Simulated environment with nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class TestAutoProvider:

    def test_is_memory_provider(self, env_with_claude_code):
        provider = AutoProvider(home_dir=env_with_claude_code)
        assert isinstance(provider, MemoryProvider)
        assert provider.name == "auto"

    def test_detects_claude_code(self, env_with_claude_code):
        provider = AutoProvider(home_dir=env_with_claude_code)
        stats = provider.get_stats()
        assert stats["detected_provider"] == "claude-code"
        assert stats["total_memories"] >= 1

    def test_detects_notes_folder(self, env_with_notes):
        provider = AutoProvider(home_dir=env_with_notes)
        stats = provider.get_stats()
        assert stats["detected_provider"] == "folder"
        assert stats["total_memories"] >= 1

    def test_empty_environment(self, env_empty):
        provider = AutoProvider(home_dir=env_empty)
        stats = provider.get_stats()
        assert stats["total_memories"] == 0

    def test_search_delegates(self, env_with_claude_code):
        provider = AutoProvider(home_dir=env_with_claude_code)
        results = provider.search("Hello")
        assert len(results) >= 1

    def test_get_recent_delegates(self, env_with_claude_code):
        provider = AutoProvider(home_dir=env_with_claude_code)
        chunks = provider.get_recent()
        assert len(chunks) >= 1

    def test_aggregates_multiple_providers(self, env_with_claude_code):
        """When both Claude Code and folders exist, use both."""
        notes = env_with_claude_code / "Documents"
        notes.mkdir()
        (notes / "note.md").write_text("A note.\n")

        provider = AutoProvider(home_dir=env_with_claude_code)
        stats = provider.get_stats()
        assert "claude-code" in stats["detected_provider"]
        assert "folder" in stats["detected_provider"]
        # get_recent merges from both
        chunks = provider.get_recent(limit=50)
        sources = {c.metadata.get("filename", c.source) for c in chunks}
        assert len(sources) >= 2  # At least one from each provider
