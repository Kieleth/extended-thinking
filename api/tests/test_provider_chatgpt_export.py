"""Tests for ChatGPTExportProvider."""

import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from extended_thinking.providers.chatgpt_export import ChatGPTExportProvider
from extended_thinking.providers.protocol import MemoryProvider


def _make_conversations_data(n_conversations: int = 2) -> list[dict]:
    """Build ChatGPT-style conversations.json content."""
    data = []
    for i in range(n_conversations):
        conv_id = f"conv-{i:03d}"
        data.append({
            "id": conv_id,
            "title": f"Conversation {i}",
            "create_time": 1712880000.0 + i * 3600,  # 2024-04-11 offset
            "update_time": 1712880000.0 + i * 3600 + 1800,
            "mapping": {
                "msg-root": {
                    "id": "msg-root",
                    "parent": None,
                    "children": ["msg-u1"],
                    "message": None,  # often empty root
                },
                "msg-u1": {
                    "id": "msg-u1",
                    "parent": "msg-root",
                    "children": ["msg-a1"],
                    "message": {
                        "id": "msg-u1",
                        "author": {"role": "user"},
                        "create_time": 1712880000.0 + i * 3600 + 100,
                        "content": {
                            "content_type": "text",
                            "parts": [f"Hello from conv {i}, I'm asking about X"],
                        },
                    },
                },
                "msg-a1": {
                    "id": "msg-a1",
                    "parent": "msg-u1",
                    "children": ["msg-u2"],
                    "message": {
                        "id": "msg-a1",
                        "author": {"role": "assistant"},
                        "create_time": 1712880000.0 + i * 3600 + 200,
                        "content": {
                            "content_type": "text",
                            "parts": [f"Answer for conv {i}: explanation of X."],
                        },
                    },
                },
                "msg-u2": {
                    "id": "msg-u2",
                    "parent": "msg-a1",
                    "children": ["msg-a2"],
                    "message": {
                        "id": "msg-u2",
                        "author": {"role": "user"},
                        "create_time": 1712880000.0 + i * 3600 + 300,
                        "content": {
                            "content_type": "text",
                            "parts": ["Follow-up question please"],
                        },
                    },
                },
                "msg-a2": {
                    "id": "msg-a2",
                    "parent": "msg-u2",
                    "children": [],
                    "message": {
                        "id": "msg-a2",
                        "author": {"role": "assistant"},
                        "create_time": 1712880000.0 + i * 3600 + 400,
                        "content": {
                            "content_type": "text",
                            "parts": ["Follow-up answer."],
                        },
                    },
                },
            },
        })
    return data


@pytest.fixture
def export_json_file():
    """Create a conversations.json file on disk."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "conversations.json"
        path.write_text(json.dumps(_make_conversations_data(3)), encoding="utf-8")
        yield path


@pytest.fixture
def export_folder():
    """Create a folder with conversations.json inside (extracted export)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "conversations.json").write_text(
            json.dumps(_make_conversations_data(2)), encoding="utf-8",
        )
        # Add other files a real export would have (should be ignored)
        (tmp_path / "user.json").write_text('{"email": "user@example.com"}')
        yield tmp_path


@pytest.fixture
def export_zip():
    """Create a zip that looks like a downloaded ChatGPT export."""
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "chatgpt-export-2026-04-12.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("conversations.json", json.dumps(_make_conversations_data(2)))
            zf.writestr("user.json", '{"email": "user@example.com"}')
        yield zip_path


class TestProtocolCompliance:

    def test_is_memory_provider(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        assert isinstance(provider, MemoryProvider)

    def test_name(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        assert provider.name == "chatgpt-export"


class TestJSONLoading:

    def test_loads_from_json_file(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=100)
        assert len(chunks) > 0

    def test_loads_from_folder(self, export_folder):
        provider = ChatGPTExportProvider(export_path=export_folder)
        chunks = provider.get_recent(limit=100)
        assert len(chunks) > 0

    def test_loads_from_zip(self, export_zip):
        provider = ChatGPTExportProvider(export_path=export_zip)
        chunks = provider.get_recent(limit=100)
        assert len(chunks) > 0

    def test_missing_path_returns_empty(self):
        provider = ChatGPTExportProvider(export_path=Path("/nonexistent/path"))
        assert provider.get_recent(limit=10) == []


class TestChunking:

    def test_chunks_are_exchange_pairs(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=100)
        assert all("[user]:" in c.content for c in chunks)
        assert all("[assistant]:" in c.content for c in chunks)

    def test_chunk_count_matches_exchange_count(self, export_json_file):
        """3 conversations x 2 exchange pairs each = 6 chunks."""
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=100)
        assert len(chunks) == 6

    def test_chunks_have_timestamps(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=100)
        assert all(c.timestamp for c in chunks)

    def test_chunks_have_conversation_metadata(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=100)
        for c in chunks:
            assert "conversation_id" in c.metadata
            assert "conversation_title" in c.metadata
            assert c.metadata.get("provider") == "chatgpt-export"

    def test_source_uses_chatgpt_scheme(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=100)
        assert all(c.source.startswith("chatgpt://") for c in chunks)

    def test_chunk_ids_stable(self, export_json_file):
        """Re-parsing the same file produces the same chunk IDs (idempotent)."""
        p1 = ChatGPTExportProvider(export_path=export_json_file)
        ids1 = {c.id for c in p1.get_recent(limit=100)}
        p2 = ChatGPTExportProvider(export_path=export_json_file)
        ids2 = {c.id for c in p2.get_recent(limit=100)}
        assert ids1 == ids2


class TestSearch:

    def test_keyword_search(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        results = provider.search("conv 1")
        assert len(results) >= 1
        assert any("conv 1" in r.content.lower() for r in results)

    def test_search_no_match_returns_empty(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        assert provider.search("this-string-does-not-exist-xyz") == []


class TestInsights:

    def test_store_and_retrieve_insight(self, tmp_path, export_json_file, monkeypatch):
        """Insights live under settings.data.root / insights / chatgpt (ADR 012)."""
        from extended_thinking.config import settings
        from extended_thinking.config.schema import DataConfig
        monkeypatch.setattr(settings, "data", DataConfig(root=tmp_path))

        provider = ChatGPTExportProvider(export_path=export_json_file)
        insight_id = provider.store_insight(
            "Test insight", "A test description", ["related1", "related2"],
        )
        assert insight_id

        retrieved = provider.get_insights()
        assert len(retrieved) == 1
        assert "Test insight" in retrieved[0].content


class TestStats:

    def test_stats_structure(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        stats = provider.get_stats()
        assert stats["provider"] == "chatgpt-export"
        assert stats["total_memories"] == 6
        assert stats["total_conversations"] == 3


class TestBranchingConversation:

    def test_linearizes_branching_dag(self):
        """If a conversation has multiple child branches, take the deepest."""
        data = [{
            "id": "conv-branchy",
            "title": "Branching",
            "create_time": 1712880000.0,
            "mapping": {
                "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
                "u1": {
                    "id": "u1", "parent": "root", "children": ["a1"],
                    "message": {
                        "author": {"role": "user"}, "create_time": 1712880001.0,
                        "content": {"content_type": "text", "parts": ["question"]},
                    },
                },
                "a1": {
                    "id": "a1", "parent": "u1", "children": ["u2a", "u2b"],
                    "message": {
                        "author": {"role": "assistant"}, "create_time": 1712880002.0,
                        "content": {"content_type": "text", "parts": ["answer"]},
                    },
                },
                # Branch A (shorter)
                "u2a": {
                    "id": "u2a", "parent": "a1", "children": [],
                    "message": {
                        "author": {"role": "user"}, "create_time": 1712880003.0,
                        "content": {"content_type": "text", "parts": ["short branch"]},
                    },
                },
                # Branch B (longer)
                "u2b": {
                    "id": "u2b", "parent": "a1", "children": ["a2b"],
                    "message": {
                        "author": {"role": "user"}, "create_time": 1712880004.0,
                        "content": {"content_type": "text", "parts": ["longer branch"]},
                    },
                },
                "a2b": {
                    "id": "a2b", "parent": "u2b", "children": [],
                    "message": {
                        "author": {"role": "assistant"}, "create_time": 1712880005.0,
                        "content": {"content_type": "text", "parts": ["long answer"]},
                    },
                },
            },
        }]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversations.json"
            path.write_text(json.dumps(data))
            provider = ChatGPTExportProvider(export_path=path)
            chunks = provider.get_recent(limit=100)

        # Should get chunks from the deeper branch (u2b/a2b), not shorter (u2a)
        all_content = "\n".join(c.content for c in chunks)
        assert "longer branch" in all_content
        assert "long answer" in all_content


class TestMalformedData:

    def test_handles_missing_message_content(self):
        data = [{
            "id": "conv-bad",
            "title": "Bad",
            "create_time": 1712880000.0,
            "mapping": {
                "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
                "u1": {
                    "id": "u1", "parent": "root", "children": [],
                    "message": None,  # missing
                },
            },
        }]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversations.json"
            path.write_text(json.dumps(data))
            provider = ChatGPTExportProvider(export_path=path)
            # Should not crash, returns empty
            assert provider.get_recent(limit=10) == []

    def test_handles_empty_conversations_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversations.json"
            path.write_text("[]")
            provider = ChatGPTExportProvider(export_path=path)
            assert provider.get_recent(limit=10) == []

    def test_handles_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversations.json"
            path.write_text("not valid json {")
            provider = ChatGPTExportProvider(export_path=path)
            assert provider.get_recent(limit=10) == []


class TestGetRecentFiltering:

    def test_since_filter(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        all_chunks = provider.get_recent(limit=100)
        assert len(all_chunks) > 0

        # Pick a timestamp from the middle of the set
        middle_ts = sorted(c.timestamp for c in all_chunks)[len(all_chunks) // 2]
        filtered = provider.get_recent(since=middle_ts, limit=100)
        assert all(c.timestamp >= middle_ts for c in filtered)

    def test_limit_respected(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=2)
        assert len(chunks) == 2

    def test_sorted_newest_first(self, export_json_file):
        provider = ChatGPTExportProvider(export_path=export_json_file)
        chunks = provider.get_recent(limit=100)
        timestamps = [c.timestamp for c in chunks]
        assert timestamps == sorted(timestamps, reverse=True)
