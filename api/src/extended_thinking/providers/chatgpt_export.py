"""ChatGPTExportProvider — ingests ChatGPT conversation exports.

Users can request a full data export from https://chatgpt.com/settings and
receive a zip containing conversations.json (plus media, user.json, etc.).
This provider reads conversations.json directly.

Format overview (subject to OpenAI changes):
  conversations.json is a list of conversation objects. Each has:
    - id: UUID
    - title: string
    - create_time / update_time: unix epoch
    - mapping: dict of message_id -> message node
      Each node has: parent, children, message { author, content, metadata }

The mapping is a DAG (supports branching conversations). We linearize by
walking from root to the most-recent leaf to get the primary thread.

Usage:
    # Point at the export zip or the extracted folder; we auto-detect.
    provider = ChatGPTExportProvider(export_path=Path("~/Downloads/chatgpt-export"))
    chunks = provider.get_recent(limit=100)

Detection: AutoProvider looks for conversations.json in common locations
(Downloads folder, ~/Documents, or a configured path).
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)

# Common locations users save exports
DEFAULT_SEARCH_PATHS = [
    Path.home() / "Downloads",
    Path.home() / "Documents",
]


def _insights_dir() -> Path:
    """Resolve insights dir at call time against current settings.data.root."""
    from extended_thinking.config import settings
    return settings.data.root / "insights" / "chatgpt"


class ChatGPTExportProvider:
    """Memory provider for ChatGPT conversation exports (conversations.json)."""

    def __init__(self, export_path: Path | None = None):
        """
        Args:
            export_path: path to either a zip file, an extracted folder
                containing conversations.json, or the conversations.json file
                itself. If None, provider will search default locations.
        """
        self._export_path = Path(export_path) if export_path else None
        self._conversations_cache: list[dict] | None = None
        self._chunks_cache: list[MemoryChunk] | None = None

    @property
    def name(self) -> str:
        return "chatgpt-export"

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        """Keyword search across conversation turns."""
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
        insights_dir = _insights_dir()
        insights_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        insight_id = hashlib.sha256(f"{title}{now.isoformat()}".encode()).hexdigest()[:16]
        data = {
            "id": insight_id,
            "title": title,
            "description": description,
            "related_concepts": related_concepts,
            "created_at": now.isoformat(),
        }
        (insights_dir / f"{insight_id}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )
        return insight_id

    def get_insights(self) -> list[MemoryChunk]:
        insights_dir = _insights_dir()
        if not insights_dir.exists():
            return []
        insights = []
        for f in sorted(insights_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
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
        conversations = self._get_conversations()
        return {
            "total_memories": len(chunks),
            "total_conversations": len(conversations) if conversations else 0,
            "total_insights": len(self.get_insights()),
            "provider": self.name,
            "export_path": str(self._export_path) if self._export_path else "(not set)",
        }

    # ── Private ──────────────────────────────────────────────────────

    def _all_chunks(self) -> list[MemoryChunk]:
        if self._chunks_cache is not None:
            return self._chunks_cache

        conversations = self._get_conversations()
        if not conversations:
            self._chunks_cache = []
            return self._chunks_cache

        chunks: list[MemoryChunk] = []
        for conv in conversations:
            chunks.extend(self._chunks_from_conversation(conv))

        self._chunks_cache = chunks
        return chunks

    def _get_conversations(self) -> list[dict]:
        """Return the parsed conversations list, or []."""
        if self._conversations_cache is not None:
            return self._conversations_cache

        path = self._resolve_path()
        if path is None:
            self._conversations_cache = []
            return self._conversations_cache

        try:
            data = self._load_conversations_from_path(path)
            self._conversations_cache = data or []
        except Exception as e:
            logger.warning("Failed to load ChatGPT export at %s: %s", path, e)
            self._conversations_cache = []
        return self._conversations_cache

    def _resolve_path(self) -> Path | None:
        """Resolve the export path to a concrete conversations.json location,
        searching default locations if none was provided."""
        if self._export_path:
            if self._export_path.exists():
                return self._export_path
            logger.warning("ChatGPT export path does not exist: %s", self._export_path)
            return None

        # Search defaults for conversations.json, a zip, or an extracted folder
        for base in DEFAULT_SEARCH_PATHS:
            if not base.exists():
                continue
            # Direct conversations.json
            candidate = base / "conversations.json"
            if candidate.exists():
                return candidate
            # Zip archives named like chatgpt-export-*.zip
            for zip_path in base.glob("*.zip"):
                lower = zip_path.name.lower()
                if "chatgpt" in lower or "openai" in lower:
                    return zip_path
            # Extracted folders named similarly
            for folder in base.iterdir():
                if folder.is_dir() and (folder / "conversations.json").exists():
                    return folder / "conversations.json"
        return None

    def _load_conversations_from_path(self, path: Path) -> list[dict]:
        """Load conversations.json from a zip, folder, or direct file."""
        if path.is_file() and path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                # conversations.json is typically at the root of the export
                for name in zf.namelist():
                    if name.endswith("conversations.json"):
                        with zf.open(name) as fp:
                            return json.load(fp)
            return []
        if path.is_dir():
            inner = path / "conversations.json"
            if inner.exists():
                return json.loads(inner.read_text(encoding="utf-8"))
            return []
        if path.is_file() and path.name == "conversations.json":
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def _chunks_from_conversation(self, conv: dict) -> list[MemoryChunk]:
        """Linearize a conversation's DAG and emit exchange-pair chunks."""
        conv_id = conv.get("id", "")
        title = conv.get("title", "") or "(untitled)"
        create_time = _epoch_to_iso(conv.get("create_time"))

        mapping = conv.get("mapping") or {}
        if not mapping:
            return []

        # Walk from root to deepest leaf; most exports have a single thread,
        # but branching conversations exist. We take the longest path.
        path = self._longest_thread(mapping)
        if not path:
            return []

        messages = []
        for mid in path:
            node = mapping.get(mid) or {}
            msg = node.get("message")
            if not msg:
                continue
            role = (msg.get("author") or {}).get("role", "")
            if role not in {"user", "assistant"}:
                continue  # skip system, tool
            content = _extract_content(msg.get("content"))
            if not content.strip():
                continue
            ts = _epoch_to_iso(msg.get("create_time")) or create_time
            messages.append({"role": role, "content": content, "timestamp": ts})

        # Pair user + assistant into exchange chunks
        chunks: list[MemoryChunk] = []
        pending_user = None
        for m in messages:
            if m["role"] == "user":
                pending_user = m
            elif m["role"] == "assistant" and pending_user is not None:
                exchange_text = (
                    f"[user]: {pending_user['content']}\n\n"
                    f"[assistant]: {m['content']}"
                )
                chunk_id = hashlib.sha256(
                    f"chatgpt:{conv_id}:{len(chunks)}:{exchange_text[:100]}".encode()
                ).hexdigest()[:16]
                chunks.append(MemoryChunk(
                    id=chunk_id,
                    content=exchange_text,
                    source=f"chatgpt://{conv_id}",
                    timestamp=pending_user["timestamp"] or m["timestamp"],
                    metadata={
                        "conversation_id": conv_id,
                        "conversation_title": title,
                        "exchange_index": len(chunks),
                        "provider": "chatgpt-export",
                    },
                ))
                pending_user = None
        return chunks

    def _longest_thread(self, mapping: dict) -> list[str]:
        """Return the message IDs along the longest root-to-leaf path in the DAG."""
        # Find root (no parent)
        roots = [mid for mid, node in mapping.items() if not node.get("parent")]
        if not roots:
            return []

        # DFS from each root, pick the longest path
        best: list[str] = []
        for root in roots:
            path = self._deepest_descendant(mapping, root)
            if len(path) > len(best):
                best = path
        return best

    def _deepest_descendant(self, mapping: dict, start: str) -> list[str]:
        """DFS: find the deepest path from `start`, picking latest child at branches."""
        path = [start]
        current = start
        while True:
            node = mapping.get(current) or {}
            children = node.get("children") or []
            if not children:
                break
            # If multiple children, prefer the one with the most-recent update
            if len(children) == 1:
                nxt = children[0]
            else:
                nxt = max(
                    children,
                    key=lambda c: _msg_time(mapping.get(c)),
                )
            path.append(nxt)
            current = nxt
        return path


# ── Helpers ─────────────────────────────────────────────────────────


def _epoch_to_iso(epoch) -> str:
    """Convert a unix epoch (float or int) to ISO 8601."""
    if epoch is None:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def _msg_time(node) -> float:
    """Extract sortable timestamp from a mapping node. 0 if unavailable."""
    if not node:
        return 0.0
    msg = node.get("message") or {}
    t = msg.get("create_time")
    try:
        return float(t) if t is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _extract_content(content) -> str:
    """Extract readable text from a message content field.

    ChatGPT content is typically { content_type: "text", parts: [...] }.
    Occasionally other types (code, multimodal) appear; we take what we can.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return str(content) if content else ""

    ctype = content.get("content_type", "")
    parts = content.get("parts")

    if ctype == "text" and isinstance(parts, list):
        return "\n".join(p for p in parts if isinstance(p, str) and p.strip())

    # Fallback: join any string parts present
    if isinstance(parts, list):
        return "\n".join(p for p in parts if isinstance(p, str))

    # Other content types (e.g., code): try the 'text' key
    text = content.get("text")
    if isinstance(text, str):
        return text

    return ""
