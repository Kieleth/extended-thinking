"""`et wizard` — interactive first-run setup.

Walks a user through:
  1. Welcome banner
  2. Provider check (which memory sources are detected, which to use)
  3. API key check (anthropic mandatory, openai optional)
  4. MCP registration (which clients to wire up)
  5. Optional first sync
  6. Done banner with next steps

Each step is independently skippable. The wizard is safe to re-run.
With `--dry-run`, it walks through the prompts and shows what it WOULD
do, but writes nothing.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import questionary

from extended_thinking import cli_style as style


def _short(p: Path) -> str:
    return str(p).replace(str(Path.home()), "~")


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def cmd_wizard(dry_run: bool = False) -> int:
    if not _is_interactive():
        print(style.notice(
            "wizard needs an interactive terminal.",
            "rerun from a real shell, not a pipe or CI step.",
            tone="warn",
        ))
        return 1

    right = "dry-run" if dry_run else None
    print(style.header("wizard", right=right))
    print(style.subtitle("  let's get you set up."))
    print()

    # ── Step 1: providers ─────────────────────────────────────────────
    detected = _detect_providers()
    if detected:
        print(style.dim(f"  detected: {', '.join(detected)}"))
    else:
        print(style.dim("  no memory providers detected on this machine."))
    print()

    chosen_providers = questionary.checkbox(
        "Which memory providers should ET read from?",
        choices=_provider_choices(detected),
    ).ask()
    if chosen_providers is None:  # user hit Ctrl-C
        return _abort()

    # ── Step 2: API key ───────────────────────────────────────────────
    print()
    key_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if key_ok:
        print(style.ok_tone("  ✓") + style.dim("  ANTHROPIC_API_KEY found in environment"))
    else:
        print(style.warn_tone("  !") + style.dim("  ANTHROPIC_API_KEY not set"))
        new_key = questionary.password(
            "Paste your Anthropic API key now? (leave blank to set later)",
        ).ask()
        if new_key:
            if dry_run:
                print(style.dim(f"    dry-run: would write to secrets.toml (...{new_key[-6:]})"))
            else:
                _write_secret_key("anthropic_api_key", new_key)
                print(style.ok_tone("    ✓") + style.dim(f"  saved to secrets.toml (...{new_key[-6:]})"))
            key_ok = True

    # ── Step 3: MCP registration ──────────────────────────────────────
    print()
    from extended_thinking.cli import _client_configs

    available_clients = [(name, path) for name, path in _client_configs() if path.exists()]
    if not available_clients:
        print(style.dim("  no MCP clients detected (Claude Code, Claude Desktop, opencode, codex)."))
        print(style.dim("  install one and run `et init` later."))
        chosen_clients = []
    else:
        print(style.dim(f"  found clients: {', '.join(n for n, _ in available_clients)}"))
        register = questionary.confirm(
            "Register ET as an MCP server with these clients?",
            default=True,
        ).ask()
        if register is None:
            return _abort()
        chosen_clients = [n for n, _ in available_clients] if register else []

    if chosen_clients:
        if dry_run:
            print(style.dim(f"    dry-run: would patch {', '.join(chosen_clients)}"))
        else:
            _run_init()

    # ── Step 4: optional first sync ───────────────────────────────────
    print()
    if chosen_providers and key_ok:
        do_sync = questionary.confirm(
            "Run an initial sync now? (extracts concepts via Haiku — costs a few cents)",
            default=False,
        ).ask()
        if do_sync is None:
            return _abort()
        if do_sync:
            if dry_run:
                print(style.dim("    dry-run: would run `et sync`"))
            else:
                _run_sync()
    else:
        if not chosen_providers:
            print(style.dim("  no providers chosen — skipping initial sync."))
        elif not key_ok:
            print(style.dim("  no API key — skipping initial sync."))

    # ── Done banner ───────────────────────────────────────────────────
    print()
    print(style.rule())
    print()
    next_steps = []
    if not key_ok:
        next_steps.append("set ANTHROPIC_API_KEY (then `et doctor` to verify)")
    if chosen_providers and key_ok:
        next_steps.append("`et sync` to pull recent memories")
        next_steps.append("`et insight` to synthesize wisdom")
    if not chosen_clients and available_clients:
        next_steps.append("`et init` later to wire up MCP clients")
    next_steps.append("`et doctor` anytime to recheck health")

    for step in next_steps:
        print(f"  {style.dim('→')} {step}")
    print()
    print(style.signature("open", "lit", glowing=True, note="set up. happy thinking."))
    return 0


# ── Helpers ───────────────────────────────────────────────────────────


def _detect_providers() -> list[str]:
    """Return the names of MemoryProviders AutoProvider would pick up."""
    try:
        from extended_thinking.providers.auto import AutoProvider
        return [p.name for p in AutoProvider()._providers]
    except Exception:
        return []


def _provider_choices(detected: list[str]) -> list[questionary.Choice]:
    """Build the multi-select choices, pre-checking detected ones."""
    all_known = ["claude-code", "folder", "mempalace", "chatgpt-export",
                 "copilot-chat", "cursor", "generic-openai-chat"]
    seen = set(detected)
    return [
        questionary.Choice(
            title=name + ("  (detected)" if name in seen else ""),
            value=name,
            checked=name in seen,
        )
        for name in all_known
    ]


def _write_secret_key(key: str, value: str) -> None:
    """Append a single key under [credentials] in secrets.toml."""
    from extended_thinking.config.paths import user_config_dir

    secrets_path = user_config_dir() / "secrets.toml"
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    if secrets_path.exists():
        text = secrets_path.read_text()
        if "[credentials]" not in text:
            text = "[credentials]\n" + text
        if f"{key} =" in text:
            # Replace existing line
            lines = text.splitlines()
            lines = [
                f'{key} = "{value}"' if line.startswith(f"{key} =") else line
                for line in lines
            ]
            text = "\n".join(lines) + "\n"
        else:
            text = text.rstrip() + f'\n{key} = "{value}"\n'
    else:
        text = f'[credentials]\n{key} = "{value}"\n'
    secrets_path.write_text(text)
    secrets_path.chmod(0o600)


def _run_init() -> None:
    """Invoke `et init` (no dry-run)."""
    from extended_thinking.cli import cmd_init
    print()
    cmd_init(dry_run=False)


def _run_sync() -> None:
    """Invoke `et sync` non-interactively (skip the source picker)."""
    from extended_thinking.cli import cmd_sync
    print()
    cmd_sync(yes=True)


def _abort() -> int:
    print()
    print(style.signature("blink", "rest", note="cancelled. nothing changed."))
    return 1
