"""ClaudeCodeProvider — reads Claude Code session transcripts from ~/.claude/projects/.

Parses JSONL files containing user/assistant exchanges. Each session becomes
a set of MemoryChunks (one per exchange pair: user question + assistant response).

This is the built-in provider for users who use Claude Code without any
external memory system.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _default_insights_dir() -> Path:
    """Resolve insights dir at call time against current settings.data.root."""
    from extended_thinking.config import settings
    return settings.data.root / "insights" / "claude-code"


class ClaudeCodeProvider:
    """Memory provider for Claude Code session transcripts.

    Reads JSONL files from ~/.claude/projects/. Each exchange pair
    (user message + assistant response) becomes one MemoryChunk.
    """

    def __init__(self, projects_dir: Path | None = None,
                 insights_dir: Path | None = None):
        self._projects_dir = projects_dir or DEFAULT_PROJECTS_DIR
        self._insights_dir = insights_dir or _default_insights_dir()
        self._chunks_cache: list[MemoryChunk] | None = None

    @property
    def name(self) -> str:
        return "claude-code"

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        query_lower = query.lower()
        return [
            c for c in self._all_chunks()
            if query_lower in c.content.lower()
        ][:limit]

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        chunks = self._all_chunks()
        if since:
            chunks = [c for c in chunks if c.timestamp >= since]
        chunks.sort(key=lambda c: c.timestamp, reverse=True)
        return chunks[:limit]

    def get_entities(self) -> list[Entity]:
        return []

    def get_knowledge_graph(self):
        """Claude Code sessions have no structured KG."""
        return None

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        self._insights_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        insight_id = hashlib.sha256(f"{title}{now.isoformat()}".encode()).hexdigest()[:16]

        data = {
            "id": insight_id,
            "title": title,
            "description": description,
            "related_concepts": related_concepts,
            "created_at": now.isoformat(),
        }
        filepath = self._insights_dir / f"{insight_id}.json"
        filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
                    metadata={"type": "insight", "related_concepts": data.get("related_concepts", [])},
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to read insight %s: %s", f, e)
        return insights

    def get_stats(self) -> dict:
        chunks = self._all_chunks()
        sessions = {c.metadata.get("session_id") for c in chunks}
        last_ts = max((c.timestamp for c in chunks), default=None)
        return {
            "total_memories": len(chunks),
            "total_sessions": len(sessions),
            "total_insights": len(self.get_insights()),
            "last_updated": last_ts,
            "provider": self.name,
            "projects_dir": str(self._projects_dir),
        }

    # ── Private ──────────────────────────────────────────────────────

    def _all_chunks(self) -> list[MemoryChunk]:
        """Parse all JSONL files into MemoryChunks. Cached after first call."""
        if self._chunks_cache is not None:
            return self._chunks_cache

        chunks: list[MemoryChunk] = []
        if not self._projects_dir.exists():
            self._chunks_cache = chunks
            return chunks

        for project_dir in self._projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            project_name = _decode_project_name(project_dir.name)
            for jsonl_file in project_dir.glob("*.jsonl"):
                session_chunks = _parse_session(jsonl_file, project_name)
                chunks.extend(session_chunks)

        self._chunks_cache = chunks
        return chunks


def _decode_project_name(dirname: str) -> str:
    """Extract the last path component from a Claude Code project dir name.
    -Users-luis-Projects-shelob → shelob
    """
    parts = dirname.split("-")
    return parts[-1] if parts else dirname


def _extract_text(content) -> str:
    """Extract readable text from a message content field.
    Handles both string content (user) and array content (assistant).
    """
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return str(content)

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            name = block.get("name", "unknown")
            desc = block.get("input", {}).get("description", "")
            if desc:
                parts.append(f"[Tool: {name}] {desc}")
    return "\n".join(parts)


def _parse_session(jsonl_path: Path, project_name: str) -> list[MemoryChunk]:
    """Parse a Claude Code JSONL file into exchange-pair MemoryChunks.

    Groups consecutive user+assistant messages into exchange pairs.
    Each pair becomes one MemoryChunk.
    """
    session_id = jsonl_path.stem
    exchanges: list[MemoryChunk] = []
    pending_user: dict | None = None

    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type")

                if entry_type == "user":
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        pending_user = {
                            "content": content,
                            "timestamp": entry.get("timestamp", ""),
                            "slug": entry.get("slug", ""),
                        }

                elif entry_type == "assistant" and pending_user:
                    msg = entry.get("message", {})
                    assistant_text = _extract_text(msg.get("content", []))

                    if assistant_text.strip():
                        exchange_text = (
                            f"[user]: {pending_user['content']}\n\n"
                            f"[assistant]: {assistant_text}"
                        )
                        exchange_ts = pending_user["timestamp"] or entry.get("timestamp", "")
                        chunk_id = hashlib.sha256(
                            f"{session_id}:{len(exchanges)}:{exchange_text[:100]}".encode()
                        ).hexdigest()[:16]

                        exchanges.append(MemoryChunk(
                            id=chunk_id,
                            content=exchange_text,
                            source=str(jsonl_path),
                            timestamp=exchange_ts,
                            metadata={
                                "session_id": session_id,
                                "project": project_name,
                                "slug": pending_user.get("slug", ""),
                                "exchange_index": len(exchanges),
                                "provider": "claude-code",
                            },
                        ))
                    pending_user = None

    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Failed to read %s: %s", jsonl_path, e)

    return exchanges
