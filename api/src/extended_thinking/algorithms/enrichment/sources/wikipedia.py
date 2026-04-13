"""Wikipedia enrichment source (ADR 011 v2 MVP).

Given a user concept, search Wikipedia, fetch the top-match summary,
and emit a `Candidate` with title / abstract / url / themes. Themes
come from an optional LLM classifier (Haiku, free-form vocabulary) or
fall back to the raw Wikipedia categories.

API endpoints used (no auth, respects Wikimedia's User-Agent policy):
  GET https://en.wikipedia.org/w/api.php?action=opensearch&search=...
    → list of (title, description, url) tuples
  GET https://en.wikipedia.org/api/rest_v1/page/summary/<title>
    → {title, extract, description, content_urls, ...}

For theme tagging: config toggle `theme_classifier` picks between
  "llm"             — Haiku call (default; cost: 1 call per fetch)
  "raw_categories"  — use Wikipedia's native categories (no LLM)
  "off"             — empty themes

The plugin keeps fetches cheap: one opensearch + one summary per
concept, `max_per_concept` candidates (default 1). Rate limiting and
retries are best-effort, not asynchronous.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

from extended_thinking.algorithms.enrichment.protocol import (
    Candidate,
    EnrichmentSourcePlugin,
)
from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


_DEFAULT_USER_AGENT = (
    "extended-thinking/0.1 (https://github.com/kieleth/extended-thinking; "
    "enrichment-source) Python/httpx"
)

_OPENSEARCH_URL = "https://{lang}.wikipedia.org/w/api.php"
_SUMMARY_URL = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"


class WikipediaSource:
    """Fetch one Wikipedia article per concept, emit as Candidate."""

    meta = AlgorithmMeta(
        name="wikipedia",
        family="enrichment.sources",
        description="Wikipedia article summaries as KnowledgeNodes.",
        paper_citation="Wikipedia REST API: https://en.wikipedia.org/api/rest_v1/",
        parameters={
            "language": "en",
            "max_per_concept": 1,
            "theme_classifier": "llm",   # "llm" | "raw_categories" | "off"
            "request_timeout_s": 5.0,
            "user_agent": _DEFAULT_USER_AGENT,
        },
        temporal_aware=False,
    )

    def __init__(
        self,
        language: str = "en",
        max_per_concept: int = 1,
        theme_classifier: str = "llm",
        request_timeout_s: float = 5.0,
        user_agent: str = _DEFAULT_USER_AGENT,
    ):
        self.language = language
        self.max_per_concept = max_per_concept
        self.theme_classifier = theme_classifier
        self.request_timeout_s = request_timeout_s
        self.user_agent = user_agent

    def source_kind(self) -> str:
        return "wikipedia"

    # ── Main entry point ─────────────────────────────────────────────

    def search(
        self,
        *,
        concept_id: str,
        concept_name: str,
        concept_description: str,
        context: AlgorithmContext,
    ) -> list[Candidate]:
        if not concept_name:
            return []
        query = concept_name
        titles = self._opensearch(query)
        if not titles:
            return []

        out: list[Candidate] = []
        for title in titles[: self.max_per_concept]:
            summary = self._fetch_summary(title)
            if summary is None:
                continue
            cand_text = f"{summary.get('title', '')}. {summary.get('extract', '')}"
            themes = self._classify_themes(
                article_title=summary.get("title", ""),
                abstract=summary.get("extract", ""),
                raw_categories=summary.get("description") and [summary["description"]] or [],
                context=context,
            )
            out.append(Candidate(
                external_id=summary.get("title", title).replace(" ", "_"),
                title=summary.get("title", title),
                abstract=summary.get("extract", ""),
                url=(summary.get("content_urls", {})
                     .get("desktop", {})
                     .get("page", "")),
                themes=themes,
                source_kind="wikipedia",
                raw={"wikipedia_description": summary.get("description", "")},
            ))
        return out

    # ── HTTP ─────────────────────────────────────────────────────────

    def _http_get(self, url: str, params: dict | None = None) -> Any:
        """Single GET with the Wikimedia-required User-Agent header.

        Imports httpx lazily so the library's optional-dependency
        behaviour stays clean — tests that don't exercise Wikipedia
        don't pay the import cost.
        """
        import httpx  # lazy

        headers = {"User-Agent": self.user_agent}
        try:
            with httpx.Client(timeout=self.request_timeout_s, headers=headers) as cli:
                r = cli.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            logger.warning("wikipedia HTTP error for %s: %s", url, e)
            raise
        except Exception as e:
            logger.warning("wikipedia fetch failed for %s: %s", url, e)
            raise

    def _opensearch(self, query: str) -> list[str]:
        """Return article titles best matching the query."""
        try:
            data = self._http_get(
                _OPENSEARCH_URL.format(lang=self.language),
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": max(self.max_per_concept, 5),
                    "namespace": 0,
                    "format": "json",
                },
            )
        except Exception:
            return []
        # Response: [query, titles[], descriptions[], urls[]]
        if not isinstance(data, list) or len(data) < 2:
            return []
        titles = data[1]
        if not isinstance(titles, list):
            return []
        return [t for t in titles if isinstance(t, str)]

    def _fetch_summary(self, title: str) -> dict | None:
        try:
            return self._http_get(
                _SUMMARY_URL.format(lang=self.language, title=quote(title)),
            )
        except Exception:
            return None

    # ── Theme classifier ─────────────────────────────────────────────

    def _classify_themes(
        self,
        *,
        article_title: str,
        abstract: str,
        raw_categories: list[str],
        context: AlgorithmContext,
    ) -> list[str]:
        mode = self.theme_classifier
        if mode == "off":
            return []
        if mode == "raw_categories":
            return [c for c in raw_categories if c]
        # Default: LLM classifier. Fall back to raw_categories on failure.
        themes = self._llm_classify(article_title, abstract)
        if themes:
            return themes
        return [c for c in raw_categories if c]

    def _llm_classify(self, title: str, abstract: str) -> list[str]:
        """Ask Haiku for 1-3 theme tags. Free-form vocabulary (ADR 011 v2).

        Fails silently (returns []) if no API key or the call errors —
        callers fall back to raw categories.
        """
        if not abstract:
            return []
        try:
            import anyio
            from extended_thinking.ai.registry import get_provider
            from extended_thinking.config import settings
        except ImportError:
            return []

        if not (settings.anthropic_api_key or settings.openai_api_key):
            return []

        prompt = (
            "Classify the following Wikipedia article into 1-3 short "
            "lowercase theme tags. Use dot-notation for hierarchy "
            "(e.g. 'cs.ai', 'biology.cell', 'math.stats'). Return ONLY "
            "the tags, comma-separated, nothing else.\n\n"
            f"Title: {title}\n"
            f"Abstract: {abstract[:1200]}"
        )
        try:
            provider = get_provider(settings.extraction_provider or None)

            async def _call() -> str:
                return await provider.complete(
                    messages=[{"role": "user", "content": prompt}],
                    model=settings.extraction_model,
                )

            raw = anyio.run(_call)
        except Exception as e:
            logger.debug("llm theme classification failed: %s", e)
            return []

        tags: list[str] = []
        for chunk in raw.strip().splitlines()[:3]:
            for tag in chunk.split(","):
                t = tag.strip().strip(".").lower()
                if t and len(t) < 40:
                    tags.append(t)
        # Dedup while preserving order
        seen: set[str] = set()
        out: list[str] = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                out.append(t)
            if len(out) >= 3:
                break
        return out


register(WikipediaSource)
