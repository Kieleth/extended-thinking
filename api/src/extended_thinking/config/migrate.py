"""One-time pre-XDG → XDG data dir migration (ADR 012 step 3).

Before ADR 012, ET stored its KG and vectors under `~/.extended-thinking/`.
The XDG Base Directory Specification places application data under
`$XDG_DATA_HOME/<app>/` (default `~/.local/share/<app>/`).

On first run after upgrading, this module moves the legacy dir to the new
location if and only if:

  1. The user is on the default `data.root` (no explicit override).
  2. The legacy dir exists.
  3. The target dir does not yet exist (or is empty).

If both dirs hold data simultaneously, we leave both untouched and log a
warning — merging user data automatically is not safe.

The migration is atomic (`Path.rename`) so a partial state cannot exist on
a POSIX filesystem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from extended_thinking.config.paths import LEGACY_DATA_DIR, default_data_root
from extended_thinking.config.schema import Settings

logger = logging.getLogger(__name__)

# Process-wide marker so entry points can call this safely from multiple places.
_migration_done = False


@dataclass
class DataDirConflict(Exception):
    """Raised when both the legacy and XDG data dirs hold user data.

    Carries both paths + sizes so the CLI can render a useful notice
    with a copy-pasteable merge command. The data itself is never
    touched — merging is a decision, not a default.
    """
    legacy: Path
    xdg: Path
    legacy_size: int  # bytes
    xdg_size: int     # bytes

    def __str__(self) -> str:
        return (
            f"both legacy {self.legacy} and XDG {self.xdg} hold data; "
            "merge manually before continuing"
        )


def _dir_size(path: Path) -> int:
    """Total bytes under a directory. Returns 0 on any IO error — size
    is diagnostic, not load-bearing."""
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def migrate_data_dir(settings: Settings, *, force: bool = False) -> Path:
    """Ensure the data dir is in its XDG home. Returns the final resolved path.

    Idempotent: safe to call on every process start. No-ops after the first
    successful migration (and on fresh installs).

    Args:
        settings: loaded Settings instance.
        force: run the check even if a previous call in this process already
            ran. Useful for tests.

    Returns:
        The path ET should use for its data dir (may equal legacy if the user
        explicitly configured the old path).
    """
    global _migration_done
    if _migration_done and not force:
        return settings.data.root

    target = settings.data.root
    user_overrode = target != default_data_root()

    # Case 1: user explicitly pointed data.root elsewhere.
    if user_overrode:
        if LEGACY_DATA_DIR.exists():
            logger.info(
                "legacy data dir %s found but config pins data.root to %s; "
                "leaving legacy untouched",
                LEGACY_DATA_DIR, target,
            )
        _migration_done = True
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(exist_ok=True)
        return target

    # Case 2: no legacy to migrate; ensure XDG target exists.
    if not LEGACY_DATA_DIR.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(exist_ok=True)
        _migration_done = True
        return target

    # Case 3: both exist with content → dangerous. Raise with both paths
    # and sizes; the CLI renders a useful notice. We do not touch either.
    if target.exists() and any(target.iterdir()):
        _migration_done = True
        raise DataDirConflict(
            legacy=LEGACY_DATA_DIR,
            xdg=target,
            legacy_size=_dir_size(LEGACY_DATA_DIR),
            xdg_size=_dir_size(target),
        )

    # Case 4: XDG target exists but is empty → remove it, then rename.
    if target.exists():
        target.rmdir()

    # Case 5: perform the move.
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "migrating data dir: %s -> %s (one-time XDG move, ADR 012)",
        LEGACY_DATA_DIR, target,
    )
    LEGACY_DATA_DIR.rename(target)
    _migration_done = True
    return target


def _reset_marker_for_tests() -> None:
    """Clear the process-wide idempotency flag. Tests only."""
    global _migration_done
    _migration_done = False
