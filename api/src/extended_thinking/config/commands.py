"""`et config ...` subcommand implementations (ADR 012 step 2).

Mirrors git-config ergonomics. Reads via load_settings() so every command
sees the fully resolved config, not just one tier.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from extended_thinking.config import (
    load_settings,
    user_config_dir,
    user_config_path,
    user_dropin_dir,
    user_secrets_path,
)
from extended_thinking.config.loader import find_project_config


DEFAULT_CONFIG_TEMPLATE = """\
# extended-thinking config
# See docs/ADR/012-centralized-config.md for the full schema.
# Every key below shows the built-in default; uncomment to override.

[data]
# root = "~/.local/share/extended-thinking"

[providers.claude_code]
enabled = true
# projects_dir = "~/.claude/projects"

[providers.chatgpt_export]
enabled = true
# scan_paths = ["~/Downloads", "~/Documents"]

[providers.copilot_chat]
enabled = true

[providers.cursor]
enabled = true

[providers.folder]
enabled = true
# paths = []

[providers.generic_openai_chat]
enabled = true

[providers.mempalace]
enabled = true

[extraction]
# provider = ""   # empty = auto-detect from available API keys
# model = "claude-haiku-4-5-20251001"

[wisdom]
# provider = ""
# model = "claude-opus-4-6"

[server]
# cors_origins = "http://localhost:3000"

# [algorithms.*] tables hold per-plugin parameters.
# Each plugin documents its own knobs; see `et catalog` once it's live.
#
# Example — tighten Physarum decay and turn off embedding-based entity resolution:
# [algorithms.decay.physarum]
# active = true
# decay_rate = 0.92
# source_age_aware = true
#
# [algorithms.resolution]
# order = ["sequence_matcher"]   # drops embedding_cosine
"""


SECRETS_TEMPLATE = """\
# extended-thinking secrets
# This file should be chmod 600 and gitignored.
# Anything here is merged into the [credentials] table at load time.

[credentials]
# anthropic_api_key = ""
# openai_api_key = ""
"""


# ── init ─────────────────────────────────────────────────────────────

def cmd_config_init(force: bool = False) -> int:
    """Scaffold config.toml and secrets.toml in the user's XDG config dir."""
    cfg_dir = user_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = user_config_path()
    sec_path = user_secrets_path()
    dropin_dir = user_dropin_dir()
    dropin_dir.mkdir(parents=True, exist_ok=True)

    wrote_any = False

    if cfg_path.exists() and not force:
        print(f"skip  {cfg_path} (exists; pass --force to overwrite)")
    else:
        cfg_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        print(f"wrote {cfg_path}")
        wrote_any = True

    if sec_path.exists() and not force:
        print(f"skip  {sec_path} (exists; pass --force to overwrite)")
    else:
        sec_path.write_text(SECRETS_TEMPLATE, encoding="utf-8")
        try:
            sec_path.chmod(0o600)
        except OSError as e:
            # Non-POSIX or restricted FS. Best-effort: tell the user.
            print(f"warn  could not chmod 600 on {sec_path}: {e}")
        print(f"wrote {sec_path} (mode 600)")
        wrote_any = True

    print(f"drop-ins dir: {dropin_dir} (empty)")
    if wrote_any:
        print()
        print("Edit with:  et config edit")
        print("Inspect:    et config show")
    return 0


# ── path ─────────────────────────────────────────────────────────────

def cmd_config_path() -> int:
    """Print every config source ET would consult, marking which exist."""
    entries: list[tuple[str, Path | None]] = [
        ("user config", user_config_path()),
        ("drop-ins dir", user_dropin_dir()),
        ("project config", find_project_config()),
        ("secrets", user_secrets_path()),
    ]
    width = max(len(label) for label, _ in entries)
    for label, p in entries:
        if p is None:
            print(f"{label:<{width}}  (none found)")
        else:
            marker = "✓" if p.exists() else "·"
            print(f"{label:<{width}}  {marker}  {p}")
    return 0


# ── show ─────────────────────────────────────────────────────────────

def _settings_to_dict(settings) -> dict:
    """model_dump with Path coerced to string for readable output."""
    def _coerce(v: Any):
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, dict):
            return {k: _coerce(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_coerce(x) for x in v]
        return v
    return _coerce(settings.model_dump())


def cmd_config_show(format: str = "toml", show_secrets: bool = False) -> int:
    """Print the fully resolved effective configuration."""
    s = load_settings()
    data = _settings_to_dict(s)

    if not show_secrets:
        # Redact credential values but keep the structure visible
        if "credentials" in data:
            data["credentials"] = {
                k: ("***" if v else "") for k, v in data["credentials"].items()
            }

    if format == "json":
        print(json.dumps(data, indent=2))
        return 0

    # Default: minimal TOML-ish rendering (nested tables, scalars, lists).
    # We avoid a tomli_w dependency; this output is read-only anyway.
    print(_render_toml(data))
    return 0


def _render_toml(data: dict, prefix: str = "") -> str:
    """Pretty-print a dict as TOML. Not a full serializer — good enough for
    human inspection of the resolved config."""
    scalar_lines: list[str] = []
    table_blocks: list[str] = []
    for k, v in data.items():
        if isinstance(v, dict):
            sub_prefix = f"{prefix}.{k}" if prefix else k
            block = f"\n[{sub_prefix}]\n{_render_toml(v, sub_prefix)}"
            table_blocks.append(block.rstrip() + "\n")
        else:
            scalar_lines.append(f"{k} = {_toml_scalar(v)}")
    out = "\n".join(scalar_lines)
    if table_blocks:
        out = (out + "\n" if out else "") + "".join(table_blocks)
    return out


def _toml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    return f'"{v}"'


# ── validate ─────────────────────────────────────────────────────────

def cmd_config_validate() -> int:
    """Load and validate the config. Exit 0 on success, 2 on error."""
    try:
        load_settings()
    except ValidationError as e:
        print("config validation failed:", file=sys.stderr)
        print(e, file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2
    print("config ok")
    return 0


# ── get / set / edit ─────────────────────────────────────────────────
#
# Dotted-path conventions match `et config show`'s output structure:
#   extraction.model
#   algorithms.decay.physarum.decay_rate
# Paths under `[algorithms.*]` are passed through unchanged (free-form tree);
# paths under typed sections must exist in the schema.

def _walk_dotted(d: dict, path: list[str]) -> Any:
    cur: Any = d
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(".".join(path))
        cur = cur[part]
    return cur


def _set_dotted(d: dict, path: list[str], value: Any) -> None:
    cur = d
    for part in path[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[path[-1]] = value


def _coerce_value(raw: str) -> Any:
    """Parse a CLI-provided value string into bool/int/float/list/string.

    Simple heuristics: "true"/"false" → bool; numeric string → int or float;
    comma-separated → list; anything else → string. For full TOML semantics
    users should `et config edit`.
    """
    lo = raw.lower()
    if lo == "true":
        return True
    if lo == "false":
        return False
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        # Not a number; fall through to list / string handling.
        ...
    if "," in raw:
        return [_coerce_value(x.strip()) for x in raw.split(",")]
    return raw


def cmd_config_get(key: str) -> int:
    """Print a single value from the resolved config. Exit 1 if missing."""
    s = load_settings()
    data = _settings_to_dict(s)
    parts = key.split(".")
    try:
        value = _walk_dotted(data, parts)
    except KeyError:
        print(f"no such key: {key}", file=sys.stderr)
        return 1
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2))
    else:
        print(value)
    return 0


def cmd_config_set(key: str, value: str, *, scope: str = "user") -> int:
    """Write `key = value` into the user (or project) TOML, creating it if
    needed.

    scope:
      "user"    — $XDG_CONFIG_HOME/extended-thinking/config.toml (default)
      "project" — ./et.toml in CWD (created if absent)
      "secrets" — $XDG_CONFIG_HOME/extended-thinking/secrets.toml (only for
                  keys under `credentials.*`)
    """
    parts = key.split(".")
    if scope == "secrets" and parts[0] != "credentials":
        print(f"scope=secrets only accepts credentials.* keys; got {key}",
              file=sys.stderr)
        return 2
    if scope != "secrets" and parts[0] == "credentials":
        print(f"credentials.* must be written with --scope secrets; "
              f"refusing to leak a secret into {scope} config",
              file=sys.stderr)
        return 2

    target = _scope_path(scope)
    target.parent.mkdir(parents=True, exist_ok=True)

    current = _read_toml_for_edit(target)
    _set_dotted(current, parts, _coerce_value(value))
    _write_toml(target, current)

    if scope == "secrets":
        try:
            target.chmod(0o600)
        except OSError as e:
            print(f"warn  could not chmod 600 on {target}: {e}",
                  file=sys.stderr)

    # Validate after writing so the user finds out now, not on next run.
    try:
        load_settings()
    except Exception as e:
        print(f"warn  value written, but config no longer validates: {e}",
              file=sys.stderr)
        return 1
    print(f"{target}: set {key} = {value}")
    return 0


def cmd_config_edit(*, scope: str = "user") -> int:
    """Open the config file in $EDITOR (or $VISUAL), then validate."""
    import os
    import subprocess

    target = _scope_path(scope)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Seed user config with the documented template so users don't
        # land in an empty file.
        if scope == "user":
            target.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        elif scope == "secrets":
            target.write_text(SECRETS_TEMPLATE, encoding="utf-8")
            try:
                target.chmod(0o600)
            except OSError as e:
                print(f"warn  could not chmod 600 on {target}: {e}",
                      file=sys.stderr)
        else:
            target.write_text("", encoding="utf-8")

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    rc = subprocess.call([*editor.split(), str(target)])
    if rc != 0:
        print(f"editor exited with status {rc}", file=sys.stderr)
        return rc

    try:
        load_settings()
    except Exception as e:
        print(f"warn  saved, but config no longer validates: {e}",
              file=sys.stderr)
        return 1
    print(f"ok  {target}")
    return 0


def _scope_path(scope: str) -> Path:
    from extended_thinking.config import user_config_path, user_secrets_path
    if scope == "user":
        return user_config_path()
    if scope == "project":
        return Path.cwd() / "et.toml"
    if scope == "secrets":
        return user_secrets_path()
    raise ValueError(f"unknown scope: {scope}")


def _read_toml_for_edit(path: Path) -> dict:
    """Load a TOML file for modification. Missing → empty dict."""
    import tomllib
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _write_toml(path: Path, data: dict) -> None:
    """Minimal TOML writer for `et config set`. Not a full serializer — it
    handles the scalar / list / nested-table cases we actually produce. For
    anything more intricate, users should `et config edit`.
    """
    path.write_text(_render_toml(data), encoding="utf-8")
