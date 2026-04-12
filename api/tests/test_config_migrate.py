"""ADR 012 step 3: one-time pre-XDG → XDG data dir migration.

Covers every branch in migrate_data_dir():
  - Fresh install (no legacy): XDG target created.
  - Legacy only: content moved, legacy gone, target populated.
  - Both exist, target has data: refuse, leave both.
  - Both exist, target empty: proceed (target gets removed then renamed).
  - User override: legacy left alone, target created if missing.
  - Idempotency: second call is a no-op.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extended_thinking.config.migrate import migrate_data_dir, _reset_marker_for_tests
from extended_thinking.config.schema import DataConfig, Settings


@pytest.fixture(autouse=True)
def reset_idempotency_flag():
    """Each test starts with a clean slate for the process-wide marker."""
    _reset_marker_for_tests()
    yield
    _reset_marker_for_tests()


@pytest.fixture
def xdg_home(tmp_path, monkeypatch):
    """Point XDG_DATA_HOME at tmp_path so the XDG target is isolated."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    yield tmp_path


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """Point Path.home() at tmp_path/home so LEGACY_DATA_DIR is under tmp.

    LEGACY_DATA_DIR is captured at import time in paths.py, so we also have
    to monkeypatch its reference there.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    legacy_path = fake_home / ".extended-thinking"

    # Patch Path.home() — covers default_data_root() and friends.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # The LEGACY_DATA_DIR constant was evaluated at module import against
    # the real home. Rebind both references.
    from extended_thinking.config import paths as _paths
    from extended_thinking.config import migrate as _migrate
    monkeypatch.setattr(_paths, "LEGACY_DATA_DIR", legacy_path)
    monkeypatch.setattr(_migrate, "LEGACY_DATA_DIR", legacy_path)

    yield legacy_path


def _make_settings_with_default_root():
    """Settings using the schema default — XDG-derived at instance time."""
    return Settings()


def _make_settings_with_custom_root(root: Path):
    return Settings(data=DataConfig(root=root))


# ── Fresh install: no legacy ──────────────────────────────────────────

class TestFreshInstall:

    def test_creates_xdg_target(self, xdg_home, legacy):
        s = _make_settings_with_default_root()
        result = migrate_data_dir(s)
        assert result.exists()
        assert result == xdg_home / "xdg_data" / "extended-thinking"
        assert not legacy.exists()

    def test_no_legacy_move_attempted(self, xdg_home, legacy, caplog):
        s = _make_settings_with_default_root()
        with caplog.at_level("INFO"):
            migrate_data_dir(s)
        assert "migrating data dir" not in caplog.text.lower()


# ── Migration: legacy only ────────────────────────────────────────────

class TestLegacyMigration:

    def test_content_is_moved(self, xdg_home, legacy):
        legacy.mkdir()
        (legacy / "knowledge").mkdir()
        (legacy / "knowledge" / "catalog.kz").write_text("x")

        s = _make_settings_with_default_root()
        result = migrate_data_dir(s)

        assert result.exists()
        assert (result / "knowledge" / "catalog.kz").read_text() == "x"
        assert not legacy.exists()

    def test_migration_logs_info(self, xdg_home, legacy, caplog):
        legacy.mkdir()
        s = _make_settings_with_default_root()
        with caplog.at_level("INFO"):
            migrate_data_dir(s)
        assert "migrating data dir" in caplog.text.lower()

    def test_empty_xdg_target_does_not_block(self, xdg_home, legacy):
        """If XDG target was pre-created but empty (e.g. by init script),
        migration should still proceed."""
        legacy.mkdir()
        (legacy / "data.txt").write_text("legacy")
        target = xdg_home / "xdg_data" / "extended-thinking"
        target.mkdir(parents=True)  # empty

        s = _make_settings_with_default_root()
        result = migrate_data_dir(s)
        assert (result / "data.txt").read_text() == "legacy"


# ── Conflict: both have data ──────────────────────────────────────────

class TestConflictBothHaveData:

    def test_refuses_to_touch_either(self, xdg_home, legacy, caplog):
        legacy.mkdir()
        (legacy / "legacy-data").write_text("old")
        target = xdg_home / "xdg_data" / "extended-thinking"
        target.mkdir(parents=True)
        (target / "new-data").write_text("new")

        s = _make_settings_with_default_root()
        with caplog.at_level("WARNING"):
            result = migrate_data_dir(s)

        assert (legacy / "legacy-data").exists()
        assert (target / "new-data").exists()
        assert "automatic migration refused" in caplog.text.lower()
        assert result == target


# ── User override: skip migration ─────────────────────────────────────

class TestUserOverride:

    def test_legacy_left_alone_when_user_pins_path(self, xdg_home, legacy, tmp_path, caplog):
        legacy.mkdir()
        (legacy / "keep").write_text("keep")
        custom = tmp_path / "custom_data"

        s = _make_settings_with_custom_root(custom)
        with caplog.at_level("INFO"):
            result = migrate_data_dir(s)

        assert result == custom
        assert custom.exists()
        assert legacy.exists()  # untouched
        assert "leaving legacy untouched" in caplog.text.lower()


# ── Idempotency ───────────────────────────────────────────────────────

class TestIdempotency:

    def test_second_call_is_noop(self, xdg_home, legacy):
        legacy.mkdir()
        (legacy / "x").write_text("1")

        s = _make_settings_with_default_root()
        first = migrate_data_dir(s)

        # Re-create legacy; second call should NOT touch it because the
        # process-wide flag short-circuits.
        legacy.mkdir()
        (legacy / "stale").write_text("untouched")

        second = migrate_data_dir(s)
        assert first == second
        assert (legacy / "stale").exists()  # migrate was skipped

    def test_force_flag_reruns(self, xdg_home, legacy):
        legacy.mkdir()
        (legacy / "a").write_text("a")

        s = _make_settings_with_default_root()
        migrate_data_dir(s)

        # Put something back under legacy; force=True should migrate again.
        legacy.mkdir()
        (legacy / "b").write_text("b")
        # target already exists with 'a' — this is the "both exist" guard.
        # To make force=True actually do something, empty the target first:
        target = s.data.root
        for item in list(target.iterdir()):
            if item.is_file():
                item.unlink()
            else:
                import shutil
                shutil.rmtree(item)

        migrate_data_dir(s, force=True)
        assert (target / "b").exists()
