"""`et doctor` — top-to-bottom health check.

Each check yields a (label, status, detail, hint) tuple. Statuses:
  ok       green ✓, all good
  warn     yellow !, works but you might want to fix it
  fail     red ✗, something is broken
  skip     dim ·, not applicable on this machine

Exit code:
  0 if every check is ok or skip
  1 if any check is warn (works but suboptimal)
  2 if any check is fail (something is broken)

The output deliberately reuses cli_style so doctor feels like the rest
of the CLI (mascot signatures, notice tones, dim hints) instead of a
generic linter dump.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from extended_thinking import cli_style as style

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass
class Check:
    label: str
    status: Status
    detail: str = ""
    hint: str | None = None


# ── Individual checks ─────────────────────────────────────────────────


def _check_python() -> Check:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 12):
        return Check("python version", "ok", f"{major}.{minor}.{sys.version_info[2]}")
    return Check(
        "python version",
        "fail",
        f"{major}.{minor} (need 3.12+)",
        hint="install Python 3.12 or later",
    )


def _check_anthropic_key() -> Check:
    from extended_thinking.config import settings

    key = settings.credentials.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if key and key.startswith("sk-"):
        return Check("anthropic api key", "ok", f"...{key[-6:]}")
    if key:
        return Check(
            "anthropic api key",
            "warn",
            "set but does not look like a sk-... key",
            hint="double-check the value in secrets.toml or env",
        )
    return Check(
        "anthropic api key",
        "fail",
        "not set",
        hint="export ANTHROPIC_API_KEY=sk-... or `et config set credentials.anthropic_api_key=... --scope secrets`",
    )


def _check_openai_key() -> Check:
    from extended_thinking.config import settings

    key = settings.credentials.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
    if key:
        return Check("openai api key", "ok", "(optional, used as fallback)")
    return Check(
        "openai api key",
        "skip",
        "not set (optional)",
        hint=None,
    )


def _check_config_file() -> Check:
    from extended_thinking.config.paths import user_config_dir

    cfg = user_config_dir() / "config.toml"
    if not cfg.exists():
        return Check(
            "user config file",
            "warn",
            f"missing at {_short(cfg)}",
            hint="run `et config init` to scaffold one (defaults work without it)",
        )
    try:
        from extended_thinking.config import settings  # noqa: F401
        return Check("user config file", "ok", _short(cfg))
    except Exception as e:
        return Check(
            "user config file",
            "fail",
            f"present but invalid: {e}",
            hint="check `et config validate`",
        )


def _check_data_dir() -> Check:
    from extended_thinking.config import settings

    root = settings.data.root
    try:
        root.mkdir(parents=True, exist_ok=True)
        # writable test
        probe = root / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
        return Check("data dir writable", "ok", _short(root))
    except OSError as e:
        return Check(
            "data dir writable",
            "fail",
            f"{_short(root)}: {e}",
            hint="check filesystem permissions",
        )


def _check_kuzu_chroma() -> Check:
    try:
        import kuzu  # noqa: F401
        try:
            import chromadb  # noqa: F401
            return Check("kuzu + chromadb importable", "ok", "both available")
        except ImportError:
            return Check(
                "kuzu + chromadb importable",
                "warn",
                "kuzu ok, chromadb missing (optional vector store)",
                hint="pip install 'extended-thinking[search]' if you want vectors",
            )
    except ImportError as e:
        return Check(
            "kuzu + chromadb importable",
            "fail",
            f"kuzu missing: {e}",
            hint="pip install kuzu",
        )


def _check_providers_detected() -> Check:
    from extended_thinking.providers.auto import AutoProvider

    try:
        provider = AutoProvider()
        sub = getattr(provider, "_providers", [])
        names = [p.name for p in sub]
        if not names:
            return Check(
                "memory providers detected",
                "warn",
                "none found on this machine",
                hint="run `et wizard` to configure one",
            )
        return Check("memory providers detected", "ok", ", ".join(names))
    except Exception as e:
        return Check(
            "memory providers detected",
            "fail",
            f"AutoProvider raised: {e}",
            hint="check `et config show` for provider settings",
        )


def _check_mcp_registered() -> Check:
    """Look for the extended-thinking MCP server in known client configs."""
    import json
    from extended_thinking.cli import _client_configs, MCP_SERVER_KEY

    registered: list[str] = []
    found_any_client = False
    for name, path in _client_configs():
        if not path.exists():
            continue
        found_any_client = True
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if MCP_SERVER_KEY in data.get("mcpServers", {}):
            registered.append(name)
    if not found_any_client:
        return Check(
            "registered as MCP server",
            "skip",
            "no MCP clients detected on this machine",
        )
    if not registered:
        return Check(
            "registered as MCP server",
            "warn",
            "found clients but ET is not registered with any",
            hint="run `et init` (or `et wizard`)",
        )
    return Check("registered as MCP server", "ok", ", ".join(registered))


# ── Driver ────────────────────────────────────────────────────────────


def _all_checks() -> Iterator[Check]:
    yield _check_python()
    yield _check_anthropic_key()
    yield _check_openai_key()
    yield _check_config_file()
    yield _check_data_dir()
    yield _check_kuzu_chroma()
    yield _check_providers_detected()
    yield _check_mcp_registered()


def cmd_doctor(quiet: bool = False) -> int:
    print(style.header("doctor"))
    print(style.subtitle("  is everything wired up?"))
    print()

    results: list[Check] = []
    label_w = 32
    for check in _all_checks():
        results.append(check)
        if not quiet:
            print(f"  {_status_glyph(check.status)}  {check.label:<{label_w}}  {style.dim(check.detail)}")
            if check.hint and check.status in ("warn", "fail"):
                print(f"     {style.dim('→')} {style.dim(check.hint)}")

    fails = sum(1 for c in results if c.status == "fail")
    warns = sum(1 for c in results if c.status == "warn")
    oks = sum(1 for c in results if c.status == "ok")

    if not quiet:
        print()
        print(style.rule())
        print()

    if fails:
        print(style.signature(
            "wink_l", "rest",
            note=f"{fails} check{'s' if fails != 1 else ''} failed, {warns} warning{'s' if warns != 1 else ''}, {oks} ok.",
        ))
        return 2
    if warns:
        print(style.signature(
            "blink", "spark", glowing=True,
            note=f"{warns} warning{'s' if warns != 1 else ''}, {oks} ok.",
        ))
        return 1
    print(style.signature(
        "open", "lit", glowing=True,
        note=f"all {oks} checks passed.",
    ))
    return 0


# ── Helpers ───────────────────────────────────────────────────────────


def _status_glyph(status: Status) -> str:
    if status == "ok":
        return style.ok_tone("✓")
    if status == "warn":
        return style.warn_tone("!")
    if status == "fail":
        return style.err_tone("✗")
    return style.dim("·")


def _short(p: Path) -> str:
    return str(p).replace(str(Path.home()), "~")
