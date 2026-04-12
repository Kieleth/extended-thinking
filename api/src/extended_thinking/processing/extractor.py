"""LLM-based concept extraction from conversations.

Two entry points:
  - extract_concepts_from_chunks(chunks) — NEW: reads from MemoryProvider chunks
  - extract_concepts_from_session(store, session_id) — LEGACY: reads from Silk

The chunks-based entry point is the future. The session-based one remains
for backward compatibility during migration.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from extended_thinking.ai.registry import get_provider

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are analyzing a conversation to extract cognitive patterns. Given the conversation below, extract the key concepts the human is thinking about.

For each concept, provide:
- **name**: Short label (2-5 words)
- **category**: One of: topic, theme, question, decision, tension, entity
- **description**: One sentence explaining it in context
- **source_quote**: The human's EXACT words (verbatim quote, max 2 sentences) that demonstrate this concept. Must be from a [user] message, not the assistant.
- **supersedes**: (optional) If this concept REPLACES a previously-held view, e.g., the user changed their mind, made a newer decision that contradicts an older one, list the names of existing concepts this supersedes. Otherwise omit or use empty list.
- **source_created_at**: (optional) If the TEXT ITSELF states when the thought was written (e.g. a dated journal entry `## 2024-06-12`, a "Updated 2025-03-01" frontmatter line, a log-style `[2024-07-04]` prefix), return that date in ISO-8601 form (`YYYY-MM-DD` or full `YYYY-MM-DDTHH:MM:SS+00:00`). Do NOT infer, do NOT guess, do NOT use the date the conversation happened on. Only extract when the text explicitly anchors itself to a date. Omit otherwise.

Categories:
- **topic**: A concrete subject (a tool, pattern, technology, domain)
- **theme**: A recurring tendency or preference (e.g., "prefers simplicity")
- **question**: An open question being explored
- **decision**: A choice that was made or proposed
- **tension**: A trade-off or conflict being navigated
- **entity**: A named thing (specific tool, library, project, person)

Rules:
- Extract 3-10 concepts per conversation
- Focus on what the HUMAN is thinking about, not what the AI suggests
- Prefer specificity over generality ("JWT auth" not "programming")
- source_quote MUST be the user's actual words from the conversation, not paraphrased
- Only claim `supersedes` when the user EXPLICITLY changed a previously-stated view. Don't supersede on adjacent concepts that coexist. When in doubt, omit.

## Existing concepts (for supersession reference)
{existing_concepts}

Return ONLY valid JSON, an array of objects:
```json
[
  {{"name": "...", "category": "...", "description": "...", "source_quote": "...", "supersedes": [], "source_created_at": ""}},
  ...
]
```

CONVERSATION:
{conversation}"""


@dataclass
class ExtractedConcept:
    """A concept extracted by the LLM."""

    name: str
    category: str
    description: str
    source_quote: str = ""
    supersedes: list[str] = field(default_factory=list)
    source_created_at: str = ""  # ISO-8601 if the text self-dates; else empty


async def extract_concepts_from_chunks(
    chunks: list,
    existing_concept_names: list[str] | None = None,
    provider_name: str | None = None,
    model: str | None = None,
) -> list[ExtractedConcept]:
    """Extract concepts from MemoryChunks using an LLM.

    Args:
        chunks: List of MemoryChunk objects from a MemoryProvider.
        existing_concept_names: Known concept names for context (dedup hints).
        provider_name: AI provider name (default: settings.extraction_provider or auto).
        model: Model name (default: settings.extraction_model).

    Returns:
        List of extracted concepts with source_quotes.
    """
    if not chunks:
        return []

    from extended_thinking.config import settings
    resolved_provider = provider_name or settings.extraction_provider or None
    resolved_model = model or settings.extraction_model
    ai_provider = get_provider(resolved_provider)

    # Build conversation text from chunks (truncate to ~12000 chars)
    conversation_parts: list[str] = []
    total_chars = 0
    for chunk in chunks:
        content = chunk.content
        if len(content) > 3000:
            content = content[:3000] + "..."
        if total_chars + len(content) > 12000:
            conversation_parts.append("... [content truncated]")
            break
        conversation_parts.append(content)
        total_chars += len(content)

    conversation_text = "\n\n---\n\n".join(conversation_parts)
    if not conversation_text.strip():
        return []

    existing_str = ", ".join(existing_concept_names[:50]) if existing_concept_names else "(none yet)"

    prompt = EXTRACTION_PROMPT.format(
        conversation=conversation_text,
        existing_concepts=existing_str,
    )

    try:
        response = await ai_provider.complete(
            messages=[{"role": "user", "content": prompt}],
            model=resolved_model,
        )
    except Exception as e:
        logger.error("LLM extraction failed (provider=%s, model=%s): %s",
                     ai_provider.name, resolved_model, e)
        return []

    return _parse_extraction_response(response)


# ── Legacy entry point (reads from Silk) ──────────────────────────────

async def extract_concepts_from_session(
    store,  # GraphStore — lazy import to avoid Silk dependency
    session_id: str,
    provider_name: str | None = None,
) -> list[ExtractedConcept]:
    """Extract concepts from a session's fragments using an LLM.

    Args:
        store: The Silk graph store.
        session_id: Node ID of the session to process.
        provider_name: Optional AI provider name.

    Returns:
        List of extracted concepts.
    """
    provider = get_provider(provider_name)

    # Get session fragments
    edges = store.outgoing_edges(session_id)
    fragment_ids = [e["target_id"] for e in edges if e["edge_type"] == "CONTAINS"]

    if not fragment_ids:
        return []

    # Build conversation text from fragments
    fragments = []
    for fid in fragment_ids:
        node = store.get_node(fid)
        if node:
            fragments.append(node)

    # Sort by position
    fragments.sort(key=lambda f: f["properties"].get("position", 0))

    # Build conversation string (truncate to ~8000 chars to fit in context)
    conversation_parts = []
    total_chars = 0
    for frag in fragments:
        role = frag["properties"].get("role", "user")
        content = frag["properties"].get("content", "")
        # Truncate individual messages
        if len(content) > 2000:
            content = content[:2000] + "..."
        line = f"[{role}]: {content}"
        if total_chars + len(line) > 8000:
            conversation_parts.append("... [conversation truncated]")
            break
        conversation_parts.append(line)
        total_chars += len(line)

    conversation_text = "\n\n".join(conversation_parts)
    if not conversation_text.strip():
        return []

    # Get existing concepts for context
    existing = store.query_nodes_by_type("concept")
    existing_names = [n["properties"].get("name", "") for n in existing[:50]]
    existing_str = ", ".join(existing_names) if existing_names else "(none yet)"

    # Build prompt
    prompt = EXTRACTION_PROMPT.format(
        conversation=conversation_text,
        existing_concepts=existing_str,
    )

    # Call LLM — model from config (extraction tier: fast + cheap by default)
    from extended_thinking.config import settings
    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            model=settings.extraction_model,
        )
    except Exception as e:
        logger.error("LLM extraction failed for session %s: %s", session_id, e)
        return []

    # Parse response
    return _parse_extraction_response(response)


def _parse_extraction_response(response: str) -> list[ExtractedConcept]:
    """Parse the LLM's JSON response into ExtractedConcept objects."""
    # Find JSON array in response (may be wrapped in markdown code blocks)
    text = response.strip()
    if "```" in text:
        # Extract from code block
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("["):
                text = part
                break

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find array in the response
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Failed to parse LLM extraction response")
                return []
        else:
            return []

    if not isinstance(data, list):
        return []

    valid_categories = {"topic", "theme", "question", "decision", "tension", "entity"}
    concepts = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "").strip()
        category = item.get("category", "").strip().lower()
        description = item.get("description", "").strip()
        source_quote = item.get("source_quote", "").strip()
        supersedes_raw = item.get("supersedes", [])
        supersedes = []
        if isinstance(supersedes_raw, list):
            supersedes = [s.strip() for s in supersedes_raw if isinstance(s, str) and s.strip()]
        elif isinstance(supersedes_raw, str) and supersedes_raw.strip():
            supersedes = [supersedes_raw.strip()]
        source_created_at = _normalize_iso_date(item.get("source_created_at", ""))
        if name and category in valid_categories:
            concepts.append(ExtractedConcept(
                name=name, category=category, description=description,
                source_quote=source_quote, supersedes=supersedes,
                source_created_at=source_created_at,
            ))

    return concepts


def _normalize_iso_date(raw) -> str:
    """Coerce extractor-provided dates into ISO-8601. Drops anything unparseable.

    Accepts `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SS`, with or without timezone.
    Returns normalized ISO string or "" if invalid. We do NOT accept vague
    strings ("2024", "Q1 2024"), only anchors the caller can compare against
    chunk.timestamp.
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not s:
        return ""
    from datetime import datetime as _dt, timezone as _tz
    # Try full ISO first, then date-only
    for parser in (_dt.fromisoformat,):
        try:
            dt = parser(s)
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.isoformat()
    return ""
