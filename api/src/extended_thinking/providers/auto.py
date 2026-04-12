"""AutoProvider — aggregates data from all available sources.

Detects all installed providers and merges their chunks on get_recent().
This is multi-provider sync: Claude Code conversations, MemPalace .md files,
and folder notes all feed into a single stream.

Detection order (all that exist are used, not just the first):
  1. Claude Code (~/.claude/projects/) — conversation transcripts (primary thinking data)
  2. MemPalace (~/.mempalace/) — project files, semantic search
  3. ~/Documents/ or ~/Notes/ — text files

For KG access, delegates to the first provider that has one (mempalace).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from extended_thinking.providers.protocol import Entity, MemoryChunk

logger = logging.getLogger(__name__)


def _vscode_user_dir_for_home(home: Path) -> Path:
    """Platform-specific VSCode user dir, rooted in a given home (for testability)."""
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Code" / "User"
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "Code" / "User"
    return home / ".config" / "Code" / "User"


def _cursor_db_for_home(home: Path) -> Path:
    """Platform-specific Cursor state.vscdb rooted in a given home."""
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Cursor"
    elif sys.platform == "win32":
        base = home / "AppData" / "Roaming" / "Cursor"
    else:
        base = home / ".config" / "Cursor"
    return base / "User" / "globalStorage" / "state.vscdb"


def _find_chatgpt_export_in_home(
    home: Path,
    scan_paths: list | None = None,
) -> Path | None:
    """Scan for a ChatGPT export (zip/folder/conversations.json).

    scan_paths (from `[providers.chatgpt_export] scan_paths`) takes
    precedence. If empty, falls back to the classic Downloads + Documents
    locations under `home`.

    Separated from ChatGPTExportProvider's own `_resolve_path` so detection
    can honor a test's fake home without relying on Path.home().
    """
    if scan_paths:
        bases = [Path(p).expanduser() for p in scan_paths]
    else:
        bases = [home / "Downloads", home / "Documents"]

    for base in bases:
        if not base.exists():
            continue
        direct = base / "conversations.json"
        if direct.exists():
            return direct
        for zip_path in base.glob("*.zip"):
            lower = zip_path.name.lower()
            if "chatgpt" in lower or "openai" in lower:
                return zip_path
        for folder in base.iterdir():
            if folder.is_dir() and (folder / "conversations.json").exists():
                return folder / "conversations.json"
    return None


class AutoProvider:
    """Aggregates all available memory providers into one stream.

    get_recent() merges chunks from all detected providers.
    search() delegates to the provider with the best search (mempalace if available).
    get_knowledge_graph() delegates to the first provider that has one.
    """

    def __init__(self, home_dir: Path | None = None):
        self._home = home_dir or Path.home()
        self._providers: list = []
        self._primary = None  # Best provider for search/KG
        self._detect_all()

    @property
    def name(self) -> str:
        return "auto"

    def search(self, query: str, limit: int = 20) -> list[MemoryChunk]:
        if self._primary:
            return self._primary.search(query, limit)
        # Fallback: search all providers
        results = []
        for p in self._providers:
            results.extend(p.search(query, limit))
        return results[:limit]

    def get_recent(self, since: str | None = None, limit: int = 50) -> list[MemoryChunk]:
        """Merge chunks from ALL providers, sorted by timestamp, deduped by ID."""
        all_chunks: list[MemoryChunk] = []
        seen_ids: set[str] = set()

        per_provider = max(limit // len(self._providers), 20) if self._providers else limit
        for p in self._providers:
            for chunk in p.get_recent(since=since, limit=per_provider):
                if chunk.id not in seen_ids:
                    seen_ids.add(chunk.id)
                    all_chunks.append(chunk)

        all_chunks.sort(key=lambda c: c.timestamp or "", reverse=True)
        return all_chunks[:limit]

    def get_entities(self) -> list[Entity]:
        entities = []
        for p in self._providers:
            entities.extend(p.get_entities())
        return entities

    def store_insight(self, title: str, description: str,
                      related_concepts: list[str]) -> str:
        if self._primary:
            return self._primary.store_insight(title, description, related_concepts)
        from extended_thinking.providers.folder import FolderProvider
        fallback = FolderProvider(self._home / ".extended-thinking")
        return fallback.store_insight(title, description, related_concepts)

    def get_insights(self) -> list[MemoryChunk]:
        insights = []
        for p in self._providers:
            insights.extend(p.get_insights())
        return insights

    def get_knowledge_graph(self):
        for p in self._providers:
            kg = p.get_knowledge_graph()
            if kg is not None:
                return kg
        return None

    def get_stats(self) -> dict:
        if not self._providers:
            return {
                "total_memories": 0,
                "detected_provider": None,
                "last_updated": None,
                "provider": "auto",
            }
        total = sum(p.get_stats().get("total_memories", 0) for p in self._providers)
        names = [p.name for p in self._providers]
        return {
            "total_memories": total,
            "detected_providers": names,
            "detected_provider": "+".join(names),
            "provider": "auto",
        }

    # ── Detection ────────────────────────────────────────────────────

    def _detect_all(self):
        """Detect ALL available providers.

        Each provider is gated by `[providers.<name>] enabled` in the
        centralized config (ADR 012). Paths derive from `self._home` so
        tests passing a fake home get pure isolation; the configured
        path only wins if it has been explicitly overridden away from
        the schema default.
        """
        from extended_thinking.config import settings
        from extended_thinking.config.schema import ClaudeCodeProviderConfig
        pc = settings.providers
        # A `default` provider config is the "as shipped" one; if the user's
        # current config matches it, we treat paths as derivable from self._home
        # (which tests override). If it doesn't match, the user explicitly
        # configured a path and we honor it verbatim.
        default_pc_claude = ClaudeCodeProviderConfig()

        # Claude Code sessions (primary thinking data)
        if pc.claude_code.enabled:
            if pc.claude_code.projects_dir == default_pc_claude.projects_dir:
                claude_dir = self._home / ".claude" / "projects"
            else:
                claude_dir = pc.claude_code.projects_dir
            if claude_dir.exists() and any(claude_dir.iterdir()):
                from extended_thinking.providers.claude_code import ClaudeCodeProvider
                provider = ClaudeCodeProvider(claude_dir)
                self._providers.append(provider)
                logger.info("AutoProvider: detected Claude Code at %s", claude_dir)

        # ChatGPT export. Config may override the scan roots.
        if pc.chatgpt_export.enabled:
            chatgpt_path = _find_chatgpt_export_in_home(
                self._home, scan_paths=pc.chatgpt_export.scan_paths,
            )
            if chatgpt_path is not None:
                from extended_thinking.providers.chatgpt_export import ChatGPTExportProvider
                self._providers.append(ChatGPTExportProvider(export_path=chatgpt_path))
                logger.info("AutoProvider: detected ChatGPT export at %s", chatgpt_path)

        # Copilot Chat (VSCode Copilot Chat extension sessions).
        if pc.copilot_chat.enabled:
            copilot_user_dir = _vscode_user_dir_for_home(self._home)
            if copilot_user_dir.exists() and (copilot_user_dir / "workspaceStorage").exists():
                from extended_thinking.providers.copilot_chat import CopilotChatProvider
                copilot_provider = CopilotChatProvider(copilot_user_dir)
                if any(copilot_provider._iter_session_files()):
                    self._providers.append(copilot_provider)
                    logger.info("AutoProvider: detected Copilot Chat at %s", copilot_user_dir)

        # Cursor editor chat (local SQLite).
        if pc.cursor.enabled:
            cursor_db = _cursor_db_for_home(self._home)
            if cursor_db.exists():
                from extended_thinking.providers.cursor import CursorProvider
                self._providers.append(CursorProvider(db_path=cursor_db))
                logger.info("AutoProvider: detected Cursor at %s", cursor_db)

        # MemPalace (project files + KG) — optional
        if pc.mempalace.enabled:
            mempalace_dir = self._home / ".mempalace"
            if mempalace_dir.exists() and (mempalace_dir / "palace").exists():
                try:
                    from extended_thinking.providers.mempalace import MemPalaceProvider
                    provider = MemPalaceProvider(mempalace_dir)
                    self._providers.append(provider)
                    self._primary = provider  # Best search + has KG
                    logger.info("AutoProvider: detected MemPalace at %s", mempalace_dir)
                except ImportError:
                    logger.debug("MemPalaceProvider not available (missing chromadb?)")

        # Folders. Config paths first (explicit), then detected Documents/Notes.
        if pc.folder.enabled:
            from extended_thinking.providers.folder import FolderProvider
            for explicit_path in pc.folder.paths:
                p = Path(explicit_path).expanduser()
                if p.exists():
                    self._providers.append(FolderProvider(p))
                    logger.info("AutoProvider: configured folder %s", p)
            # Auto-detected fallback: first Documents/Notes folder that has content
            for folder_name in ["Documents", "documents", "Notes", "notes"]:
                folder = self._home / folder_name
                if folder.exists() and (any(folder.glob("*.md")) or any(folder.glob("*.txt"))):
                    # Avoid double-adding if already configured explicitly
                    if not any(isinstance(p, FolderProvider) and p._folder == folder
                               for p in self._providers):
                        self._providers.append(FolderProvider(folder))
                        logger.info("AutoProvider: detected folder %s", folder)
                    break

        if not self._providers:
            logger.info("AutoProvider: no data sources found")

        if not self._primary and self._providers:
            self._primary = self._providers[0]
