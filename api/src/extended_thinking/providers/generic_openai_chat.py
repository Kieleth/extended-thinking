"""GenericOpenAIChatProvider — catch-all for OpenAI-format conversation JSON.

Many tools (Continue.dev, open-source chat clients, custom exports, Azure
OpenAI dumps) produce JSON files shaped like OpenAI's chat API:

    [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."},
      ...
    ]

Or wrapped:

    {"messages": [...]}
    {"conversation": {"messages": [...]}}

This provider points at a directory and reads every .json file. Each file
is one conversation. Exchange pairs (user → assistant) become MemoryChunks.

Use this when you have exports from a tool we don't have a dedicated
provider for. It's the fallback D+I ingestor.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)

INSIGHTS_DIR_NAME = "_insights"


class GenericOpenAIChatProvider:
    """Memory provider for directories of OpenAI-format JSON conversations."""

    def __init__(self, folder: Path):
        """
        Args:
            folder: directory containing JSON conversation files.
        """
        self._folder = Path(folder)
        self._insights_dir = self._folder / INSIGHTS_DIR_NAME
        self._chunks_cache: list[MemoryChunk] | None = None

    @property
    def name(self) -> str:
        return "generic-openai-chat"

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        q = query.lower()
        return [c for c in self._all_chunks() if q in c.content.lower()][:limit]

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        chunks = self._all_chunks()
        if since:
            chunks = [c for c in chunks if c.timestamp >= since]
        chunks.sort(key=lambda c: c.timestamp, reverse=True)
        return chunks[:limit]

    def get_entities(self) -> list[Entity]:
        return []

    def get_knowledge_graph(self):
        return None

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        self._insights_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        insight_id = hashlib.sha256(f"{title}{now.isoformat()}".encode()).hexdigest()[:16]
        data = {
            "id": insight_id, "title": title, "description": description,
            "related_concepts": related_concepts,
            "created_at": now.isoformat(),
        }
        (self._insights_dir / f"{insight_id}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )
        return insight_id

    def get_insights(self) -> list[MemoryChunk]:
        if not self._insights_dir.exists():
            return []
        insights = []
        for f in sorted(self._insights_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                insights.append(MemoryChunk(
                    id=data["id"],
                    content=f"{data['title']}\n\n{data['description']}",
                    source=str(f),
                    timestamp=data.get("created_at", ""),
                    metadata={"type": "insight",
                              "related_concepts": data.get("related_concepts", [])},
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to read insight %s: %s", f, e)
        return insights

    def get_stats(self) -> dict:
        files = list(self._iter_json_files())
        chunks = self._all_chunks()
        return {
            "total_memories": len(chunks),
            "total_files": len(files),
            "total_insights": len(self.get_insights()),
            "provider": self.name,
            "folder": str(self._folder),
        }

    # ── Private ──────────────────────────────────────────────────────

    def _iter_json_files(self):
        """Yield JSON files in folder, skipping the insights subdirectory."""
        if not self._folder.exists():
            return
        for path in self._folder.iterdir():
            if not path.is_file():
                continue
            if path.suffix != ".json":
                continue
            if path.parent.name == INSIGHTS_DIR_NAME:
                continue
            yield path

    def _all_chunks(self) -> list[MemoryChunk]:
        if self._chunks_cache is not None:
            return self._chunks_cache
        chunks: list[MemoryChunk] = []
        for path in self._iter_json_files():
            chunks.extend(self._parse_file(path))
        self._chunks_cache = chunks
        return chunks

    def _parse_file(self, path: Path) -> list[MemoryChunk]:
        """Parse one JSON file into exchange-pair chunks."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Skipping unreadable JSON %s: %s", path, e)
            return []

        messages = self._extract_messages(data)
        if not messages:
            return []

        conv_id = path.stem
        stat = path.stat()
        file_ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        return _exchanges_from_messages(
            messages,
            source=str(path),
            conv_id=conv_id,
            file_timestamp=file_ts,
        )

    def _extract_messages(self, data) -> list[dict]:
        """Walk common wrapper shapes and return the list of messages, or []."""
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []

        # Common wrappers
        for key in ("messages", "conversation", "chat", "history"):
            val = data.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                inner = val.get("messages")
                if isinstance(inner, list):
                    return inner
        return []


def _exchanges_from_messages(messages, source: str, conv_id: str,
                              file_timestamp: str) -> list[MemoryChunk]:
    """Pair consecutive user + assistant messages into exchange chunks."""
    chunks: list[MemoryChunk] = []
    pending_user = None
    pending_ts = file_timestamp

    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = _get_content_text(m.get("content"))
        if not content.strip():
            continue

        # OpenAI messages may carry a unix timestamp in various places
        msg_ts = _extract_timestamp(m) or file_timestamp

        if role == "user":
            pending_user = content
            pending_ts = msg_ts
        elif role == "assistant" and pending_user:
            exchange = f"[user]: {pending_user}\n\n[assistant]: {content}"
            chunk_id = hashlib.sha256(
                f"generic:{conv_id}:{i}:{exchange[:100]}".encode()
            ).hexdigest()[:16]
            chunks.append(MemoryChunk(
                id=chunk_id,
                content=exchange,
                source=source,
                timestamp=pending_ts,
                metadata={
                    "conversation_id": conv_id,
                    "exchange_index": len(chunks),
                    "provider": "generic-openai-chat",
                },
            ))
            pending_user = None
    return chunks


def _get_content_text(content) -> str:
    """OpenAI content is either a string or a list of content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for p in content:
            if isinstance(p, str):
                pieces.append(p)
            elif isinstance(p, dict):
                if p.get("type") == "text":
                    t = p.get("text")
                    if isinstance(t, str):
                        pieces.append(t)
                elif "text" in p and isinstance(p["text"], str):
                    pieces.append(p["text"])
        return "\n".join(pieces)
    return ""


def _extract_timestamp(message: dict) -> str:
    """Try common timestamp fields on a message. Returns '' if none."""
    for key in ("timestamp", "created_at", "created", "time"):
        v = message.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, (int, float)):
            # Heuristic: treat >1e12 as epoch-ms, else epoch-seconds
            try:
                seconds = float(v) / 1000.0 if v > 1e12 else float(v)
                return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                continue  # value is out-of-range; try the next candidate field
    return ""
