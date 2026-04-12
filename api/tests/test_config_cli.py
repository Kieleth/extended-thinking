"""ADR 012 step 2: `et config` subcommands.

Verifies init scaffolding (file perms, idempotency, --force), path listing,
show output, and validate exit codes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from extended_thinking.config.commands import (
    cmd_config_init,
    cmd_config_path,
    cmd_config_show,
    cmd_config_validate,
)


@pytest.fixture
def xdg_home(tmp_path, monkeypatch):
    """Point XDG_CONFIG_HOME at a throwaway dir so tests don't touch the real one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


class TestConfigInit:

    def test_writes_both_files(self, xdg_home, capsys):
        rc = cmd_config_init()
        assert rc == 0
        cfg = xdg_home / "extended-thinking" / "config.toml"
        sec = xdg_home / "extended-thinking" / "secrets.toml"
        assert cfg.exists()
        assert sec.exists()

    def test_secrets_is_mode_600(self, xdg_home):
        cmd_config_init()
        sec = xdg_home / "extended-thinking" / "secrets.toml"
        mode = stat.S_IMODE(os.stat(sec).st_mode)
        assert mode == 0o600, f"expected 600, got {oct(mode)}"

    def test_creates_dropins_dir(self, xdg_home):
        cmd_config_init()
        dropin = xdg_home / "extended-thinking" / "conf.d"
        assert dropin.is_dir()

    def test_rerun_skips_existing_without_force(self, xdg_home, capsys):
        cmd_config_init()
        capsys.readouterr()  # clear
        cmd_config_init()
        out = capsys.readouterr().out
        assert "skip" in out
        assert "exists" in out

    def test_force_overwrites(self, xdg_home):
        cmd_config_init()
        cfg = xdg_home / "extended-thinking" / "config.toml"
        cfg.write_text("# tampered\n")
        cmd_config_init(force=True)
        assert "extended-thinking config" in cfg.read_text()

    def test_generated_config_is_valid_toml_and_loads(self, xdg_home):
        """The default template must parse and validate — otherwise `init`
        followed by `validate` would fail on a fresh machine."""
        cmd_config_init()
        rc = cmd_config_validate()
        assert rc == 0


class TestConfigPath:

    def test_prints_all_known_paths(self, xdg_home, capsys):
        rc = cmd_config_path()
        assert rc == 0
        out = capsys.readouterr().out
        for label in ("user config", "drop-ins dir", "project config", "secrets"):
            assert label in out


class TestConfigShow:

    def test_redacts_credentials_by_default(self, xdg_home, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real")
        cmd_config_show()
        out = capsys.readouterr().out
        assert "sk-real" not in out
        # redacted form
        assert "***" in out or 'anthropic_api_key = ""' in out

    def test_show_secrets_reveals(self, xdg_home, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real")
        cmd_config_show(show_secrets=True)
        out = capsys.readouterr().out
        assert "sk-real" in out

    def test_json_format(self, xdg_home, capsys):
        cmd_config_show(format="json")
        out = capsys.readouterr().out
        import json
        data = json.loads(out)
        assert "extraction" in data
        assert data["extraction"]["model"].startswith("claude-")


class TestConfigValidate:

    def test_valid_config_exits_zero(self, xdg_home):
        assert cmd_config_validate() == 0

    def test_invalid_config_exits_two(self, xdg_home, capsys):
        cfg_dir = xdg_home / "extended-thinking"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text("nonsense_top_key = 1\n")
        rc = cmd_config_validate()
        err = capsys.readouterr().err
        assert rc == 2
        assert "validation failed" in err or "invalid" in err.lower()

    def test_malformed_toml_exits_two(self, xdg_home, capsys):
        cfg_dir = xdg_home / "extended-thinking"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text("this is = not [[toml\n")
        rc = cmd_config_validate()
        err = capsys.readouterr().err
        assert rc == 2
        assert "invalid TOML" in err or "TOML" in err
