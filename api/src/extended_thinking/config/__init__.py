"""extended-thinking configuration (ADR 012).

Public surface:
  settings         — process-wide Settings instance (module-level singleton)
  Settings         — Pydantic model; see schema.py for the full tree
  load_settings    — tiered loader; tests may call with fake paths
  PROJECT_ROOT     — repo root, kept for a few legacy callers

Nested config is the source of truth. Legacy flat attrs
(`settings.anthropic_api_key`, `settings.extraction_model`, etc.) remain
available as compatibility properties on Settings.
"""

from extended_thinking.config.loader import (
    PROJECT_ROOT,
    find_project_config,
    load_settings,
)
from extended_thinking.config.migrate import DataDirConflict, migrate_data_dir
from extended_thinking.config.paths import (
    APP_NAME,
    LEGACY_DATA_DIR,
    default_data_root,
    user_config_dir,
    user_config_path,
    user_dropin_dir,
    user_secrets_path,
    xdg_cache_home,
    xdg_config_home,
    xdg_data_home,
)
from extended_thinking.config.schema import Settings

settings: Settings = load_settings()

__all__ = [
    "settings",
    "Settings",
    "load_settings",
    "migrate_data_dir",
    "DataDirConflict",
    "PROJECT_ROOT",
    "APP_NAME",
    "LEGACY_DATA_DIR",
    "default_data_root",
    "find_project_config",
    "user_config_dir",
    "user_config_path",
    "user_dropin_dir",
    "user_secrets_path",
    "xdg_config_home",
    "xdg_data_home",
    "xdg_cache_home",
]
