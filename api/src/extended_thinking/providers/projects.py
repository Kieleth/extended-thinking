"""ProjectsProvider — recursive project-meta scanner.

Walks configured root directories (e.g. ~/Projects, ~/code) looking for
files matching the configured patterns. The canonical harvest target is
`CLAUDE.md` (per-project assistant context), with `AGENTS.md`, top-level
`README.md`, and `docs/**/*.md` as the default siblings. Any file in a
directory without a `.git` ancestor is skipped by default — "a folder
without a git root isn't a project."

Each ingested file becomes a `MemoryChunk` whose metadata is tagged
with its project namespace: `memory:project:<git-root-basename>`.
`Pipeline.sync` reads that off the chunk and writes concepts scoped to
that namespace, so harvesting CLAUDE.md from `~/code/autoresearch-ET`
and `~/code/malleus` keeps their concepts in separate slices of the
graph.

Design notes:
  - Read-only. The provider never writes into user projects.
  - Skips common noise dirs (`node_modules`, `.venv`, `__pycache__`).
  - Caps files per project (default 50) so a repo with hundreds of
    ADRs doesn't dominate the extraction budget.
  - Dedups by absolute path — multiple roots pointing at overlapping
    subtrees won't ingest the same file twice.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)

# Directories we never descend into. These are tool caches, build
# artefacts, or package sources that would pollute the concept graph.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", ".env",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".hypothesis", ".tox",
    "dist", "build", "target", "_build",
    ".next", ".nuxt", ".svelte-kit",
}


def namespace_for_project(project_root: Path) -> str:
    """Derive `memory:project:<slug>` from a repo root path.

    `~/Projects/extended_thinking` → `memory:project:extended-thinking`
    `~/code/my-vault/notes`        → `memory:project:notes`   (nearest root)
    """
    basename = (project_root.name or "").lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", basename).strip("-")
    return f"memory:project:{slug}" if slug else "memory:project"


class ProjectsProvider:
    """Recursive scanner for project-meta files (CLAUDE.md and friends).

    One provider instance handles multiple roots. Per-file namespace is
    derived from the nearest enclosing `.git` directory; chunks emit
    their namespace in metadata so `Pipeline.sync` routes concepts into
    per-project namespaces automatically.
    """

    name = "projects"

    def __init__(
        self,
        roots: list[Path],
        *,
        patterns: list[str] | None = None,
        require_git: bool = True,
        max_files_per_project: int = 50,
    ):
        self._roots = [Path(r).expanduser() for r in roots]
        self._patterns = patterns or [
            "CLAUDE.md", "AGENTS.md", "README.md", "docs/**/*.md",
        ]
        self._require_git = require_git
        self._max_per_project = max_files_per_project

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        """All project-meta files from every configured root.

        Sorted newest-first by file mtime. `since` filters on ISO
        timestamp. `limit` caps the total.
        """
        chunks = list(self._iter_chunks())
        if since:
            chunks = [c for c in chunks if c.timestamp >= since]
        chunks.sort(key=lambda c: c.timestamp, reverse=True)
        return chunks[:limit]

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        """Case-insensitive substring search across harvested files."""
        q = query.lower()
        results: list[MemoryChunk] = []
        for chunk in self._iter_chunks():
            if q in chunk.content.lower():
                results.append(chunk)
                if len(results) >= limit:
                    break
        return results

    def get_entities(self) -> list[Entity]:
        return []

    def get_knowledge_graph(self):
        return None

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        # This provider is read-only. Insights belong to ET's own store.
        return ""

    def get_insights(self) -> list[MemoryChunk]:
        return []

    def get_stats(self) -> dict:
        files = list(self._iter_files_with_projects())
        projects = {proj for _, proj in files}
        last = 0.0
        for f, _ in files:
            try:
                last = max(last, f.stat().st_mtime)
            except OSError:
                continue
        return {
            "total_memories": len(files),
            "total_projects": len(projects),
            "last_updated": (
                datetime.fromtimestamp(last, tz=timezone.utc).isoformat()
                if last else None
            ),
            "provider": self.name,
            "roots": [str(r) for r in self._roots],
        }

    # ── Private ──────────────────────────────────────────────────────

    def _iter_files_with_projects(self):
        """Yield (file_path, project_root) pairs for every match under
        every configured root. Dedup by absolute path so overlapping
        roots don't double-count."""
        seen: set[Path] = set()
        per_project_count: dict[Path, int] = {}
        for root in self._roots:
            if not root.exists():
                continue
            for file_path, project_root in self._scan_root(root):
                abs_path = file_path.resolve()
                if abs_path in seen:
                    continue
                if per_project_count.get(project_root, 0) >= self._max_per_project:
                    continue
                seen.add(abs_path)
                per_project_count[project_root] = per_project_count.get(project_root, 0) + 1
                yield abs_path, project_root

    def _scan_root(self, root: Path):
        """Walk files under `root`, match each against the patterns,
        and resolve each match's project root.

        require_git=True:  each file must have a `.git` ancestor inside
                           the root. The nearest `.git` parent is the
                           project root.
        require_git=False: the root itself is the project; every
                           matching file belongs to it.
        """
        if self._require_git:
            # Walk directories top-down; for each git repo, collect its
            # matching files via project-root-relative globbing. Prune
            # noise dirs so we don't descend into node_modules.
            for dirpath in self._walk_dirs(root):
                if (dirpath / ".git").exists():
                    for match in self._files_matching_patterns(dirpath):
                        yield match, dirpath
        else:
            # No git gate — root is the project, all matches belong to it.
            for match in self._files_matching_patterns(root):
                yield match, root

    def _files_matching_patterns(self, project_root: Path) -> list[Path]:
        """All files under `project_root` matching any pattern. Exact
        filenames (no `/`, no `*`) are found recursively via rglob so a
        bare `CLAUDE.md` pattern still catches `subdir/CLAUDE.md`.
        Patterns with slashes are resolved with `glob` relative to the
        project root (supports `docs/**/*.md`)."""
        matched: set[Path] = set()
        for pattern in self._patterns:
            try:
                if "/" in pattern or "*" in pattern:
                    iterator = project_root.glob(pattern)
                else:
                    # Bare filename — recursive hunt. Matches any depth
                    # so nested docs folders still surface their READMEs.
                    iterator = project_root.rglob(pattern)
                for match in iterator:
                    if not match.is_file():
                        continue
                    # Skip matches that traversed a noise dir.
                    if any(part in _SKIP_DIRS for part in match.parts):
                        continue
                    matched.add(match)
            except OSError:
                continue
        return sorted(matched, key=lambda p: str(p))

    def _walk_dirs(self, start: Path):
        """Depth-first directory walk, pruning noise dirs."""
        stack: list[Path] = [start]
        while stack:
            current = stack.pop()
            if not current.is_dir():
                continue
            yield current
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir() and entry.name not in _SKIP_DIRS and not entry.is_symlink():
                    stack.append(entry)

    def _iter_chunks(self):
        for path, project_root in self._iter_files_with_projects():
            chunk = self._file_to_chunk(path, project_root)
            if chunk is not None:
                yield chunk

    def _file_to_chunk(self, path: Path, project_root: Path) -> MemoryChunk | None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
        except OSError as e:
            logger.debug("projects: skipping %s (%s)", path, e)
            return None

        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        chunk_id = hashlib.sha256(f"{path}{stat.st_mtime}".encode()).hexdigest()[:16]
        namespace = namespace_for_project(project_root)

        return MemoryChunk(
            id=chunk_id,
            content=content,
            source=str(path),
            timestamp=mtime,
            metadata={
                "filename": path.name,
                "size_bytes": stat.st_size,
                "project_root": str(project_root),
                "namespace": namespace,
                "provider": "projects",
            },
        )
