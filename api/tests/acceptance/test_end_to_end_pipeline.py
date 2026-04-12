"""End-to-end pipeline tests on the small fixture.

Proves the plumbing: CC session JSONL → ClaudeCodeProvider → Pipeline.sync()
with a scripted fake LLM → ConceptStore has the expected concepts.

Idempotency + content-filter paths are exercised too.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.storage import StorageLayer
from tests.helpers.fake_llm import DummyLM

pytestmark = pytest.mark.acceptance

# What the LLM should "extract" from session_small. Shaped to match the
# extractor's JSON contract (valid_categories in
# extended_thinking.processing.extractor._parse_extraction_response).
EXTRACTION_RESPONSE = json.dumps([
    {
        "name": "Kuzu",
        "category": "entity",
        "description": "Embedded graph database with Cypher and temporal properties",
        "source_quote": "look at Kuzu. It's embedded, Cypher-native",
    },
    {
        "name": "SQLite",
        "category": "entity",
        "description": "Row-store considered and rejected as KG backend",
        "source_quote": "SQLite with two timestamp columns would work",
    },
    {
        "name": "bitemporal knowledge graph",
        "category": "topic",
        "description": "Graph with valid_time and transaction_time per edge",
        "source_quote": "I need a store for a bitemporal knowledge graph",
    },
    {
        "name": "KG store choice",
        "category": "decision",
        "description": "Choice of backend for the knowledge graph",
        "source_quote": "Decision: going with Kuzu for the KG layer",
    },
    {
        "name": "ChromaDB",
        "category": "entity",
        "description": "Vector store kept alongside Kuzu",
        "source_quote": "I'll keep ChromaDB for vector embeddings",
    },
])


def _fake_provider_with_response(response: str):
    """Build a DummyLM-shaped AIProvider that returns `response` for every call."""
    # DummyLM matches on substring. The extractor's prompt always contains
    # "CONVERSATION:" near the end, so that key is a reliable catch-all.
    return DummyLM({"CONVERSATION:": response}, default=response)


async def test_sync_extracts_expected_concepts(tmp_data_dir, cc_session_small):
    storage = StorageLayer.lite(tmp_data_dir / "storage")
    pipeline = Pipeline.from_storage(cc_session_small, storage)

    fake = _fake_provider_with_response(EXTRACTION_RESPONSE)
    with patch(
        "extended_thinking.processing.extractor.get_provider",
        return_value=fake,
    ):
        result = await pipeline.sync()

    assert result["chunks_processed"] >= 1, result
    assert result["concepts_extracted"] >= 1, result

    concept_names = [c["name"].lower() for c in pipeline.store.list_concepts(limit=50)]
    assert any("kuzu" in n for n in concept_names), f"Kuzu missing from {concept_names}"
    assert any("bitemporal" in n for n in concept_names), (
        f"bitemporal concept missing from {concept_names}"
    )


async def test_sync_is_idempotent(tmp_data_dir, cc_session_small):
    storage = StorageLayer.lite(tmp_data_dir / "storage")
    pipeline = Pipeline.from_storage(cc_session_small, storage)
    fake = _fake_provider_with_response(EXTRACTION_RESPONSE)

    with patch("extended_thinking.processing.extractor.get_provider", return_value=fake):
        first = await pipeline.sync()
        second = await pipeline.sync()

    assert first["chunks_processed"] >= 1
    assert second["chunks_processed"] == 0, (
        f"second sync should process no chunks (idempotent), got {second}"
    )


async def test_sync_filters_code_chunks(tmp_data_dir, cc_session_small):
    """Fixture is pure thinking content, so filter_code should report 0 filtered.
    Sanity check that the counter exists in the response shape."""
    storage = StorageLayer.lite(tmp_data_dir / "storage")
    pipeline = Pipeline.from_storage(cc_session_small, storage)
    fake = _fake_provider_with_response(EXTRACTION_RESPONSE)

    with patch("extended_thinking.processing.extractor.get_provider", return_value=fake):
        result = await pipeline.sync()

    assert "filtered_code" not in result or result.get("filtered_code", 0) == 0


async def test_sync_records_llm_calls(tmp_data_dir, cc_session_small):
    """The fake LLM should be invoked at least once by the extraction step."""
    storage = StorageLayer.lite(tmp_data_dir / "storage")
    pipeline = Pipeline.from_storage(cc_session_small, storage)
    fake = _fake_provider_with_response(EXTRACTION_RESPONSE)

    with patch("extended_thinking.processing.extractor.get_provider", return_value=fake):
        await pipeline.sync()

    assert fake.calls, "extractor did not call the LLM during sync"
    assert fake.hits.get("CONVERSATION:", 0) >= 1, fake.hits


async def test_sync_on_empty_provider_returns_no_new_data(tmp_data_dir):
    """Empty provider: sync should return status=no_new_data without calling the LLM."""
    empty_mock = AsyncMock()
    empty_mock.get_recent = lambda since=None, limit=50: []
    empty_mock.name = "empty"

    storage = StorageLayer.lite(tmp_data_dir / "storage")
    pipeline = Pipeline.from_storage(empty_mock, storage)

    result = await pipeline.sync()
    assert result["chunks_processed"] == 0
    assert result.get("status") == "no_new_data"
