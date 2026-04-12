"""Tests for GenericOpenAIChatProvider."""

import json
import tempfile
from pathlib import Path

import pytest

from extended_thinking.providers.generic_openai_chat import GenericOpenAIChatProvider
from extended_thinking.providers.protocol import MemoryProvider


@pytest.fixture
def chat_folder():
    """Folder with three JSON conversations in varying shapes."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)

        # Plain list form
        (folder / "conv1.json").write_text(json.dumps([
            {"role": "user", "content": "question one"},
            {"role": "assistant", "content": "answer one"},
            {"role": "user", "content": "follow up"},
            {"role": "assistant", "content": "follow up answer"},
        ]))

        # Wrapped in "messages"
        (folder / "conv2.json").write_text(json.dumps({
            "messages": [
                {"role": "user", "content": "different form"},
                {"role": "assistant", "content": "wrapped answer"},
            ],
            "model": "gpt-4",
        }))

        # Double-wrapped
        (folder / "conv3.json").write_text(json.dumps({
            "conversation": {
                "messages": [
                    {"role": "user", "content": "triple-wrapped"},
                    {"role": "assistant", "content": "ok"},
                ],
            },
        }))

        yield folder


class TestProtocolCompliance:

    def test_is_memory_provider(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        assert isinstance(provider, MemoryProvider)

    def test_name(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        assert provider.name == "generic-openai-chat"


class TestFormatVariants:

    def test_reads_plain_list_format(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        chunks = provider.get_recent(limit=100)
        contents = [c.content for c in chunks]
        assert any("question one" in c and "answer one" in c for c in contents)
        assert any("follow up" in c for c in contents)

    def test_reads_wrapped_messages_format(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        contents = [c.content for c in provider.get_recent(limit=100)]
        assert any("different form" in c and "wrapped answer" in c for c in contents)

    def test_reads_double_wrapped(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        contents = [c.content for c in provider.get_recent(limit=100)]
        assert any("triple-wrapped" in c for c in contents)

    def test_total_exchange_count(self, chat_folder):
        """2 + 1 + 1 = 4 exchange pairs."""
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        chunks = provider.get_recent(limit=100)
        assert len(chunks) == 4


class TestContentParts:

    def test_handles_content_parts_list(self):
        """OpenAI content can be a list of {type: text, text: ...} parts."""
        data = [
            {"role": "user", "content": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ]},
            {"role": "assistant", "content": "answer"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "c.json").write_text(json.dumps(data))
            provider = GenericOpenAIChatProvider(folder=folder)
            chunks = provider.get_recent(limit=10)
            assert len(chunks) == 1
            assert "part one" in chunks[0].content
            assert "part two" in chunks[0].content


class TestTimestamps:

    def test_uses_message_timestamp_when_present(self):
        data = [
            {"role": "user", "content": "q", "timestamp": "2025-06-01T12:00:00Z"},
            {"role": "assistant", "content": "a"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "c.json").write_text(json.dumps(data))
            provider = GenericOpenAIChatProvider(folder=folder)
            chunks = provider.get_recent(limit=10)
            assert chunks[0].timestamp == "2025-06-01T12:00:00Z"

    def test_handles_epoch_seconds(self):
        data = [
            {"role": "user", "content": "q", "created_at": 1712880000},
            {"role": "assistant", "content": "a"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "c.json").write_text(json.dumps(data))
            provider = GenericOpenAIChatProvider(folder=folder)
            chunks = provider.get_recent(limit=10)
            # 1712880000 → 2024-04-11T22:40:00+00:00
            assert chunks[0].timestamp.startswith("2024-04-12")

    def test_handles_epoch_ms(self):
        data = [
            {"role": "user", "content": "q", "created_at": 1712880000000},  # ms
            {"role": "assistant", "content": "a"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "c.json").write_text(json.dumps(data))
            provider = GenericOpenAIChatProvider(folder=folder)
            chunks = provider.get_recent(limit=10)
            # Heuristic: >1e12 treated as ms → same date
            assert chunks[0].timestamp.startswith("2024-04-12")

    def test_fallback_to_file_mtime(self, chat_folder):
        """When messages have no timestamps, file mtime is used."""
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        chunks = provider.get_recent(limit=10)
        # All chunks should have non-empty timestamps
        assert all(c.timestamp for c in chunks)


class TestMetadata:

    def test_chunks_have_provider_metadata(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        chunks = provider.get_recent(limit=100)
        for c in chunks:
            assert c.metadata.get("provider") == "generic-openai-chat"
            assert "conversation_id" in c.metadata

    def test_conversation_id_matches_filename_stem(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        chunks = provider.get_recent(limit=100)
        ids = {c.metadata["conversation_id"] for c in chunks}
        assert {"conv1", "conv2", "conv3"}.issubset(ids)


class TestMalformed:

    def test_handles_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "bad.json").write_text("not valid json")
            provider = GenericOpenAIChatProvider(folder=folder)
            assert provider.get_recent(limit=10) == []

    def test_handles_unrecognized_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "weird.json").write_text(json.dumps({"unexpected": True}))
            provider = GenericOpenAIChatProvider(folder=folder)
            assert provider.get_recent(limit=10) == []

    def test_empty_folder_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GenericOpenAIChatProvider(folder=Path(tmp))
            assert provider.get_recent(limit=10) == []

    def test_ignores_insights_subfolder(self, chat_folder):
        """Its own _insights/ subfolder shouldn't be treated as chat data."""
        insights = chat_folder / "_insights"
        insights.mkdir()
        (insights / "insight.json").write_text(json.dumps(
            {"messages": [{"role": "user", "content": "should be ignored"}]},
        ))
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        chunks = provider.get_recent(limit=100)
        contents = "\n".join(c.content for c in chunks)
        assert "should be ignored" not in contents


class TestStats:

    def test_stats_structure(self, chat_folder):
        provider = GenericOpenAIChatProvider(folder=chat_folder)
        stats = provider.get_stats()
        assert stats["provider"] == "generic-openai-chat"
        assert stats["total_memories"] == 4
        assert stats["total_files"] == 3
