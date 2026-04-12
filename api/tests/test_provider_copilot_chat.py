"""Tests for CopilotChatProvider."""

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.providers.copilot_chat import CopilotChatProvider
from extended_thinking.providers.protocol import MemoryProvider


def _session_payload(session_id: str = "sess-001",
                     n_exchanges: int = 2,
                     creation_ms: int = 1712880000000) -> dict:
    """Build a Copilot Chat session JSON structure."""
    requests = []
    for i in range(n_exchanges):
        requests.append({
            "message": {
                "text": f"user question {i} about async code",
                "parts": [{"kind": "text", "text": f"user question {i}"}],
            },
            "response": {
                "value": [
                    {"kind": "markdown", "value": f"assistant answer {i}"},
                    {"kind": "inlineReference",
                     "inlineReference": {"name": f"file{i}.py", "uri": f"file:///{i}.py"}},
                ],
            },
            "timestamp": creation_ms + i * 10000,
        })
    return {
        "version": 1,
        "sessionId": session_id,
        "creationDate": creation_ms,
        "requests": requests,
    }


@pytest.fixture
def copilot_user_dir():
    """Synthesize a VSCode user directory with workspaceStorage + sessions."""
    with tempfile.TemporaryDirectory() as tmp:
        user = Path(tmp) / "User"
        ws_storage = user / "workspaceStorage"
        ws_storage.mkdir(parents=True)

        # Workspace 1 with two sessions
        ws1 = ws_storage / "abc123hash"
        (ws1 / "chatSessions").mkdir(parents=True)
        (ws1 / "chatSessions" / "sess-001.json").write_text(
            json.dumps(_session_payload("sess-001", n_exchanges=2)),
        )
        (ws1 / "chatSessions" / "sess-002.json").write_text(
            json.dumps(_session_payload("sess-002", n_exchanges=3,
                                         creation_ms=1712900000000)),
        )

        # Workspace 2 with one session using an older subdir name
        ws2 = ws_storage / "def456hash"
        (ws2 / "interactiveSessions").mkdir(parents=True)
        (ws2 / "interactiveSessions" / "sess-old.json").write_text(
            json.dumps(_session_payload("sess-old", n_exchanges=1)),
        )

        yield user


class TestProtocolCompliance:

    def test_is_memory_provider(self, copilot_user_dir):
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        assert isinstance(provider, MemoryProvider)

    def test_name(self, copilot_user_dir):
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        assert provider.name == "copilot-chat"


class TestDiscovery:

    def test_finds_sessions_across_workspaces(self, copilot_user_dir):
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        sessions = list(provider._iter_session_files())
        assert len(sessions) == 3

    def test_supports_multiple_session_subdirs(self, copilot_user_dir):
        """Both chatSessions/ and interactiveSessions/ should be picked up."""
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        sessions = list(provider._iter_session_files())
        names = {p.parent.name for p in sessions}
        assert "chatSessions" in names
        assert "interactiveSessions" in names

    def test_missing_user_dir_returns_empty(self):
        provider = CopilotChatProvider(user_dir=Path("/nonexistent"))
        assert provider.get_recent(limit=10) == []

    def test_empty_workspace_storage_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "User"
            (user / "workspaceStorage").mkdir(parents=True)
            provider = CopilotChatProvider(user_dir=user)
            assert provider.get_recent(limit=10) == []


class TestChunking:

    def test_chunks_are_exchange_pairs(self, copilot_user_dir):
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        chunks = provider.get_recent(limit=100)
        assert all("[user]:" in c.content for c in chunks)
        assert all("[assistant]:" in c.content for c in chunks)

    def test_chunk_count_matches_exchanges(self, copilot_user_dir):
        """2 + 3 + 1 = 6 exchanges across 3 sessions."""
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        chunks = provider.get_recent(limit=100)
        assert len(chunks) == 6

    def test_markdown_response_parts_extracted(self, copilot_user_dir):
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        chunks = provider.get_recent(limit=100)
        assert all("assistant answer" in c.content for c in chunks)

    def test_inline_references_serialized(self, copilot_user_dir):
        """inlineReference parts get [ref: ...] markers so evidence isn't lost."""
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        chunks = provider.get_recent(limit=100)
        assert any("[ref: file" in c.content for c in chunks)

    def test_chunks_have_session_metadata(self, copilot_user_dir):
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        chunks = provider.get_recent(limit=100)
        for c in chunks:
            assert "session_id" in c.metadata
            assert "workspace_hash" in c.metadata
            assert c.metadata.get("provider") == "copilot-chat"

    def test_chunk_ids_stable(self, copilot_user_dir):
        p1 = CopilotChatProvider(user_dir=copilot_user_dir)
        ids1 = {c.id for c in p1.get_recent(limit=100)}
        p2 = CopilotChatProvider(user_dir=copilot_user_dir)
        ids2 = {c.id for c in p2.get_recent(limit=100)}
        assert ids1 == ids2


class TestMalformedData:

    def test_handles_missing_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "User"
            (user / "workspaceStorage" / "hash" / "chatSessions").mkdir(parents=True)
            (user / "workspaceStorage" / "hash" / "chatSessions" / "empty.json").write_text(
                json.dumps({"version": 1, "sessionId": "empty"}),
            )
            provider = CopilotChatProvider(user_dir=user)
            assert provider.get_recent(limit=10) == []

    def test_handles_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "User"
            sessions = user / "workspaceStorage" / "hash" / "chatSessions"
            sessions.mkdir(parents=True)
            (sessions / "bad.json").write_text("not valid json {")
            provider = CopilotChatProvider(user_dir=user)
            assert provider.get_recent(limit=10) == []

    def test_handles_empty_message_text(self):
        """Exchanges with empty user or assistant text are skipped."""
        session = {
            "version": 1,
            "sessionId": "sess",
            "creationDate": 1712880000000,
            "requests": [
                {"message": {"text": ""}, "response": {"value": [{"kind": "markdown", "value": "x"}]}},
                {"message": {"text": "y"}, "response": {"value": []}},  # empty response
                {"message": {"text": "z"}, "response": {"value": [{"kind": "markdown", "value": "w"}]}},  # good
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "User"
            sessions = user / "workspaceStorage" / "h" / "chatSessions"
            sessions.mkdir(parents=True)
            (sessions / "sess.json").write_text(json.dumps(session))
            provider = CopilotChatProvider(user_dir=user)
            chunks = provider.get_recent(limit=100)
            assert len(chunks) == 1
            assert "z" in chunks[0].content


class TestStats:

    def test_stats_structure(self, copilot_user_dir):
        provider = CopilotChatProvider(user_dir=copilot_user_dir)
        stats = provider.get_stats()
        assert stats["provider"] == "copilot-chat"
        assert stats["total_memories"] == 6
        assert stats["total_sessions"] == 3
