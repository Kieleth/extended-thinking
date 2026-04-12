"""Entity resolution: detect and merge concept duplicates.

When the extractor produces a new concept, resolution plugins check
whether it's a variant of an existing concept. If yes, merge instead
of creating a duplicate.

Plugins:
  - sequence_matcher: character-level similarity (fast, zero deps)
  - embedding_cosine: semantic similarity via VectorStore (catches synonyms)

Both return Optional[dict] — the matched concept, or None if no match
above threshold. Pipeline tries plugins in config order; first match wins.

Contract:
    def resolve(context: AlgorithmContext, query_concept: dict) -> dict | None

The algorithm `run(context)` method is a no-op for this family; callers
use `resolve()` directly because entity resolution is called per-concept
during extraction, not batch.
"""

from extended_thinking.algorithms.resolution.sequence_matcher import (
    SequenceMatcherResolution,
)
from extended_thinking.algorithms.resolution.embedding_cosine import (
    EmbeddingCosineResolution,
)

__all__ = ["SequenceMatcherResolution", "EmbeddingCosineResolution"]
