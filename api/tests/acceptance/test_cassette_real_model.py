"""Layer B: the load-bearing extraction path against a real LLM, via cassette.

Records Anthropic API calls once (`make at-record`), replays them offline
forever after (`make at-vcr`). Gives us "real model behaves as expected on
session_small" coverage without paying per-test API cost or requiring network.

The cassette filters Authorization and x-api-key headers so credentials never
end up in the committed YAML. See vcr_config fixture below.

Record mode is None on the default path: tests fail loudly if a cassette is
missing rather than silently calling out. That is deliberate.
"""

from __future__ import annotations

import json
import os

import pytest

from extended_thinking.processing.extractor import extract_concepts_from_chunks
from extended_thinking.providers.protocol import MemoryChunk

pytestmark = [pytest.mark.acceptance, pytest.mark.vcr]


@pytest.fixture(scope="module")
def vcr_config():
    """Scrub credentials from cassettes. Shared by every test in this module.

    `record_mode` intentionally omitted so pytest-recording's `--record-mode`
    CLI flag is authoritative. Default on replay is `none`.
    """
    return {
        "filter_headers": [
            ("authorization", "REDACTED"),
            ("x-api-key", "REDACTED"),
        ],
    }


@pytest.mark.asyncio
async def test_real_extraction_on_session_small(cc_session_small):
    """Extract concepts from session_small against real Anthropic (via cassette).

    Assertions are deliberately loose because real LLM output phrasing varies:
      - at least 2 concepts extracted
      - Kuzu is named somewhere (it's the central decision in the fixture)
      - every concept has a non-empty source_quote (grounding invariant)
    """
    chunks = cc_session_small.get_recent(limit=10)
    assert chunks, "fixture provider returned no chunks"

    concepts = await extract_concepts_from_chunks(
        chunks,
        existing_concept_names=[],
        provider_name="anthropic",
    )

    assert len(concepts) >= 2, (
        f"real model returned fewer than 2 concepts: {concepts}"
    )
    names = [c.name.lower() for c in concepts]
    assert any("kuzu" in n for n in names), (
        f"Kuzu should appear in real-model extraction; got {names}"
    )
    for c in concepts:
        assert c.source_quote.strip(), (
            f"concept {c.name!r} has empty source_quote (grounding invariant)"
        )


@pytest.mark.asyncio
async def test_extraction_returns_valid_categories(cc_session_small):
    """Every concept's category must be in the allowed set."""
    chunks = cc_session_small.get_recent(limit=10)
    concepts = await extract_concepts_from_chunks(
        chunks,
        existing_concept_names=[],
        provider_name="anthropic",
    )
    allowed = {"topic", "theme", "entity", "question", "decision", "tension"}
    for c in concepts:
        assert c.category in allowed, (
            f"concept {c.name!r} has category {c.category!r} not in {allowed}"
        )
