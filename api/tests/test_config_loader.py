"""ADR 012: tiered config loader.

Covers tier precedence (user → drop-ins → project → secrets → env → overrides),
deep-merge semantics, legacy env var compatibility, XDG path derivation, and
Pydantic validation behavior.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from extended_thinking.config import load_settings
from extended_thinking.config.loader import (
    _deep_merge,
    _env_to_dict,
    _read_dotenv,
    find_project_config,
    xdg_config_home,
    xdg_data_home,
)
from extended_thinking.config.schema import Settings


# ── Tier isolation helper ─────────────────────────────────────────────

@pytest.fixture
def empty_env(tmp_path: Path):
    """load_settings() with every path pointed at empty tmp locations."""
    return dict(
        user_config=tmp_path / "no_user.toml",
        dropin_dir=tmp_path / "no_dropins",
        project_config=None,
        secrets=tmp_path / "no_secrets.toml",
        env={},
        dotenv=tmp_path / "no_dotenv",
    )


def _write_toml(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ── Defaults ──────────────────────────────────────────────────────────

class TestDefaults:

    def test_no_sources_returns_schema_defaults(self, empty_env):
        s = load_settings(**empty_env)
        assert s.extraction.model == "claude-haiku-4-5-20251001"
        assert s.wisdom.model == "claude-opus-4-6"
        assert s.providers.claude_code.enabled is True
        assert s.algorithms == {}

    def test_legacy_flat_attrs_still_work(self, empty_env):
        s = load_settings(**empty_env)
        # Compatibility properties defined in schema.py
        assert s.extraction_model == "claude-haiku-4-5-20251001"
        assert s.wisdom_model == "claude-opus-4-6"
        assert s.anthropic_api_key == ""
        assert s.cors_origins == "http://localhost:3000"


# ── User → drop-ins → project → secrets precedence ────────────────────

class TestTierPrecedence:

    def test_user_config_applied(self, tmp_path, empty_env):
        _write_toml(tmp_path / "user.toml", """
[extraction]
model = "user-model"
""")
        s = load_settings(**{**empty_env, "user_config": tmp_path / "user.toml"})
        assert s.extraction.model == "user-model"

    def test_dropin_overrides_user(self, tmp_path, empty_env):
        user = _write_toml(tmp_path / "user.toml", """
[extraction]
model = "user-model"
""")
        dropin = tmp_path / "conf.d"
        _write_toml(dropin / "10-plugin.toml", """
[extraction]
model = "dropin-model"
""")
        s = load_settings(**{**empty_env, "user_config": user, "dropin_dir": dropin})
        assert s.extraction.model == "dropin-model"

    def test_dropins_processed_in_lexical_order(self, tmp_path, empty_env):
        dropin = tmp_path / "conf.d"
        _write_toml(dropin / "99-last.toml", '[extraction]\nmodel = "last"\n')
        _write_toml(dropin / "10-first.toml", '[extraction]\nmodel = "first"\n')
        s = load_settings(**{**empty_env, "dropin_dir": dropin})
        assert s.extraction.model == "last"  # 99 > 10

    def test_project_config_overrides_dropins(self, tmp_path, empty_env):
        dropin = tmp_path / "conf.d"
        _write_toml(dropin / "10.toml", '[extraction]\nmodel = "dropin"\n')
        project = _write_toml(tmp_path / "et.toml", '[extraction]\nmodel = "project"\n')
        s = load_settings(**{
            **empty_env,
            "dropin_dir": dropin,
            "project_config": project,
        })
        assert s.extraction.model == "project"

    def test_secrets_overrides_project(self, tmp_path, empty_env):
        """Precedence check on a non-credential key.

        Credentials in project config are rejected by the secrets guard
        (ADR 012 step 6) — see test_config_secrets_guard.py. For tier
        ordering we use a regular config key both tiers are allowed to touch.
        """
        project = _write_toml(tmp_path / "et.toml", """
[extraction]
model = "project-model"
""")
        secrets = _write_toml(tmp_path / "secrets.toml", """
[extraction]
model = "secrets-model"
""")
        s = load_settings(**{
            **empty_env,
            "project_config": project,
            "secrets": secrets,
        })
        assert s.extraction.model == "secrets-model"

    def test_env_overrides_everything(self, tmp_path, empty_env):
        secrets = _write_toml(tmp_path / "secrets.toml", """
[credentials]
anthropic_api_key = "secrets-key"
""")
        s = load_settings(**{
            **empty_env,
            "secrets": secrets,
            "env": {"ANTHROPIC_API_KEY": "env-key"},
        })
        assert s.credentials.anthropic_api_key == "env-key"

    def test_explicit_overrides_trump_env(self, empty_env):
        s = load_settings(**{
            **empty_env,
            "env": {"ET_EXTRACTION__MODEL": "env-model"},
            "overrides": {"extraction": {"model": "override-model"}},
        })
        assert s.extraction.model == "override-model"


# ── Deep merge semantics ──────────────────────────────────────────────

class TestDeepMerge:

    def test_scalar_replaced(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_tables_recurse(self):
        merged = _deep_merge(
            {"a": {"b": 1, "c": 2}},
            {"a": {"b": 9}},
        )
        assert merged == {"a": {"b": 9, "c": 2}}  # c preserved

    def test_list_replaced_not_appended(self):
        merged = _deep_merge({"a": [1, 2]}, {"a": [3]})
        assert merged == {"a": [3]}  # lists wholesale replaced

    def test_table_replaces_scalar(self):
        merged = _deep_merge({"a": 1}, {"a": {"b": 2}})
        assert merged == {"a": {"b": 2}}


# ── Env var translation ──────────────────────────────────────────────

class TestEnvTranslation:

    def test_new_style_double_underscore(self):
        d = _env_to_dict({"ET_EXTRACTION__MODEL": "m"})
        assert d == {"extraction": {"model": "m"}}

    def test_new_style_deep_nesting(self):
        d = _env_to_dict({"ET_ALGORITHMS__DECAY__PHYSARUM__DECAY_RATE": "0.9"})
        # Note: decay_rate stays a single key because it's the terminal segment
        assert d == {"algorithms": {"decay": {"physarum": {"decay_rate": "0.9"}}}}

    def test_legacy_anthropic_key(self):
        d = _env_to_dict({"ANTHROPIC_API_KEY": "sk-..."})
        assert d == {"credentials": {"anthropic_api_key": "sk-..."}}

    def test_legacy_extraction_model(self):
        d = _env_to_dict({"ET_EXTRACTION_MODEL": "haiku"})
        assert d == {"extraction": {"model": "haiku"}}

    def test_new_style_wins_over_legacy_when_both_set(self):
        d = _env_to_dict({
            "ET_EXTRACTION_MODEL": "legacy",
            "ET_EXTRACTION__MODEL": "new",
        })
        assert d["extraction"]["model"] == "new"

    def test_unrelated_env_vars_ignored(self):
        d = _env_to_dict({"PATH": "/usr/bin", "HOME": "/home/x"})
        assert d == {}


# ── XDG + .env + project walk ────────────────────────────────────────

class TestXDGPaths:

    def test_xdg_config_home_respects_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert xdg_config_home() == tmp_path

    def test_xdg_config_home_defaults_to_dot_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert xdg_config_home() == Path.home() / ".config"

    def test_xdg_data_home_respects_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert xdg_data_home() == tmp_path

    def test_find_project_config_walks_upward(self, tmp_path):
        project_root = tmp_path / "proj"
        (project_root / "deep" / "nested").mkdir(parents=True)
        (project_root / "et.toml").write_text("")
        found = find_project_config(start=project_root / "deep" / "nested")
        assert found == project_root / "et.toml"

    def test_find_project_config_returns_none_when_absent(self, tmp_path):
        found = find_project_config(start=tmp_path)
        assert found is None


class TestDotenv:

    def test_reads_key_value_pairs(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("A=1\nB=2\n")
        assert _read_dotenv(p) == {"A": "1", "B": "2"}

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("# comment\n\nA=1\n# another\nB=2\n")
        assert _read_dotenv(p) == {"A": "1", "B": "2"}

    def test_strips_quotes(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text('A="quoted"\nB=\'single\'\nC=bare\n')
        assert _read_dotenv(p) == {"A": "quoted", "B": "single", "C": "bare"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert _read_dotenv(tmp_path / "nope.env") == {}

    def test_dotenv_loaded_below_env(self, tmp_path, empty_env):
        dotenv = tmp_path / ".env"
        dotenv.write_text("ANTHROPIC_API_KEY=from-dotenv\n")
        s = load_settings(**{
            **empty_env,
            "dotenv": dotenv,
            "env": {"ANTHROPIC_API_KEY": "from-real-env"},
        })
        # Real env overrides .env
        assert s.credentials.anthropic_api_key == "from-real-env"


# ── Validation ────────────────────────────────────────────────────────

class TestValidation:

    def test_unknown_top_level_key_rejected(self, tmp_path, empty_env):
        cfg = _write_toml(tmp_path / "u.toml", 'nonsense_key = "x"\n')
        with pytest.raises(ValidationError):
            load_settings(**{**empty_env, "user_config": cfg})

    def test_wrong_type_rejected(self, tmp_path, empty_env):
        cfg = _write_toml(tmp_path / "u.toml", """
[providers.claude_code]
enabled = "not-a-bool"
""")
        with pytest.raises(ValidationError):
            load_settings(**{**empty_env, "user_config": cfg})

    def test_algorithms_table_is_free_form(self, tmp_path, empty_env):
        """[algorithms.*] stays untyped so plugins validate their own params."""
        cfg = _write_toml(tmp_path / "u.toml", """
[algorithms.decay.physarum]
active = true
decay_rate = 0.9
source_age_aware = true
some_new_knob = "whatever"
""")
        s = load_settings(**{**empty_env, "user_config": cfg})
        assert s.algorithms["decay"]["physarum"]["decay_rate"] == 0.9
        assert s.algorithms["decay"]["physarum"]["some_new_knob"] == "whatever"

    def test_malformed_toml_raises_actionable_error(self, tmp_path, empty_env):
        bad = _write_toml(tmp_path / "u.toml", "this is = not [[valid toml\n")
        with pytest.raises(RuntimeError, match="invalid TOML"):
            load_settings(**{**empty_env, "user_config": bad})


# ── Plugin param override via drop-in (ADR 011 prep) ─────────────────

class TestPluginOverrideFlow:

    def test_dropin_can_override_plugin_params(self, tmp_path, empty_env):
        """Confirms the pattern ADR 011 will rely on: a drop-in file owns
        the config for a specific plugin family without touching user config."""
        dropin = tmp_path / "conf.d"
        _write_toml(dropin / "20-physarum.toml", """
[algorithms.decay.physarum]
active = true
decay_rate = 0.85
""")
        s = load_settings(**{**empty_env, "dropin_dir": dropin})
        assert s.algorithms["decay"]["physarum"]["decay_rate"] == 0.85
