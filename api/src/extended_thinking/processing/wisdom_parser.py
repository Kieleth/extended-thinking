"""Opus wisdom-response parser.

Pulled out of the legacy silk-coupled `wisdom.py` so `pipeline_v2.py` can
keep using it without taking a silk dependency. The parser is
implementation-heavy because Opus output is not always strict JSON: stray
markdown fences, literal newlines inside string values, bare arrays, nested
`items` arrays, and "everything jammed into one field" all show up in real
responses.

No behavior change from the previous location; this is pure extraction.
"""

from __future__ import annotations

import json


def _parse_wisdom_response(response: str) -> dict | None:
    """Parse Opus response. Expects a single JSON object with type/title/why/action."""
    text = response.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{") or part.startswith("["):
                text = part
                break

    def _fix_newlines_in_strings(s: str) -> str:
        """Escape literal newlines inside JSON string values."""
        result: list[str] = []
        in_string = False
        escape_next = False
        for ch in s:
            if escape_next:
                result.append(ch)
                escape_next = False
                continue
            if ch == "\\":
                result.append(ch)
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string and ch == "\n":
                result.append("\\n")
                continue
            result.append(ch)
        return "".join(result)

    def _try_parse(s: str):
        """Try parsing JSON with newline fixing. Returns parsed dict/list or None."""
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            try:
                return json.loads(_fix_newlines_in_strings(s))
            except json.JSONDecodeError:
                return None

    # Parse JSON. Multiple strategies since Opus output is unpredictable.
    data = _try_parse(text)

    # Strategy 2: extract JSON block from surrounding prose.
    if data is None:
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            end = text.rfind(end_char) + 1
            if start >= 0 and end > start:
                data = _try_parse(text[start:end])
                if data:
                    break

    if data is None:
        return None

    # Bare array: take the first item.
    if isinstance(data, list):
        if not data:
            return None
        item = data[0]
        if not isinstance(item, dict) or not item.get("title"):
            return None
        return {
            "type": item.get("type", "wisdom"),
            "title": item.get("title", ""),
            "why": item.get("why", ""),
            "action": item.get("action", ""),
            "related_concepts": item.get("related_concepts", []),
        }

    if not isinstance(data, dict):
        return None

    # Old format with an `items` array: take the first item.
    if "items" in data and isinstance(data["items"], list) and data["items"]:
        item = data["items"][0]
        return {
            "type": data.get("type", "wisdom"),
            "title": item.get("title", ""),
            "why": item.get("why", ""),
            "action": item.get("action", ""),
            "related_concepts": item.get("related_concepts", []),
        }

    # Flexible key extraction. Opus doesn't always use our exact field names.
    title = data.get("title") or data.get("advice", "")
    why = data.get("why") or data.get("reasoning") or data.get("explanation") or ""
    action = (
        data.get("action")
        or data.get("recommendation")
        or data.get("next_step")
        or data.get("what_to_do")
        or ""
    )
    related = data.get("related_concepts") or data.get("concepts") or []

    # If Opus jammed everything into a single "advice" field, salvage it.
    if not title and not why:
        for v in data.values():
            if isinstance(v, str) and len(v) > 20:
                title = v[:80]
                why = v
                break

    if not title:
        return None

    # Title very long (everything in one field): split it.
    if len(title) > 100 and not why:
        why = title
        title = title[:80] + "..."

    return {
        "type": data.get("type", "wisdom"),
        "title": title,
        "why": why,
        "action": action,
        "related_concepts": related,
        "references_wisdom": data.get("references_wisdom"),
    }
