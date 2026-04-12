"""CopilotChatProvider — ingests VSCode GitHub Copilot Chat history.

VSCode's Copilot Chat extension persists each session as a JSON file under
the user's workspaceStorage directory. Each workspace has its own hash
subdirectory; within that, `chatSessions/` (or similar) contains session
files.

Typical path (Mac):
    ~/Library/Application Support/Code/User/workspaceStorage/<workspace_hash>/chatSessions/<session_uuid>.json

Linux:
    ~/.config/Code/User/workspaceStorage/<workspace_hash>/chatSessions/*.json

Windows:
    %APPDATA%/Code/User/workspaceStorage/<workspace_hash>/chatSessions/*.json

Session file format (v1, subject to VSCode updates):
    {
      "version": 1,
      "sessionId": "...",
      "creationDate": <epoch_ms>,
      "requests": [
        {
          "message": {"text": "user prompt", "parts": [...]},
          "response": {"value": [{"kind": "markdown", "value": "..."}, ...]},
          "result": {...},
          "timestamp": <epoch_ms>
        },
        ...
      ]
    }

We extract each (request.message, response) pair as one exchange chunk.
Unknown response kinds (e.g., 'inlineReference', 'treeData') are best-effort
serialized as text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)

def _insights_dir() -> Path:
    from extended_thinking.config import settings
    return settings.data.root / "insights" / "copilot-chat"


def _default_vscode_user_dir() -> Path:
    """Platform-specific VSCode user data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    if sys.platform == "win32":
        appdata = Path.home() / "AppData" / "Roaming"
        return appdata / "Code" / "User"
    # Linux
    return Path.home() / ".config" / "Code" / "User"


class CopilotChatProvider:
    """Memory provider for VSCode Copilot Chat session files."""

    # Directory names Copilot Chat has used across VSCode versions.
    # We scan all of them so we don't miss sessions after format migrations.
    SESSION_SUBDIRS = ["chatSessions", "chat-sessions", "interactiveSessions"]

    def __init__(self, user_dir: Path | None = None):
        """
        Args:
            user_dir: VSCode user data dir. Defaults to platform-standard location.
        """
        self._user_dir = Path(user_dir) if user_dir else _default_vscode_user_dir()
        self._chunks_cache: list[MemoryChunk] | None = None

    @property
    def name(self) -> str:
        return "copilot-chat"

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
                logger.warning("Failed to read insight %s: %s", f, e)
        return insights

    def get_stats(self) -> dict:
        session_files = list(self._iter_session_files())
        chunks = self._all_chunks()
        return {
            "total_memories": len(chunks),
            "total_sessions": len(session_files),
            "total_insights": len(self.get_insights()),
            "provider": self.name,
            "user_dir": str(self._user_dir),
        }

    # ── Private ──────────────────────────────────────────────────────

    def _all_chunks(self) -> list[MemoryChunk]:
        if self._chunks_cache is not None:
            return self._chunks_cache
        chunks: list[MemoryChunk] = []
        for session_path in self._iter_session_files():
            chunks.extend(self._parse_session(session_path))
        self._chunks_cache = chunks
        return chunks

    def _iter_session_files(self):
        """Yield all Copilot Chat session JSON files across workspaces."""
        if not self._user_dir.exists():
            return
        workspace_storage = self._user_dir / "workspaceStorage"
        if not workspace_storage.exists():
            return

        for ws_dir in workspace_storage.iterdir():
            if not ws_dir.is_dir():
                continue
            for subdir_name in self.SESSION_SUBDIRS:
                sessions_dir = ws_dir / subdir_name
                if sessions_dir.exists() and sessions_dir.is_dir():
                    for session_file in sessions_dir.glob("*.json"):
                        yield session_file

    def _parse_session(self, path: Path) -> list[MemoryChunk]:
        """Parse a single session file into exchange-pair MemoryChunks."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Failed to read Copilot Chat session %s: %s", path, e)
            return []

        session_id = data.get("sessionId") or path.stem
        creation = _epoch_ms_to_iso(data.get("creationDate"))
        workspace_hash = path.parent.parent.name  # workspaceStorage/<hash>/chatSessions/file.json

        requests = data.get("requests") or []
        chunks: list[MemoryChunk] = []
        for i, req in enumerate(requests):
            user_text = _extract_request_text(req.get("message") or {})
            assistant_text = _extract_response_text(req.get("response") or {})
            if not user_text.strip() or not assistant_text.strip():
                continue
            ts = _epoch_ms_to_iso(req.get("timestamp")) or creation
            exchange = f"[user]: {user_text}\n\n[assistant]: {assistant_text}"
            chunk_id = hashlib.sha256(
                f"copilot:{session_id}:{i}:{exchange[:100]}".encode()
            ).hexdigest()[:16]
            chunks.append(MemoryChunk(
                id=chunk_id,
                content=exchange,
                source=str(path),
                timestamp=ts,
                metadata={
                    "session_id": session_id,
                    "workspace_hash": workspace_hash,
                    "exchange_index": i,
                    "provider": "copilot-chat",
                },
            ))
        return chunks


# ── Helpers ─────────────────────────────────────────────────────────


def _epoch_ms_to_iso(epoch_ms) -> str:
    if epoch_ms is None:
        return ""
    try:
        return datetime.fromtimestamp(
            float(epoch_ms) / 1000.0, tz=timezone.utc,
        ).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def _extract_request_text(message) -> str:
    """Pull user-visible text from a request.message object.

    Copilot Chat requests have shape {"text": "...", "parts": [...]} or similar.
    """
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return str(message) if message else ""

    text = message.get("text")
    if isinstance(text, str) and text:
        return text

    parts = message.get("parts")
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


def _extract_response_text(response) -> str:
    """Pull assistant-visible text from a response object.

    Copilot Chat responses have `response.value` which is a list of parts
    like {"kind": "markdown", "value": "..."}, {"kind": "inlineReference", ...}.
    """
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return str(response) if response else ""

    value = response.get("value")
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        pieces = []
        for part in value:
            if isinstance(part, str):
                pieces.append(part)
                continue
            if not isinstance(part, dict):
                continue
            kind = part.get("kind", "")
            # Known text-bearing kinds
            if kind in ("markdown", "text"):
                text = part.get("value")
                if isinstance(text, str):
                    pieces.append(text)
                elif isinstance(text, dict):
                    inner = text.get("value")
                    if isinstance(inner, str):
                        pieces.append(inner)
            elif kind == "inlineReference":
                ref = part.get("inlineReference") or {}
                name = ref.get("name") or ref.get("uri") or ""
                if name:
                    pieces.append(f"[ref: {name}]")
            # Best-effort fallback for unknown kinds with a 'value' key
            elif "value" in part and isinstance(part["value"], str):
                pieces.append(part["value"])
        return "\n".join(pieces)
    return ""
