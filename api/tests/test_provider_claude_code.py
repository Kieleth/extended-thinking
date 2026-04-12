"""Tests for ClaudeCodeProvider — reads Claude Code JSONL sessions."""

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.providers.protocol import MemoryChunk, MemoryProvider
from extended_thinking.providers.claude_code import ClaudeCodeProvider


def _write_session(projects_dir: Path, project: str, session_id: str, entries: list[dict]):
    """Helper: create a Claude Code session JSONL file."""
    project_dir = projects_dir / project
    project_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = project_dir / f"{session_id}.jsonl"
    with open(jsonl_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return jsonl_path


@pytest.fixture
def sample_sessions():
    """Temp dir simulating ~/.claude/projects/ with two sessions."""
    with tempfile.TemporaryDirectory() as tmp:
        projects_dir = Path(tmp)

        _write_session(projects_dir, "-Users-test-Projects-shelob", "sess-001", [
            {
                "type": "user",
                "message": {"role": "user", "content": "How should we handle JWT auth?"},
                "timestamp": "2026-03-20T10:00:00Z",
                "sessionId": "sess-001",
                "slug": "auth-discussion",
                "gitBranch": "main",
                "cwd": "/Users/test/Projects/shelob",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "For JWT auth, use short-lived access tokens with refresh tokens."}],
                },
                "timestamp": "2026-03-20T10:00:05Z",
                "sessionId": "sess-001",
            },
            {
                "type": "user",
                "message": {"role": "user", "content": "What about session-based auth instead?"},
                "timestamp": "2026-03-20T10:01:00Z",
                "sessionId": "sess-001",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Sessions are simpler but don't scale as well for APIs."}],
                },
                "timestamp": "2026-03-20T10:01:10Z",
                "sessionId": "sess-001",
            },
        ])

        _write_session(projects_dir, "-Users-test-Projects-ministitch", "sess-002", [
            {
                "type": "user",
                "message": {"role": "user", "content": "The pixel art generation is too slow."},
                "timestamp": "2026-03-21T14:00:00Z",
                "sessionId": "sess-002",
                "slug": "perf-optimization",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Let me profile the rendering pipeline."}],
                },
                "timestamp": "2026-03-21T14:00:10Z",
                "sessionId": "sess-002",
            },
        ])

        yield projects_dir


class TestClaudeCodeProvider:

    def test_is_memory_provider(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        assert isinstance(provider, MemoryProvider)
        assert provider.name == "claude-code"

    def test_get_recent_returns_exchanges(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        chunks = provider.get_recent()
        assert len(chunks) >= 2  # at least 2 sessions
        assert all(isinstance(c, MemoryChunk) for c in chunks)

    def test_chunks_are_exchange_pairs(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        chunks = provider.get_recent()
        # Each chunk should contain a user message
        auth_chunks = [c for c in chunks if "JWT" in c.content or "auth" in c.content.lower()]
        assert len(auth_chunks) >= 1

    def test_chunks_have_provenance(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        chunks = provider.get_recent()
        for chunk in chunks:
            assert chunk.id
            assert chunk.source  # file path
            assert chunk.timestamp
            assert "session_id" in chunk.metadata

    def test_search_finds_content(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        results = provider.search("JWT")
        assert len(results) >= 1
        assert any("JWT" in r.content for r in results)

    def test_search_case_insensitive(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        results = provider.search("pixel art")
        assert len(results) >= 1

    def test_search_no_results(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        results = provider.search("quantum entanglement")
        assert len(results) == 0

    def test_get_recent_with_limit(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        chunks = provider.get_recent(limit=1)
        assert len(chunks) <= 1

    def test_get_recent_sorted_newest_first(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        chunks = provider.get_recent()
        if len(chunks) >= 2:
            assert chunks[0].timestamp >= chunks[1].timestamp

    def test_metadata_includes_project(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        chunks = provider.get_recent()
        projects = {c.metadata.get("project") for c in chunks}
        assert "shelob" in projects or any("shelob" in (c.metadata.get("project", "") or "") for c in chunks)

    def test_store_insight(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        insight_id = provider.store_insight(
            title="Auth Decision",
            description="JWT over sessions for scalability.",
            related_concepts=["JWT", "auth"],
        )
        assert insight_id

        insights = provider.get_insights()
        assert len(insights) >= 1

    def test_get_stats(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        stats = provider.get_stats()
        assert stats["total_memories"] >= 2
        assert stats["provider"] == "claude-code"

    def test_get_entities_returns_empty(self, sample_sessions):
        provider = ClaudeCodeProvider(sample_sessions)
        assert provider.get_entities() == []

    def test_skips_progress_and_system_entries(self, sample_sessions):
        """Progress and system entries from Claude Code should be ignored."""
        _write_session(sample_sessions, "-Users-test-Projects-other", "sess-003", [
            {"type": "progress", "content": {"type": "tool_use"}},
            {"type": "system", "subtype": "init", "timestamp": "2026-03-22T10:00:00Z"},
            {
                "type": "user",
                "message": {"role": "user", "content": "Real message here."},
                "timestamp": "2026-03-22T10:00:00Z",
                "sessionId": "sess-003",
            },
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Got it."}]},
                "timestamp": "2026-03-22T10:00:05Z",
                "sessionId": "sess-003",
            },
        ])
        provider = ClaudeCodeProvider(sample_sessions)
        chunks = provider.get_recent()
        # Should have the real message but not progress/system
        contents = " ".join(c.content for c in chunks)
        assert "Real message here" in contents

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = ClaudeCodeProvider(Path(tmp))
            assert provider.get_recent() == []
            assert provider.search("anything") == []
            assert provider.get_stats()["total_memories"] == 0
