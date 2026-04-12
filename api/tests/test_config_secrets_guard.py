"""ADR 012 step 6: credentials in config.toml / drop-ins / project are a
hard error. Secrets belong in secrets.toml or env vars, period.

This is a security invariant: a user who copies their config.toml into a
dotfiles repo should never leak an API key, even by accident.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extended_thinking.config import load_settings


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def empty(tmp_path: Path):
    """Every loader input pinned to an empty tmp location."""
    return dict(
        user_config=tmp_path / "user.toml",
        dropin_dir=tmp_path / "dropins",
        project_config=None,
        secrets=tmp_path / "secrets.toml",
        env={},
        dotenv=tmp_path / ".env.none",
    )


# ── Rejection cases ────────────────────────────────────────────────────

class TestRejectsSecretsOutsideSecretsFile:

    def test_raises_on_api_key_in_user_config(self, tmp_path, empty):
        _write(tmp_path / "user.toml", """
[credentials]
anthropic_api_key = "sk-live-key"
""")
        with pytest.raises(RuntimeError, match="credentials found in"):
            load_settings(**{**empty, "user_config": tmp_path / "user.toml"})

    def test_raises_on_api_key_in_dropin(self, tmp_path, empty):
        dropin = tmp_path / "dropins"
        _write(dropin / "10-oops.toml", """
[credentials]
openai_api_key = "sk-leaked"
""")
        with pytest.raises(RuntimeError, match="credentials found in"):
            load_settings(**{**empty, "dropin_dir": dropin})

    def test_raises_on_api_key_in_project_config(self, tmp_path, empty):
        proj = _write(tmp_path / "et.toml", """
[credentials]
anthropic_api_key = "sk-project-scope"
""")
        with pytest.raises(RuntimeError, match="credentials found in"):
            load_settings(**{**empty, "project_config": proj})

    def test_error_message_points_to_secrets_file(self, tmp_path, empty):
        _write(tmp_path / "user.toml", """
[credentials]
anthropic_api_key = "x"
""")
        with pytest.raises(RuntimeError) as exc:
            load_settings(**{**empty, "user_config": tmp_path / "user.toml"})
        msg = str(exc.value)
        assert "secrets.toml" in msg
        assert "docs/configuration.md" in msg


# ── Permitted locations ────────────────────────────────────────────────

class TestAllowsSecretsInLegitimatePlaces:

    def test_secrets_file_is_fine(self, tmp_path, empty):
        secrets = _write(tmp_path / "secrets.toml", """
[credentials]
anthropic_api_key = "sk-from-secrets"
""")
        s = load_settings(**{**empty, "secrets": secrets})
        assert s.credentials.anthropic_api_key == "sk-from-secrets"

    def test_env_var_is_fine(self, empty):
        s = load_settings(**{**empty, "env": {"ANTHROPIC_API_KEY": "sk-from-env"}})
        assert s.credentials.anthropic_api_key == "sk-from-env"

    def test_empty_credentials_block_in_config_is_fine(self, tmp_path, empty):
        """A scaffolded config.toml with empty credential keys must still load —
        `et config init` produces exactly this shape."""
        _write(tmp_path / "user.toml", """
[credentials]
""")
        s = load_settings(**{**empty, "user_config": tmp_path / "user.toml"})
        assert s.credentials.anthropic_api_key == ""

    def test_only_whitespace_value_treated_as_empty(self, tmp_path, empty):
        """A stray empty-string or whitespace-only value doesn't trip the guard."""
        _write(tmp_path / "user.toml", """
[credentials]
anthropic_api_key = "   "
""")
        s = load_settings(**{**empty, "user_config": tmp_path / "user.toml"})
        assert s.credentials.anthropic_api_key == "   "


# ── Isolation: guard is per-tier, not global ──────────────────────────

class TestGuardIsTierSpecific:

    def test_secrets_in_secrets_does_not_break_when_user_has_other_config(
        self, tmp_path, empty,
    ):
        """Realistic case: user config has non-credential settings, secrets has the key.
        Both should load together without the guard tripping."""
        _write(tmp_path / "user.toml", """
[extraction]
model = "custom-model"
""")
        _write(tmp_path / "secrets.toml", """
[credentials]
anthropic_api_key = "sk-real"
""")
        s = load_settings(**{
            **empty,
            "user_config": tmp_path / "user.toml",
            "secrets": tmp_path / "secrets.toml",
        })
        assert s.extraction.model == "custom-model"
        assert s.credentials.anthropic_api_key == "sk-real"
