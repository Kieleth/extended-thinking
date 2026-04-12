"""CursorProvider — ingests Cursor editor's local chat history.

Cursor stores chat history in a SQLite database (as a VSCode fork). The
relevant file is typically:

Mac:     ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
Linux:   ~/.config/Cursor/User/globalStorage/state.vscdb
Windows: %APPDATA%/Cursor/User/globalStorage/state.vscdb

The DB has a `cursorDiskKV` (or `ItemTable`) table with JSON blobs keyed
by conversation ID. Cursor versions have shipped multiple schemas; we
probe for the shapes we know and extract what we can. Users with a
schema we don't recognize see an empty result with a warning logged.

This provider is best-effort by design. Cursor's internal storage is not
documented, and format changes are expected. If a user reports chats
missing, we add support for the new shape.

For users who want guaranteed extraction, an `export_path` option accepts
a folder of manually-exported JSON or markdown conversations — stable
input format we fully control.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)

def _insights_dir() -> Path:
    from extended_thinking.config import settings
    return settings.data.root / "insights" / "cursor"


def _default_cursor_db_path() -> Path:
    """Platform-specific Cursor state.vscdb location."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Cursor"
    elif sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming" / "Cursor"
    else:
        base = Path.home() / ".config" / "Cursor"
    return base / "User" / "globalStorage" / "state.vscdb"


# Known Cursor chat-data keys across versions. We search for rows where `key`
# matches any of these prefixes.
CHAT_KEY_PREFIXES = [
    "workbench.panel.aichat.view.aichat.chatdata",
    "aiService.prompts",
    "composerData:",
    "cursorDiskKV/composerData:",
]


class CursorProvider:
    """Memory provider for Cursor editor chat history."""

    def __init__(self, db_path: Path | None = None,
                 export_path: Path | None = None):
        """
        Args:
            db_path: path to Cursor's state.vscdb. Defaults to platform location.
            export_path: alternative — a folder of manually-exported conversations.
        """
        self._db_path = Path(db_path) if db_path else _default_cursor_db_path()
        self._export_path = Path(export_path) if export_path else None
        self._chunks_cache: list[MemoryChunk] | None = None

    @property
    def name(self) -> str:
        return "cursor"

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
        d = _insights_dir()
        d.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        insight_id = hashlib.sha256(f"{title}{now.isoformat()}".encode()).hexdigest()[:16]
        data = {
            "id": insight_id, "title": title, "description": description,
            "related_concepts": related_concepts,
            "created_at": now.isoformat(),
        }
        (d / f"{insight_id}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )
        return insight_id

    def get_insights(self) -> list[MemoryChunk]:
        d = _insights_dir()
        if not d.exists():
            return []
        insights = []
        for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
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
                logger.warning("Failed to read Cursor insight %s: %s", f, e)
        return insights

    def get_stats(self) -> dict:
        chunks = self._all_chunks()
        return {
            "total_memories": len(chunks),
            "total_insights": len(self.get_insights()),
            "provider": self.name,
            "db_path": str(self._db_path),
            "export_path": str(self._export_path) if self._export_path else "(none)",
        }

    # ── Private ──────────────────────────────────────────────────────

    def _all_chunks(self) -> list[MemoryChunk]:
        if self._chunks_cache is not None:
            return self._chunks_cache

        chunks: list[MemoryChunk] = []

        # 1. Export path (most reliable if user provides one)
        if self._export_path and self._export_path.exists():
            chunks.extend(self._parse_export_folder(self._export_path))

        # 2. Cursor's local SQLite (best-effort)
        if self._db_path.exists():
            chunks.extend(self._parse_sqlite(self._db_path))

        self._chunks_cache = chunks
        return chunks

    def _parse_export_folder(self, folder: Path) -> list[MemoryChunk]:
        """Read a folder of JSON/markdown conversation exports."""
        chunks: list[MemoryChunk] = []
        for path in folder.iterdir():
            if path.suffix == ".json":
                chunks.extend(self._parse_export_json(path))
            elif path.suffix in {".md", ".markdown"}:
                chunks.extend(self._parse_export_markdown(path))
        return chunks

    def _parse_export_json(self, path: Path) -> list[MemoryChunk]:
        """Parse an exported JSON conversation. Format varies; do best effort."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Cursor export JSON unreadable %s: %s", path, e)
            return []

        # Common shapes: {"messages": [...]} or [{role, content}...]
        messages = data.get("messages") if isinstance(data, dict) else data
        if not isinstance(messages, list):
            return []

        return _exchanges_from_messages(
            messages,
            source=str(path),
            conv_id=path.stem,
            provider_tag="cursor-export",
        )

    def _parse_export_markdown(self, path: Path) -> list[MemoryChunk]:
        """Parse an exported markdown conversation as one chunk.

        Cursor's markdown export is prose with role headers. We keep it as
        one chunk per file for simplicity.
        """
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        if not content.strip():
            return []
        stat = path.stat()
        ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        chunk_id = hashlib.sha256(f"cursor-md:{path}:{stat.st_mtime}".encode()).hexdigest()[:16]
        return [MemoryChunk(
            id=chunk_id,
            content=content,
            source=str(path),
            timestamp=ts,
            metadata={"provider": "cursor-export", "format": "markdown"},
        )]

    def _parse_sqlite(self, db_path: Path) -> list[MemoryChunk]:
        """Extract chat data from Cursor's state.vscdb. Best-effort."""
        chunks: list[MemoryChunk] = []
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            logger.warning("Cursor SQLite unreachable %s: %s", db_path, e)
            return []

        try:
            # Which tables exist (Cursor shipped ItemTable and cursorDiskKV variants)
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

            for table in ("cursorDiskKV", "ItemTable"):
                if table not in tables:
                    continue
                chunks.extend(self._extract_from_table(conn, table))
        except sqlite3.Error as e:
            logger.warning("Cursor SQLite query failed: %s", e)
        finally:
            conn.close()

        return chunks

    def _extract_from_table(self, conn, table: str) -> list[MemoryChunk]:
        """Pull matching rows and parse their JSON payloads."""
        chunks: list[MemoryChunk] = []
        # Build LIKE patterns for known chat keys
        patterns = [f"{pref}%" for pref in CHAT_KEY_PREFIXES] + \
                   [f"%{pref}%" for pref in CHAT_KEY_PREFIXES]
        try:
            for pattern in patterns:
                rows = conn.execute(
                    f"SELECT key, value FROM {table} WHERE key LIKE ?", (pattern,),
                ).fetchall()
                for row in rows:
                    chunks.extend(self._parse_blob(row["key"], row["value"], table))
        except sqlite3.Error as e:
            logger.debug("Cursor table %s scan failed: %s", table, e)
        return chunks

    def _parse_blob(self, key: str, value, table: str) -> list[MemoryChunk]:
        """Parse a single row's JSON blob into chunks. Format varies."""
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return []
        if not isinstance(value, str):
            return []

        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return []

        # Cursor stores chat data in nested structures. We probe for known
        # shapes: list of tabs, each with messages; or a single messages array.
        tabs = None
        if isinstance(data, dict):
            tabs = data.get("tabs") or data.get("composers") or data.get("conversations")
        if isinstance(tabs, list):
            chunks: list[MemoryChunk] = []
            for tab in tabs:
                chunks.extend(self._parse_tab(tab, key, table))
            return chunks

        # Fallback: treat top-level as a single conversation
        return self._parse_tab(data, key, table)

    def _parse_tab(self, tab, key: str, table: str) -> list[MemoryChunk]:
        if not isinstance(tab, dict):
            return []
        messages = tab.get("messages") or tab.get("bubbles") or tab.get("richText")
        if not isinstance(messages, list):
            return []
        conv_id = tab.get("tabId") or tab.get("composerId") or tab.get("id") or key
        return _exchanges_from_messages(
            messages,
            source=f"cursor-db://{table}/{key}",
            conv_id=str(conv_id),
            provider_tag="cursor-sqlite",
        )


# ── Helpers ─────────────────────────────────────────────────────────


def _exchanges_from_messages(messages, source: str, conv_id: str,
                              provider_tag: str) -> list[MemoryChunk]:
    """Generic pairing: walk a list of {role, content}-ish messages, emit exchanges."""
    chunks: list[MemoryChunk] = []
    pending_user = None
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        role = m.get("role") or m.get("type") or ""
        if role == "user" or role == 1:
            content = _get_text(m)
            if content:
                pending_user = content
        elif role == "assistant" or role == "bot" or role == 2:
            content = _get_text(m)
            if content and pending_user:
                exchange = f"[user]: {pending_user}\n\n[assistant]: {content}"
                chunk_id = hashlib.sha256(
                    f"{provider_tag}:{conv_id}:{i}:{exchange[:100]}".encode()
                ).hexdigest()[:16]
                chunks.append(MemoryChunk(
                    id=chunk_id,
                    content=exchange,
                    source=source,
                    timestamp="",  # per-message timestamps often missing in cursor
                    metadata={
                        "conversation_id": conv_id,
                        "exchange_index": len(chunks),
                        "provider": provider_tag,
                    },
                ))
                pending_user = None
    return chunks


def _get_text(message: dict) -> str:
    """Extract the best-available text from a message dict."""
    for key in ("text", "content", "richText", "message"):
        v = message.get(key)
        if isinstance(v, str) and v.strip():
            return v
    # Occasionally content is a list of parts
    parts = message.get("parts") or message.get("content")
    if isinstance(parts, list):
        pieces = []
        for p in parts:
            if isinstance(p, str):
                pieces.append(p)
            elif isinstance(p, dict):
                t = p.get("text") or p.get("value")
                if isinstance(t, str):
                    pieces.append(t)
        return "\n".join(pieces)
    return ""
