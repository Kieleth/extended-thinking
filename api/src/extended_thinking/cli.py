#!/usr/bin/env python3
"""Extended-thinking CLI.

Usage:
  et insight            # sync + generate wisdom
  et concepts           # list concepts
  et sync               # pull from provider
  et stats              # show stats
  et init               # register ET as an MCP server with CC / Claude Desktop
  et mcp-serve          # run the MCP server (usually invoked by a client, not humans)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _get_pipeline():
    from extended_thinking.config import migrate_data_dir, settings
    from extended_thinking.processing.pipeline_v2 import Pipeline
    from extended_thinking.providers import get_provider
    from extended_thinking.storage import StorageLayer

    data_dir = migrate_data_dir(settings)
    storage = StorageLayer.default(data_dir)
    return Pipeline.from_storage(get_provider(), storage)


def cmd_insight(force: bool = False) -> int:
    from extended_thinking.mcp_server import _render_insight
    pipeline = _get_pipeline()

    print("Syncing...", end="", flush=True)
    sync_result = asyncio.run(pipeline.sync())
    print(f" {sync_result['concepts_extracted']} new concepts")

    print("Thinking...", end="", flush=True)
    insight = asyncio.run(pipeline.get_insight())

    if insight["type"] == "nothing_new" and force:
        wisdom = asyncio.run(pipeline.generate_wisdom(force=True))
        if wisdom:
            insight = {"type": "wisdom"}

    concepts = pipeline.store.list_concepts(order_by="frequency", limit=50)
    wisdoms = pipeline.store.list_wisdoms(limit=1)

    if wisdoms:
        print("\r" + " " * 20 + "\r", end="")
        print(_render_insight(wisdoms[0], concepts))
    else:
        print(f"\n{insight.get('insight', {}).get('title', 'No insight available')}")
    return 0


def cmd_concepts(limit: int = 20) -> int:
    from extended_thinking.mcp_server import _render_concepts
    pipeline = _get_pipeline()
    concepts = pipeline.store.list_concepts(order_by="frequency", limit=limit)
    print(_render_concepts(concepts))
    return 0


def cmd_sync() -> int:
    pipeline = _get_pipeline()
    result = asyncio.run(pipeline.sync())
    total = pipeline.store.get_stats()["total_concepts"]
    print(f"Synced: {result['chunks_processed']} chunks, +{result['concepts_extracted']} concepts. Total: {total}")
    return 0


def cmd_stats() -> int:
    pipeline = _get_pipeline()
    stats = pipeline.get_stats()
    p = stats["provider"]
    c = stats["concepts"]
    print(f"Provider: {p.get('detected_provider', p.get('provider', '?'))}")
    print(f"Memories: {p.get('total_memories', 0)}")
    print(f"Concepts: {c['total_concepts']}")
    print(f"Relationships: {c['total_relationships']}")
    print(f"Wisdoms: {c['total_wisdoms']}")
    return 0


def cmd_mcp_serve() -> int:
    """Run the MCP server. Usually invoked by a client, not directly by humans."""
    from extended_thinking.mcp_server import run_mcp_server
    run_mcp_server()
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


def _patch_client(name: str, path: Path, dry_run: bool = False) -> str:
    """Register ET in one client's config. Returns a one-line status."""
    if not path.exists():
        return f"  skip    {name:<15} (config not found at {path})"

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return f"  ERROR   {name:<15} (invalid JSON: {e})"
    except OSError as e:
        return f"  ERROR   {name:<15} (read failed: {e})"

    mcp = data.setdefault("mcpServers", {})
    entry = _mcp_entry()
    existing = mcp.get(MCP_SERVER_KEY)

    if existing == entry:
        return f"  ok      {name:<15} (already registered, no change)"

    action = "update" if existing else "add"
    if dry_run:
        return f"  dry-run {name:<15} (would {action}: {path})"

    bak = _backup(path)
    mcp[MCP_SERVER_KEY] = entry
    path.write_text(json.dumps(data, indent=2) + "\n")
    return f"  {action:<7} {name:<15} ({path}, backup: {bak.name})"


def cmd_init(dry_run: bool = False) -> int:
    print(f"Registering '{MCP_SERVER_KEY}' MCP server:")
    print(f"  command: {sys.executable} -m extended_thinking.mcp_server\n")

    for name, path in _client_configs():
        print(_patch_client(name, path, dry_run=dry_run))

    print("\nRestart the client to pick up the new MCP server.")
    if dry_run:
        print("(dry-run: no files were modified)")
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


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return 1

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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
