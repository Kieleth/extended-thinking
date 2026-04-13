"""FolderProvider — reads .md/.txt files from a directory.

The simplest possible provider. No dependencies beyond stdlib.
Useful as a fallback and as the reference implementation for the protocol.

ADR 013 C2: each FolderProvider instance carries a `namespace`. Chunks
emitted by this provider get that namespace stamped in their metadata;
`Pipeline.sync` reads it off the source chunk and uses it when writing
concepts, so notes from `~/vault/notes` end up under `memory:notes`
and stay isolated from notes under `~/writing` (`memory:writing`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".md", ".txt", ".markdown", ".rst", ".text"}
INSIGHTS_DIR = "_insights"


def namespace_for_root(root: Path) -> str:
    """Derive a memory:<slug> namespace from a folder root.

    `~/vault/notes`      → `memory:notes`
    `~/My Writing`       → `memory:my-writing`
    `~/Documents`        → `memory:documents`
    `~` (empty basename) → `memory`

    Lowercase, ASCII-safe, hyphen-separated. Stable across platforms.
    """
    basename = (root.name or "").lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", basename).strip("-")
    return f"memory:{slug}" if slug else "memory"


class FolderProvider:
    """Memory provider that reads text files from a directory.

    Simple, zero-dependency, works everywhere. Stores insights as
    markdown files in a _insights/ subdirectory.
    """

    def __init__(self, root: Path, *, namespace: str | None = None):
        self._root = Path(root)
        self._insights_dir = self._root / INSIGHTS_DIR
        # Per-folder namespace: either explicit (from config) or derived
        # from the folder's basename. Stamped on every emitted chunk.
        self._namespace = namespace or namespace_for_root(self._root)

    @property
    def name(self) -> str:
        return "folder"

    @property
    def namespace(self) -> str:
        return self._namespace

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        """Case-insensitive substring search across all text files."""
        query_lower = query.lower()
        results: list[MemoryChunk] = []

        for chunk in self._iter_chunks():
            if query_lower in chunk.content.lower():
                results.append(chunk)
                if len(results) >= limit:
                    break

        return results

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        """Get chunks sorted by file modification time, newest first."""
        chunks = list(self._iter_chunks())

        if since:
            chunks = [c for c in chunks if c.timestamp >= since]

        chunks.sort(key=lambda c: c.timestamp, reverse=True)
        return chunks[:limit]

    def get_entities(self) -> list[Entity]:
        """FolderProvider does not extract entities."""
        return []

    def get_knowledge_graph(self):
        """FolderProvider has no structured knowledge."""
        return None

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        """Store an insight as a markdown file in _insights/."""
        self._insights_dir.mkdir(exist_ok=True)

        now = datetime.now(timezone.utc)
        slug = title.lower().replace(" ", "-")[:40]
        filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{slug}.md"
        insight_id = hashlib.sha256(f"{title}{now.isoformat()}".encode()).hexdigest()[:16]

        content = (
            f"# {title}\n\n"
            f"{description}\n\n"
            f"---\n"
            f"Related concepts: {', '.join(related_concepts)}\n"
            f"Generated: {now.isoformat()}\n"
            f"ID: {insight_id}\n"
        )

        (self._insights_dir / filename).write_text(content, encoding="utf-8")
        logger.info("Stored insight %s in %s", insight_id, filename)
        return insight_id

    def get_insights(self) -> list[MemoryChunk]:
        """Retrieve insights from the _insights/ subdirectory."""
        if not self._insights_dir.exists():
            return []
        return [
            self._file_to_chunk(f)
            for f in sorted(self._insights_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        ]

    def get_stats(self) -> dict:
        """Count text files and insights."""
        text_files = list(self._iter_text_files())
        insight_count = len(list(self._insights_dir.glob("*.md"))) if self._insights_dir.exists() else 0
        last_updated = max((f.stat().st_mtime for f in text_files), default=0)

        return {
            "total_memories": len(text_files),
            "total_insights": insight_count,
            "last_updated": datetime.fromtimestamp(last_updated, tz=timezone.utc).isoformat() if last_updated else None,
            "provider": self.name,
            "root": str(self._root),
        }

    # ── Private ──────────────────────────────────────────────────────

    def _iter_text_files(self):
        """Yield text files in the root directory (non-recursive, skip _insights)."""
        if not self._root.exists():
            return
        for f in self._root.iterdir():
            if f.is_file() and f.suffix.lower() in TEXT_EXTENSIONS and f.parent.name != INSIGHTS_DIR:
                yield f

    def _iter_chunks(self):
        """Yield MemoryChunks from all text files."""
        for f in self._iter_text_files():
            yield self._file_to_chunk(f)

    def _file_to_chunk(self, path: Path) -> MemoryChunk:
        """Convert a text file to a MemoryChunk."""
        content = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        chunk_id = hashlib.sha256(f"{path}{stat.st_mtime}".encode()).hexdigest()[:16]

        return MemoryChunk(
            id=chunk_id,
            content=content,
            source=str(path),
            timestamp=mtime,
            metadata={
                "filename": path.name,
                "size_bytes": stat.st_size,
                # Namespace travels with the chunk so Pipeline.sync scopes
                # the extracted concepts to the right folder-project.
                "namespace": self._namespace,
                "provider": "folder",
            },
        )
