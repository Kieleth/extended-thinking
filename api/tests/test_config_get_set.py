"""ADR 012 step 7: `et config get/set/edit` subcommands.

Covers dotted-path reads, type coercion on writes, scope routing
(user/project/secrets), secret-scope guards, and validation after write.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from extended_thinking.config.commands import (
    cmd_config_edit,
    cmd_config_get,
    cmd_config_set,
)


@pytest.fixture
def xdg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture
def project_cwd(tmp_path, monkeypatch):
    """Run `set --scope project` inside a clean tmp dir."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


class TestGet:

    def test_reads_top_level_default(self, xdg_home, capsys):
        rc = cmd_config_get("extraction.model")
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out.startswith("claude-")

    def test_missing_key_exits_1(self, xdg_home, capsys):
        rc = cmd_config_get("nonexistent.path")
        err = capsys.readouterr().err
        assert rc == 1
        assert "no such key" in err

    def test_reads_nested_provider_value(self, xdg_home, capsys):
        rc = cmd_config_get("providers.claude_code.enabled")
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == "True"

    def test_table_prints_as_json(self, xdg_home, capsys):
        """Getting a subtree (not a leaf) should return structured output."""
        rc = cmd_config_get("extraction")
        out = capsys.readouterr().out
        assert rc == 0
        import json
        data = json.loads(out)
        assert "model" in data


class TestSet:

    def test_writes_and_round_trips(self, xdg_home, capsys):
        assert cmd_config_set("extraction.model", "custom-model") == 0
        capsys.readouterr()
        cmd_config_get("extraction.model")
        assert capsys.readouterr().out.strip() == "custom-model"

    def test_creates_config_file_when_absent(self, xdg_home):
        cfg = xdg_home / "extended-thinking" / "config.toml"
        assert not cfg.exists()
        cmd_config_set("extraction.model", "x")
        assert cfg.exists()

    def test_boolean_coercion(self, xdg_home, capsys):
        cmd_config_set("providers.claude_code.enabled", "false")
        capsys.readouterr()
        cmd_config_get("providers.claude_code.enabled")
        assert capsys.readouterr().out.strip() == "False"

    def test_numeric_coercion_int(self, xdg_home, capsys):
        cmd_config_set("algorithms.bow_tie.in_out_degree.top_k", "25")
        capsys.readouterr()
        cmd_config_get("algorithms.bow_tie.in_out_degree.top_k")
        assert capsys.readouterr().out.strip() == "25"

    def test_numeric_coercion_float(self, xdg_home, capsys):
        cmd_config_set("algorithms.decay.physarum.decay_rate", "0.88")
        capsys.readouterr()
        cmd_config_get("algorithms.decay.physarum.decay_rate")
        assert capsys.readouterr().out.strip() == "0.88"

    def test_list_coercion_via_commas(self, xdg_home, capsys):
        cmd_config_set(
            "algorithms.resolution.order",
            "sequence_matcher,embedding_cosine",
        )
        capsys.readouterr()
        cmd_config_get("algorithms.resolution.order")
        out = capsys.readouterr().out
        assert "sequence_matcher" in out
        assert "embedding_cosine" in out


class TestScopeRouting:

    def test_user_scope_writes_to_xdg_config(self, xdg_home):
        cmd_config_set("extraction.model", "x", scope="user")
        assert (xdg_home / "extended-thinking" / "config.toml").exists()

    def test_project_scope_writes_to_cwd(self, xdg_home, project_cwd):
        cmd_config_set("extraction.model", "proj", scope="project")
        assert (project_cwd / "et.toml").exists()

    def test_secrets_scope_chmod_600(self, xdg_home):
        cmd_config_set(
            "credentials.anthropic_api_key", "sk-test",
            scope="secrets",
        )
        sec = xdg_home / "extended-thinking" / "secrets.toml"
        assert sec.exists()
        mode = stat.S_IMODE(os.stat(sec).st_mode)
        assert mode == 0o600


class TestSecretsGuard:

    def test_refuses_credentials_in_user_scope(self, xdg_home, capsys):
        rc = cmd_config_set(
            "credentials.anthropic_api_key", "sk-leak",
            scope="user",
        )
        err = capsys.readouterr().err
        assert rc == 2
        assert "refusing to leak" in err or "secrets" in err.lower()

    def test_refuses_credentials_in_project_scope(self, xdg_home, project_cwd, capsys):
        rc = cmd_config_set(
            "credentials.openai_api_key", "sk-leak",
            scope="project",
        )
        assert rc == 2

    def test_refuses_nonsecret_in_secrets_scope(self, xdg_home, capsys):
        rc = cmd_config_set("extraction.model", "x", scope="secrets")
        err = capsys.readouterr().err
        assert rc == 2
        assert "credentials" in err.lower()


class TestValidationAfterWrite:

    def test_valid_write_returns_zero(self, xdg_home):
        rc = cmd_config_set("extraction.model", "ok")
        assert rc == 0

    def test_write_that_breaks_config_warns_and_exits_nonzero(self, xdg_home, capsys):
        """Writing an invalid nested value should surface the validation error."""
        # providers.claude_code.enabled must be bool; injecting a string-of-ints
        # via the int coercion path would succeed (Pydantic allows int→bool?),
        # so we force a string value that cannot be coerced to bool by TOML.
        # Easier: inject an unknown top-level key via direct path.
        rc = cmd_config_set("nonsense_key", "x")
        # Validation runs after write; should report failure via stderr
        err = capsys.readouterr().err
        assert rc == 1
        assert "no longer validates" in err


class TestEdit:

    def test_seeds_template_when_absent(self, xdg_home, monkeypatch):
        """cmd_config_edit creates and seeds the target file if missing."""
        # Use 'true' as a no-op editor so the test doesn't actually open $EDITOR
        monkeypatch.setenv("EDITOR", "true")
        monkeypatch.setenv("VISUAL", "true")
        rc = cmd_config_edit(scope="user")
        assert rc == 0
        cfg = xdg_home / "extended-thinking" / "config.toml"
        assert cfg.exists()
        assert "extended-thinking config" in cfg.read_text()

    def test_secrets_scope_seeds_and_chmods(self, xdg_home, monkeypatch):
        monkeypatch.setenv("EDITOR", "true")
        monkeypatch.setenv("VISUAL", "true")
        rc = cmd_config_edit(scope="secrets")
        assert rc == 0
        sec = xdg_home / "extended-thinking" / "secrets.toml"
        assert sec.exists()
        mode = stat.S_IMODE(os.stat(sec).st_mode)
        assert mode == 0o600
