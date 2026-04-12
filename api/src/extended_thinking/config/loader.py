"""Tiered config loader (ADR 012).

Loading order (lowest → highest precedence, per-key deep merge):

  1. Schema defaults (Pydantic field defaults).
  2. User config:     $XDG_CONFIG_HOME/extended-thinking/config.toml
  3. Drop-ins:        $XDG_CONFIG_HOME/extended-thinking/conf.d/*.toml
                      (lexical order, later wins)
  4. Project config:  ./et.toml (discovered via upward directory walk)
  5. Secrets file:    $XDG_CONFIG_HOME/extended-thinking/secrets.toml
                      (merged like the others; separate file, same shape)
  6. Environment:     ET_* variables + a handful of legacy names.
                      Double-underscore separates nesting:
                      ET_EXTRACTION__MODEL → extraction.model
  7. Explicit overrides passed into load_settings().

The merged dict is handed to Pydantic for validation; bad keys/types fail
loud at startup.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Any, Mapping

from extended_thinking.config.paths import (  # re-exported for public API stability
    APP_NAME,
    user_config_dir,
    user_config_path,
    user_dropin_dir,
    user_secrets_path,
    xdg_config_home,
    xdg_data_home,
)
from extended_thinking.config.schema import Settings

__all__ = [
    "APP_NAME",
    "PROJECT_ROOT",
    "find_project_config",
    "load_settings",
    "user_config_dir",
    "user_config_path",
    "user_dropin_dir",
    "user_secrets_path",
    "xdg_config_home",
    "xdg_data_home",
]

logger = logging.getLogger(__name__)

# Project root: still exported for a handful of legacy callers.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

# ── Legacy env var names ──────────────────────────────────────────────
# Env keys that historically existed before ADR 012. Each maps to a
# dotted config key. Preserved so existing .env files keep working.
_LEGACY_ENV_MAP: dict[str, str] = {
    "ANTHROPIC_API_KEY": "credentials.anthropic_api_key",
    "OPENAI_API_KEY": "credentials.openai_api_key",
    "ET_EXTRACTION_MODEL": "extraction.model",
    "ET_EXTRACTION_PROVIDER": "extraction.provider",
    "ET_WISDOM_MODEL": "wisdom.model",
    "ET_WISDOM_PROVIDER": "wisdom.provider",
    "CORS_ORIGINS": "server.cors_origins",
}


def find_project_config(start: Path | None = None) -> Path | None:
    """Walk upward from `start` (default CWD) looking for `et.toml`."""
    cur = (start or Path.cwd()).resolve()
    seen_root = False
    while not seen_root:
        candidate = cur / "et.toml"
        if candidate.exists():
            return candidate
        if cur.parent == cur:
            seen_root = True
        else:
            cur = cur.parent
    return None


# ── Helpers ──────────────────────────────────────────────────────────

def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"invalid TOML in {path}: {e}") from e


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive per-key merge; scalars and lists get replaced wholesale,
    only tables recurse. Pattern matches systemd drop-in semantics."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _dotted_set(d: dict, dotted: str, value: Any) -> None:
    """Set d[a][b][c] = value from 'a.b.c'. Creates intermediate dicts."""
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _env_to_dict(env: Mapping[str, str]) -> dict[str, Any]:
    """Translate ET_* env vars and legacy names into a nested dict.

    Supports two conventions simultaneously:
      - New:    ET_<FAMILY>__<KEY>[__<SUBKEY>]  ('__' → nesting separator)
                e.g. ET_EXTRACTION__MODEL → extraction.model
      - Legacy: the handful of flat names in _LEGACY_ENV_MAP.
    """
    out: dict[str, Any] = {}
    # Legacy flat names first; new-style wins if both are set.
    for env_key, dotted in _LEGACY_ENV_MAP.items():
        if env_key in env:
            _dotted_set(out, dotted, env[env_key])
    # ET_* new-style
    for k, v in env.items():
        if not k.startswith("ET_") or k in _LEGACY_ENV_MAP:
            continue
        # ET_FOO__BAR → foo.bar ; ET_FOO → foo (root-level)
        stripped = k[3:]
        parts = [p.lower() for p in stripped.split("__") if p]
        if not parts:
            continue
        _dotted_set(out, ".".join(parts), v)
    return out


def _read_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader: KEY=value, one per line, # comments, no expansion.

    We avoid pulling in python-dotenv just for the legacy .env path. Values
    are treated as raw strings; Pydantic handles coercion downstream.
    """
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        result[key] = val
    return result


def _collect_dropins(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(p for p in path.glob("*.toml") if p.is_file())


# ── Public API ───────────────────────────────────────────────────────

def _reject_secrets_in_nonsecret_tiers(
    *sources: tuple[str, dict[str, Any]],
) -> None:
    """Fail loud if any [credentials.*] key appears outside secrets.toml/env.

    Credentials belong in `secrets.toml` (mode 0600, gitignored) or
    environment variables — never in `config.toml`, drop-ins, or project
    overrides. A repo-checked `config.toml` with a real API key is a
    security bug, and ET refuses to start in that state.
    """
    for label, data in sources:
        creds = data.get("credentials")
        if not isinstance(creds, dict):
            continue
        leaked = [k for k, v in creds.items() if isinstance(v, str) and v.strip()]
        if leaked:
            keys = ", ".join(sorted(leaked))
            raise RuntimeError(
                f"credentials found in {label}: {keys}. Move them to "
                f"{user_secrets_path()} (which is mode 0600 and gitignored), "
                f"or set them as environment variables. See "
                f"docs/configuration.md for the secrets policy."
            )


def load_settings(
    *,
    user_config: Path | None = None,
    dropin_dir: Path | None = None,
    project_config: Path | None = None,
    secrets: Path | None = None,
    env: Mapping[str, str] | None = None,
    overrides: dict | None = None,
    dotenv: Path | None = None,
) -> Settings:
    """Walk all config tiers, deep-merge, validate.

    Every path argument is overridable for tests. In production, callers
    should pass nothing and let XDG defaults apply.
    """
    user_config = user_config if user_config is not None else user_config_path()
    dropin_dir = dropin_dir if dropin_dir is not None else user_dropin_dir()
    if project_config is None:
        project_config = find_project_config()
    secrets = secrets if secrets is not None else user_secrets_path()

    # Load each non-secret tier separately so we can check each for leaked
    # credentials before merging.
    user_data = _read_toml(user_config)
    dropin_data_list = [(dp, _read_toml(dp)) for dp in _collect_dropins(dropin_dir)]
    project_data = _read_toml(project_config) if project_config is not None else {}

    # Enforce: no [credentials.*] anywhere except the secrets file and env.
    _reject_secrets_in_nonsecret_tiers(
        (f"config file {user_config}", user_data),
        *[(f"drop-in {p}", d) for p, d in dropin_data_list],
        (f"project config {project_config}", project_data) if project_config else ("", {}),
    )

    merged: dict[str, Any] = {}

    # 2. User
    merged = _deep_merge(merged, user_data)

    # 3. Drop-ins (lexical)
    for _, dp_data in dropin_data_list:
        merged = _deep_merge(merged, dp_data)

    # 4. Project
    merged = _deep_merge(merged, project_data)

    # 5. Secrets
    merged = _deep_merge(merged, _read_toml(secrets))

    # 6a. Legacy .env (at project root) — kept for continuity with old setup.
    #     Treated as if its KEY=value pairs were env vars of equivalent form.
    if dotenv is None:
        dotenv = PROJECT_ROOT / ".env"
    env_combined: dict[str, str] = {}
    env_combined.update(_read_dotenv(dotenv))
    # 6b. Real environment wins over .env
    env_combined.update(env if env is not None else os.environ)
    merged = _deep_merge(merged, _env_to_dict(env_combined))

    # 7. Explicit overrides
    if overrides:
        merged = _deep_merge(merged, overrides)

    return Settings.model_validate(merged)
