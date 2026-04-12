"""Tests for the provider-based concept extractor.

TDD: these tests define the interface BEFORE the implementation exists.
Uses mock LLM responses to avoid API calls.
"""

from unittest.mock import AsyncMock, patch

import pytest

from extended_thinking.providers.protocol import MemoryChunk
from extended_thinking.processing.extractor import (
    ExtractedConcept,
    extract_concepts_from_chunks,
    _parse_extraction_response,
)


# ── Test data ────────────────────────────────────────────────────────

SAMPLE_CHUNKS = [
    MemoryChunk(
        id="chunk-001",
        content="[user]: How should we handle JWT auth for the API?\n\n[assistant]: For JWT auth, use short-lived access tokens with refresh tokens.",
        source="/test/sessions/sess-001.jsonl",
        timestamp="2026-03-20T10:00:00Z",
        metadata={"session_id": "sess-001", "project": "shelob"},
    ),
    MemoryChunk(
        id="chunk-002",
        content="[user]: The deployment is failing because nginx can't find the SSL cert.\n\n[assistant]: Check the Shelob gateway config — it manages certs via the KG.",
        source="/test/sessions/sess-001.jsonl",
        timestamp="2026-03-20T10:05:00Z",
        metadata={"session_id": "sess-001", "project": "shelob"},
    ),
    MemoryChunk(
        id="chunk-003",
        content="[user]: I want to simplify the architecture. Too many moving parts.\n\n[assistant]: Consider consolidating the services into a monolith first.",
        source="/test/sessions/sess-002.jsonl",
        timestamp="2026-03-21T14:00:00Z",
        metadata={"session_id": "sess-002", "project": "ministitch"},
    ),
]

MOCK_LLM_RESPONSE = """[
  {"name": "JWT auth", "category": "topic", "description": "Token-based API authentication", "source_quote": "How should we handle JWT auth for the API?"},
  {"name": "simplicity preference", "category": "theme", "description": "User prefers simpler architecture", "source_quote": "I want to simplify the architecture"},
  {"name": "SSL certificate management", "category": "topic", "description": "Nginx cert configuration via Shelob", "source_quote": "nginx can't find the SSL cert"}
]"""


# ── Tests ────────────────────────────────────────────────────────────

class TestExtractConceptsFromChunks:

    @pytest.mark.asyncio
    async def test_extracts_concepts_from_chunks(self):
        """Core contract: chunks in → concepts out."""
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value=MOCK_LLM_RESPONSE)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_provider):
            concepts = await extract_concepts_from_chunks(SAMPLE_CHUNKS)

        assert len(concepts) == 3
        assert all(isinstance(c, ExtractedConcept) for c in concepts)

    @pytest.mark.asyncio
    async def test_concepts_have_source_quotes(self):
        """Every concept should have a source_quote from the user's words."""
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value=MOCK_LLM_RESPONSE)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_provider):
            concepts = await extract_concepts_from_chunks(SAMPLE_CHUNKS)

        for concept in concepts:
            assert concept.source_quote, f"Concept '{concept.name}' missing source_quote"

    @pytest.mark.asyncio
    async def test_concepts_have_valid_categories(self):
        """All categories must be from the allowed set."""
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value=MOCK_LLM_RESPONSE)

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_provider):
            concepts = await extract_concepts_from_chunks(SAMPLE_CHUNKS)

        valid = {"topic", "theme", "entity", "question", "decision", "tension"}
        for concept in concepts:
            assert concept.category in valid, f"Invalid category: {concept.category}"

    @pytest.mark.asyncio
    async def test_empty_chunks_returns_empty(self):
        """No chunks → no concepts, no LLM call."""
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock()

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_provider):
            concepts = await extract_concepts_from_chunks([])

        assert concepts == []
        mock_provider.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_builds_conversation_from_chunks(self):
        """The LLM prompt should contain the chunk contents."""
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value="[]")

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_provider):
            await extract_concepts_from_chunks(SAMPLE_CHUNKS)

        # Verify the prompt sent to the LLM contains chunk content
        call_args = mock_provider.complete.call_args
        prompt = call_args[1].get("messages", call_args[0][0] if call_args[0] else [])[0]["content"]
        assert "JWT auth" in prompt
        assert "simplify the architecture" in prompt

    @pytest.mark.asyncio
    async def test_truncates_large_chunks(self):
        """Very large chunks should be truncated to fit context."""
        huge_chunk = MemoryChunk(
            id="huge",
            content="x" * 50000,
            source="test",
            timestamp="2026-03-20T10:00:00Z",
        )
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value="[]")

        with patch("extended_thinking.processing.extractor.get_provider", return_value=mock_provider):
            await extract_concepts_from_chunks([huge_chunk])

        call_args = mock_provider.complete.call_args
        # The messages kwarg contains the prompt
        messages = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        prompt = messages[0]["content"]
        assert len(prompt) < 20000  # Should be truncated


class TestParseExtractionResponse:
    """These test the parser directly — no LLM needed."""

    def test_clean_json(self):
        concepts = _parse_extraction_response(MOCK_LLM_RESPONSE)
        assert len(concepts) == 3
        assert concepts[0].name == "JWT auth"
        assert concepts[0].source_quote == "How should we handle JWT auth for the API?"

    def test_code_block_wrapped(self):
        response = f"```json\n{MOCK_LLM_RESPONSE}\n```"
        concepts = _parse_extraction_response(response)
        assert len(concepts) == 3

    def test_garbage_returns_empty(self):
        assert _parse_extraction_response("not json") == []
        assert _parse_extraction_response("") == []

    def test_invalid_category_filtered(self):
        response = '[{"name": "x", "category": "INVALID", "description": "y"}]'
        assert _parse_extraction_response(response) == []
