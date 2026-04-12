"""Deterministic vector-izer for the fast-path acceptance suite.

N-gram polynomial hash, seeded, L2-normalized. Gives synthetic text some
semantic-ish behavior (similar strings land near each other in cosine space)
without ever calling a real embedding model.

Pattern attributed to DSPy's DummyVectorizer:
    https://github.com/stanfordnlp/dspy/blob/main/dspy/utils/dummies.py

Ported here rather than imported to avoid taking DSPy as a test-time
dependency, and to keep the implementation under our own eyes.
"""

from __future__ import annotations

import hashlib
import math

DEFAULT_DIM = 128
DEFAULT_N = 3


class DummyVectorizer:
    """Hash-based deterministic vectorizer.

    Two texts that share many character n-grams end up with higher cosine
    similarity than two that share few. Good enough for link-prediction,
    nearest-neighbor, and clustering tests where we want some signal without
    spinning up a real model.

    Parameters:
        dim: output dimensionality. 128 is plenty for tests.
        n: n-gram size. 3 gives reasonable overlap for short synthetic text.
        seed: deterministic salt mixed into each hash.
    """

    def __init__(self, dim: int = DEFAULT_DIM, n: int = DEFAULT_N, seed: int = 42):
        if dim <= 0:
            raise ValueError("dim must be positive")
        if n <= 0:
            raise ValueError("n must be positive")
        self.dim = dim
        self.n = n
        self.seed = seed
        self._salt = str(seed).encode("utf-8")

    def embed(self, text: str) -> list[float]:
        """Return a deterministic dim-D L2-normalized vector for `text`."""
        vec = [0.0] * self.dim
        normalized = text.lower()
        if len(normalized) < self.n:
            # Very short string: hash it whole.
            self._accumulate(vec, normalized)
        else:
            for i in range(len(normalized) - self.n + 1):
                self._accumulate(vec, normalized[i : i + self.n])
        return _l2_normalize(vec)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def _accumulate(self, vec: list[float], token: str) -> None:
        digest = hashlib.sha256(self._salt + token.encode("utf-8")).digest()
        # Use the digest bytes to drive two values: an index and a signed increment.
        idx = int.from_bytes(digest[:4], "little") % self.dim
        # Increment is signed, centered on 0, range roughly [-1, +1).
        raw = int.from_bytes(digest[4:8], "little")
        sign = 1.0 if (raw & 1) == 0 else -1.0
        magnitude = ((raw >> 1) / (2**31 - 1))  # 0..1
        vec[idx] += sign * magnitude


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors. For test assertions."""
    if len(a) != len(b):
        raise ValueError("vectors must be the same length")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]
