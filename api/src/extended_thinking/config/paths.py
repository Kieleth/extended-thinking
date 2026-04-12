"""XDG Base Directory helpers and default path constants (ADR 012).

Separated from loader.py so the schema can import path resolvers without
creating a circular dependency.

Reference:
    https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "extended-thinking"

# Pre-XDG location retained for one-time migration (see migrate_data_dir).
LEGACY_DATA_DIR = Path.home() / ".extended-thinking"


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def default_data_root() -> Path:
    """XDG-compliant default for ET's data dir (KG, vectors, insights)."""
    return xdg_data_home() / APP_NAME


def user_config_dir() -> Path:
    return xdg_config_home() / APP_NAME


def user_config_path() -> Path:
    return user_config_dir() / "config.toml"


def user_dropin_dir() -> Path:
    return user_config_dir() / "conf.d"


def user_secrets_path() -> Path:
    return user_config_dir() / "secrets.toml"
