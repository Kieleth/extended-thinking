#!/usr/bin/env python3
"""Extended-thinking CLI.

Usage:
  et insight              # sync + generate wisdom
  et concepts             # list concepts
  et sync                 # pull from provider
  et stats                # show stats
  et init                 # register ET as an MCP server with CC / Claude Desktop
  et reset [--go-home]    # wipe all ET state (dry-run unless --go-home)
  et mcp-serve            # run the MCP server (usually invoked by a client, not humans)
"""

from __future__ import annotations

# ── Silence the noise floor BEFORE any heavy import ──────────────────
# Must happen at module top so the environment is set before chromadb,
# huggingface/tokenizers, or any transitive import sees it. These are
# not our warnings — they leak from dependencies and make ET output
# look unfinished.
import os as _os
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")   # HF fork warning
_os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")     # chromadb posthog
_os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "none")

import warnings as _warnings
_warnings.filterwarnings("ignore", module=r"chromadb\..*")
_warnings.filterwarnings("ignore", module=r"posthog\..*")
_warnings.filterwarnings("ignore", module=r"transformers\..*")

import logging as _logging
# Chromadb logs telemetry errors at ERROR level, not WARNING, so a filter
# on warnings alone doesn't catch them. Raise the floor for those loggers
# specifically — we don't want their internal problems on our stdout.
for _name in ("chromadb.telemetry", "chromadb.telemetry.product.posthog",
              "posthog", "httpx"):
    _logging.getLogger(_name).setLevel(_logging.CRITICAL + 1)

# ── Real imports ─────────────────────────────────────────────────────

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from extended_thinking import cli_style as style


def _get_pipeline():
    """Construct the pipeline. Raises DataDirConflict if both legacy and
    XDG data dirs exist — the caller renders a notice."""
    from extended_thinking.config import migrate_data_dir, settings
    from extended_thinking.processing.pipeline_v2 import Pipeline
    from extended_thinking.providers import get_provider
    from extended_thinking.storage import StorageLayer

    data_dir = migrate_data_dir(settings)
    storage = StorageLayer.default(data_dir)
    return Pipeline.from_storage(get_provider(), storage)


def _humanize_bytes(n: int) -> str:
    """`12 MB` / `842 KB` / `7 B`. Three significant figures max."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{n} {unit}"
            return f"{n:.0f} {unit}" if n >= 10 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.0f} TB"


def _render_data_dir_conflict(exc) -> str:
    """Turn a DataDirConflict into the redesigned notice."""
    legacy_size = _humanize_bytes(exc.legacy_size)
    xdg_size = _humanize_bytes(exc.xdg_size)
    legacy_str = str(exc.legacy).replace(str(Path.home()), "~")
    xdg_str = str(exc.xdg).replace(str(Path.home()), "~")

    # Pad paths to the same column so the sizes line up
    path_w = max(len(legacy_str), len(xdg_str))

    rows = [
        f"  {style.dim('legacy')}  {legacy_str:<{path_w}}  {legacy_size:>8}",
        f"  {style.dim('xdg')}     {xdg_str:<{path_w}}  {xdg_size:>8}",
    ]
    return style.notice(
        "two data directories hold data. merge manually before continuing.",
        *rows,
        "",
        "to merge legacy into xdg:",
        f"  {style.dim('$')} rsync -a {legacy_str}/ {xdg_str}/",
        f"  {style.dim('$')} rm -rf {legacy_str}",
        "",
        "or keep one, remove the other, and run et sync again.",
        tone="warn",
    )


# ── Commands ─────────────────────────────────────────────────────────

def cmd_insight(force: bool = False) -> int:
    from extended_thinking.mcp_server import _render_insight
    pipeline = _get_pipeline()

    print(style.header("insight"))

    sync_result = asyncio.run(pipeline.sync())
    insight = asyncio.run(pipeline.get_insight())

    if insight["type"] == "nothing_new" and force:
        wisdom = asyncio.run(pipeline.generate_wisdom(force=True))
        if wisdom:
            insight = {"type": "wisdom"}

    concepts = pipeline.store.list_concepts(order_by="frequency", limit=50)
    wisdoms = pipeline.store.list_wisdoms(limit=1)

    print()
    if wisdoms:
        print(_render_insight(wisdoms[0], concepts))
    else:
        title = insight.get("insight", {}).get("title", "no insight available")
        print(f"  {title}")
    return 0


def cmd_concepts(limit: int = 20) -> int:
    from extended_thinking.mcp_server import _render_concepts
    pipeline = _get_pipeline()
    concepts = pipeline.store.list_concepts(order_by="frequency", limit=limit)

    print(style.header("concepts", right=f"top {len(concepts)}"))
    print()
    print(_render_concepts(concepts))
    return 0


class _SyncReporter:
    """Phase-boundary reporter for `Pipeline.sync(on_progress=...)`.

    Each call prints one line: `·  phase-label           detail   1.4s`.
    Lines are flushed as they arrive so the user sees stages narrate in
    real time. No cursor tricks, no in-place redraws — Unix semantics,
    one completed event per line.

    Labels are deliberately human: `reading provider`, not `read`. The
    pipeline emits the short phase-id; the reporter provides the copy.
    """

    _LABELS = {
        "read":    "reading provider",
        "filter":  "filtering content",
        "index":   "indexing vectors",
        "extract": "extracting concepts",
        "resolve": "resolving entities",
        "relate":  "detecting relationships",
        "enrich":  "enriching with knowledge",
    }

    def __init__(self):
        import time
        self._time = time
        self._t_start = time.monotonic()
        self._t_phase = self._t_start
        self._label_w = max(len(v) for v in self._LABELS.values())

    def __call__(self, phase: str, detail: str) -> None:
        now = self._time.monotonic()
        elapsed = now - self._t_phase
        self._t_phase = now
        label = self._LABELS.get(phase, phase)
        elapsed_str = f"{elapsed:>5.1f}s"
        print(
            f"  {style.dim('·')}  "
            f"{label:<{self._label_w + 2}}"
            f"{detail:<42}"
            f"{style.dim(elapsed_str)}"
        )

    def total(self) -> float:
        return self._time.monotonic() - self._t_start


def cmd_sync() -> int:
    pipeline = _get_pipeline()

    provider = pipeline.provider if hasattr(pipeline, "provider") else None
    provider_name = getattr(provider, "name", "?") if provider else "?"

    print(style.header("sync", right=provider_name))
    print()

    reporter = _SyncReporter()
    result = asyncio.run(pipeline.sync(on_progress=reporter))
    total_time = reporter.total()
    total = pipeline.store.get_stats()["total_concepts"]

    delta = result["concepts_extracted"]
    chunks = result["chunks_processed"]
    status = result.get("status", "synced")

    print()
    print(style.rule())
    print()
    grid_rows = [
        [("chunks", str(chunks)), ("concepts", f"{total:,}")],
        [("+ concepts", f"+{delta}"), ("status", status)],
    ]
    enrichment = result.get("enrichment")
    if enrichment:
        grid_rows.append([
            ("+ enriched", str(enrichment.get("knowledge_nodes_created", 0))),
            ("runs", str(enrichment.get("runs_recorded", 0))),
        ])
    grid_rows.append([("elapsed", f"{total_time:.1f}s"), ("", "")])
    print(style.grid(grid_rows))
    return 0


def cmd_stats() -> int:
    pipeline = _get_pipeline()
    stats = pipeline.get_stats()
    p = stats["provider"]
    c = stats["concepts"]

    provider_name = p.get("detected_provider", p.get("provider", "?"))
    print(style.header("stats", right=provider_name))
    print()

    grid_rows = [
        [("memories", f"{p.get('total_memories', 0):,}"),
         ("concepts", f"{c['total_concepts']:,}")],
        [("relationships", f"{c['total_relationships']:,}"),
         ("wisdoms", f"{c['total_wisdoms']:,}")],
    ]
    print(style.grid(grid_rows))
    return 0


def cmd_mcp_serve() -> int:
    """Run the MCP server. Usually invoked by a client, not directly by humans."""
    from extended_thinking.mcp_server import run_mcp_server
    run_mcp_server()
    return 0


# ── et reset ─────────────────────────────────────────────────────────
# Nukes every trace of ET on the machine. Dry-run by default — actual
# deletion requires --go-home (the intent is "you're going home; start
# over"). No partial modes: all three locations go together or the
# command does nothing. Anything subtler is a config edit, not a reset.

def _reset_targets() -> list[tuple[str, Path]]:
    """The three locations ET occupies on disk."""
    from extended_thinking.config import settings
    from extended_thinking.config.paths import LEGACY_DATA_DIR, user_config_dir

    return [
        ("data", settings.data.root),
        ("config", user_config_dir()),
        ("legacy", LEGACY_DATA_DIR),
    ]


def cmd_reset(go_home: bool = False) -> int:
    """`et reset` — wipe all ET state. Dry-run unless --go-home is set."""
    from extended_thinking.config.migrate import _dir_size

    targets = _reset_targets()
    present = [(label, path, _dir_size(path)) for label, path, *_ in
               [(lbl, p) for lbl, p in targets] if path.exists()]

    mode = "going home" if go_home else "dry-run"
    print(style.header("reset", right=mode))
    print()

    if not present:
        print(style.notice(
            "nothing to reset — no ET state found on this machine.",
            tone="warn",
        ))
        return 0

    if not go_home:
        # Preview: list what would be deleted, with sizes.
        rows = []
        total = 0
        path_w = max(len(str(p).replace(str(Path.home()), "~")) for _, p, _ in present)
        for label, path, size in present:
            path_str = str(path).replace(str(Path.home()), "~")
            total += size
            rows.append(
                f"  {style.dim(label):<14}  {path_str:<{path_w}}  {_humanize_bytes(size):>10}"
            )

        print(style.notice(
            "about to remove every trace of ET on this machine.",
            *rows,
            "",
            f"  {style.dim('total')}         {_humanize_bytes(total):>10}  across {len(present)} location{'s' if len(present) != 1 else ''}.",
            "",
            f"run {style.accent('et reset --go-home')} to actually do it.",
            tone="warn",
        ))
        return 0

    # Real deletion.
    failures: list[str] = []
    for label, path, size in present:
        try:
            shutil.rmtree(path)
            print(f"  {style.row('ok', [f'{label:<10}  removed  ({_humanize_bytes(size)})'])}")
        except OSError as e:
            failures.append(f"{label}: {e}")
            print(f"  {style.row('fail', [f'{label:<10}  {e}'])}")

    print()
    if failures:
        print(style.hint(f"  {len(failures)} location{'s' if len(failures) != 1 else ''} could not be removed; see above"))
        return 1

    print(style.hint("  clean slate. run et sync to start over."))
    return 0


# ── et init ──────────────────────────────────────────────────────────────────

MCP_SERVER_KEY = "extended-thinking"


def _mcp_entry() -> dict:
    """The MCP server entry to register. Uses sys.executable so it works across
    pip, pipx, and editable installs without relying on PATH resolution at spawn time."""
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "extended_thinking.mcp_server"],
        "env": {},
    }


def _client_configs() -> list[tuple[str, Path]]:
    """Known MCP-client config locations on this machine. Returns (name, path) pairs."""
    home = Path.home()
    return [
        ("Claude Code", home / ".claude.json"),
        ("Claude Desktop", home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"),
        ("opencode", home / ".config" / "opencode" / "config.json"),
        ("Codex CLI", home / ".codex" / "config.json"),
    ]


def _backup(path: Path) -> Path:
    """Timestamped backup sibling. Returns the backup path."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak-{ts}")
    shutil.copy2(path, bak)
    return bak


def _patch_client(name: str, path: Path, dry_run: bool = False) -> tuple[str, str]:
    """Register ET in one client's config. Returns (status, detail) for a row()."""
    if not path.exists():
        return ("pending", f"{name:<15} config not found")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return ("fail", f"{name:<15} invalid JSON ({e})")
    except OSError as e:
        return ("fail", f"{name:<15} read failed ({e})")

    mcp = data.setdefault("mcpServers", {})
    entry = _mcp_entry()
    existing = mcp.get(MCP_SERVER_KEY)

    if existing == entry:
        return ("ok", f"{name:<15} already registered")

    action = "update" if existing else "add"
    if dry_run:
        return ("pending", f"{name:<15} would {action}")

    bak = _backup(path)
    mcp[MCP_SERVER_KEY] = entry
    path.write_text(json.dumps(data, indent=2) + "\n")
    return ("ok", f"{name:<15} {action} ({bak.name})")


def cmd_init(dry_run: bool = False) -> int:
    right = "dry-run" if dry_run else None
    print(style.header("init", right=right))
    print(style.subtitle(f"  register {MCP_SERVER_KEY!r} with local MCP clients"))
    print()

    for name, path in _client_configs():
        status, detail = _patch_client(name, path, dry_run=dry_run)
        print(f"  {style.row(status, [detail])}")

    print()
    print(style.hint("  restart the client to pick up the new MCP server"))
    if dry_run:
        print(style.hint("  (dry-run: no files were modified)"))
    return 0


# ── entry point ──────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="et", description="Extended-thinking CLI.")
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("insight", help="sync + generate wisdom").add_argument("--force", action="store_true")
    sub.add_parser("concepts", help="list concepts").add_argument("--limit", type=int, default=20)
    sub.add_parser("sync", help="pull from provider")
    sub.add_parser("stats", help="show stats")
    sub.add_parser("mcp-serve", help="run the MCP server (for clients)")

    p_init = sub.add_parser("init", help="register ET as an MCP server with CC / Claude Desktop")
    p_init.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")

    p_reset = sub.add_parser("reset", help="wipe all ET state (dry-run unless --go-home)")
    p_reset.add_argument("--go-home", action="store_true",
                         help="actually delete; without this flag, reset previews only")

    # `et config ...` — ADR 012
    p_cfg = sub.add_parser("config", help="inspect or edit ET configuration")
    cfg_sub = p_cfg.add_subparsers(dest="config_cmd", required=True)
    p_cfg_init = cfg_sub.add_parser("init", help="scaffold config.toml and secrets.toml under ~/.config/extended-thinking")
    p_cfg_init.add_argument("--force", action="store_true", help="overwrite existing files")
    cfg_sub.add_parser("path", help="print resolved config file paths")
    p_cfg_show = cfg_sub.add_parser("show", help="print resolved effective config")
    p_cfg_show.add_argument("--format", choices=["toml", "json"], default="toml")
    p_cfg_show.add_argument("--show-secrets", action="store_true", help="do not redact credential values")
    cfg_sub.add_parser("validate", help="load + validate config, exit nonzero on error")

    p_cfg_get = cfg_sub.add_parser("get", help="read a single config value (dotted path)")
    p_cfg_get.add_argument("key", help="e.g. extraction.model, algorithms.decay.physarum.decay_rate")

    p_cfg_set = cfg_sub.add_parser("set", help="write a single config value")
    p_cfg_set.add_argument("key", help="dotted path")
    p_cfg_set.add_argument("value", help="value (bool/int/float/list via commas/string)")
    p_cfg_set.add_argument("--scope", choices=["user", "project", "secrets"], default="user")

    p_cfg_edit = cfg_sub.add_parser("edit", help="open config in $EDITOR")
    p_cfg_edit.add_argument("--scope", choices=["user", "project", "secrets"], default="user")

    return parser


def _dispatch(args) -> int:
    if args.cmd == "insight":
        return cmd_insight(force=args.force)
    if args.cmd == "concepts":
        return cmd_concepts(limit=args.limit)
    if args.cmd == "sync":
        return cmd_sync()
    if args.cmd == "stats":
        return cmd_stats()
    if args.cmd == "init":
        return cmd_init(dry_run=args.dry_run)
    if args.cmd == "reset":
        return cmd_reset(go_home=args.go_home)
    if args.cmd == "mcp-serve":
        return cmd_mcp_serve()
    if args.cmd == "config":
        from extended_thinking.config.commands import (
            cmd_config_edit,
            cmd_config_get,
            cmd_config_init,
            cmd_config_path,
            cmd_config_set,
            cmd_config_show,
            cmd_config_validate,
        )
        if args.config_cmd == "init":
            return cmd_config_init(force=args.force)
        if args.config_cmd == "path":
            return cmd_config_path()
        if args.config_cmd == "show":
            return cmd_config_show(format=args.format, show_secrets=args.show_secrets)
        if args.config_cmd == "validate":
            return cmd_config_validate()
        if args.config_cmd == "get":
            return cmd_config_get(args.key)
        if args.config_cmd == "set":
            return cmd_config_set(args.key, args.value, scope=args.scope)
        if args.config_cmd == "edit":
            return cmd_config_edit(scope=args.scope)
    return 1


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return 1

    # Single place to catch expected, renderable error states. Anything
    # else bubbles up as a Python traceback (bug, not a UX concern).
    try:
        return _dispatch(args)
    except Exception as exc:
        from extended_thinking.config.migrate import DataDirConflict
        if isinstance(exc, DataDirConflict):
            print(_render_data_dir_conflict(exc), file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
