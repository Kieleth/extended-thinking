"""Integration test: provider → extractor → concepts.

End-to-end flow using a synthetic Claude Code session. Uses mock LLM
to avoid API calls but verifies the full data path from JSONL file
through provider chunking to concept extraction.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from extended_thinking.providers import get_provider
from extended_thinking.providers.claude_code import ClaudeCodeProvider
from extended_thinking.providers.auto import AutoProvider
from extended_thinking.processing.extractor import extract_concepts_from_chunks


# ── Synthetic test session ───────────────────────────────────────────

SYNTHETIC_SESSION = [
    {
        "type": "user",
        "message": {"role": "user", "content": "I want to build a knowledge graph that captures how I think when using LLMs. Something like an externalized cognitive architecture."},
        "timestamp": "2026-03-28T10:00:00Z",
        "sessionId": "integration-test",
        "slug": "cognitive-architecture",
        "cwd": "/Users/test/Projects/extended-thinking",
    },
    {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "That's an interesting idea. You could use a CRDT-based graph store like Silk for the knowledge graph, with LLM extraction for concept detection."}]},
        "timestamp": "2026-03-28T10:00:10Z",
        "sessionId": "integration-test",
    },
    {
        "type": "user",
        "message": {"role": "user", "content": "The key insight is that memory systems store and retrieve, but nobody synthesizes wisdom from the patterns. I want Opus to look at everything I've been thinking about and tell me what I'm missing."},
        "timestamp": "2026-03-28T10:01:00Z",
        "sessionId": "integration-test",
    },
    {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "So a DIKW pipeline: Haiku for extraction, algorithmic pattern detection, then Opus for wisdom synthesis. The value isn't in the memory — it's in the thinking layer on top."}]},
        "timestamp": "2026-03-28T10:01:15Z",
        "sessionId": "integration-test",
    },
    {
        "type": "user",
        "message": {"role": "user", "content": "Exactly. And it should work with any memory system — MemPalace, Obsidian, plain files. A pluggable architecture."},
        "timestamp": "2026-03-28T10:02:00Z",
        "sessionId": "integration-test",
    },
    {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "A MemoryProvider protocol — search, get_recent, store_insight. Any backend implements the interface."}]},
        "timestamp": "2026-03-28T10:02:10Z",
        "sessionId": "integration-test",
    },
]

MOCK_EXTRACTION = """[
  {"name": "externalized cognitive architecture", "category": "topic", "description": "Building a system that captures thinking patterns", "source_quote": "I want to build a knowledge graph that captures how I think when using LLMs"},
  {"name": "wisdom synthesis gap", "category": "tension", "description": "Memory systems store but don't synthesize", "source_quote": "memory systems store and retrieve, but nobody synthesizes wisdom from the patterns"},
  {"name": "pluggable memory architecture", "category": "decision", "description": "Support any memory backend via protocol", "source_quote": "it should work with any memory system"},
  {"name": "DIKW pipeline", "category": "topic", "description": "Data→Information→Knowledge→Wisdom processing", "source_quote": "Haiku for extraction, algorithmic pattern detection, then Opus for wisdom synthesis"}
]"""


@pytest.fixture
def synthetic_project():
    """Create a temp dir with one synthetic Claude Code session."""
    with tempfile.TemporaryDirectory() as tmp:
        projects_dir = Path(tmp)
        project = projects_dir / "-Users-test-Projects-extended-thinking"
        project.mkdir(parents=True)
        jsonl = project / "integration-test.jsonl"
        with open(jsonl, "w") as f:
            for entry in SYNTHETIC_SESSION:
                f.write(json.dumps(entry) + "\n")
        yield projects_dir


# ── Integration tests ────────────────────────────────────────────────

class TestProviderToExtractor:

    def test_provider_reads_synthetic_session(self, synthetic_project):
        """ClaudeCodeProvider reads our synthetic JSONL and produces chunks."""
        provider = ClaudeCodeProvider(synthetic_project)
        chunks = provider.get_recent()

        assert len(chunks) == 3  # 3 exchange pairs
        assert all(c.metadata.get("session_id") == "integration-test" for c in chunks)
        assert any("knowledge graph" in c.content for c in chunks)
        assert any("pluggable" in c.content for c in chunks)

    def test_provider_search_works(self, synthetic_project):
        """Search finds content in the synthetic session."""
        provider = ClaudeCodeProvider(synthetic_project)

        results = provider.search("cognitive architecture")
        assert len(results) >= 1
        assert any("cognitive" in r.content.lower() for r in results)

    @pytest.mark.asyncio
    async def test_full_pipeline_provider_to_concepts(self, synthetic_project):
        """Full chain: JSONL → provider → chunks → extractor → concepts."""
        provider = ClaudeCodeProvider(synthetic_project)
        chunks = provider.get_recent()
        assert len(chunks) > 0

        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(return_value=MOCK_EXTRACTION)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            concepts = await extract_concepts_from_chunks(chunks)

        # Verify concepts were extracted
        assert len(concepts) == 4

        # Verify specific concepts
        names = {c.name for c in concepts}
        assert "externalized cognitive architecture" in names
        assert "DIKW pipeline" in names
        assert "pluggable memory architecture" in names

        # Verify source quotes are present
        for concept in concepts:
            assert concept.source_quote, f"Missing source_quote for '{concept.name}'"

        # Verify categories are valid
        valid_cats = {"topic", "theme", "entity", "question", "decision", "tension"}
        for concept in concepts:
            assert concept.category in valid_cats

    @pytest.mark.asyncio
    async def test_extractor_receives_chunk_content(self, synthetic_project):
        """Verify the LLM receives the actual conversation content."""
        provider = ClaudeCodeProvider(synthetic_project)
        chunks = provider.get_recent()

        mock_ai = AsyncMock()
        mock_ai.complete = AsyncMock(return_value="[]")

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_ai):
            await extract_concepts_from_chunks(chunks)

        # The prompt sent to LLM should contain user's words
        call_args = mock_ai.complete.call_args
        messages = call_args.kwargs.get("messages", [])
        prompt = messages[0]["content"]
        assert "knowledge graph" in prompt
        assert "cognitive architecture" in prompt
        assert "pluggable" in prompt

    def test_auto_provider_detects_synthetic(self, synthetic_project):
        """AutoProvider should detect our synthetic Claude Code sessions."""
        # Simulate home dir structure
        home = synthetic_project.parent
        claude_dir = home / ".claude" / "projects"
        claude_dir.mkdir(parents=True, exist_ok=True)
        # Symlink or copy the project dir
        import shutil
        dest = claude_dir / "-Users-test-Projects-extended-thinking"
        if not dest.exists():
            shutil.copytree(
                synthetic_project / "-Users-test-Projects-extended-thinking",
                dest,
            )

        auto = AutoProvider(home_dir=home)
        stats = auto.get_stats()
        assert stats["detected_provider"] == "claude-code"
        assert stats["total_memories"] >= 3

    def test_provider_stats_are_accurate(self, synthetic_project):
        """Stats should reflect the actual content."""
        provider = ClaudeCodeProvider(synthetic_project)
        stats = provider.get_stats()

        assert stats["total_memories"] == 3  # 3 exchange pairs
        assert stats["total_sessions"] == 1
        assert stats["provider"] == "claude-code"
