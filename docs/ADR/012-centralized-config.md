# ADR 012: Centralized Configuration (XDG + TOML + Tiered Overrides)

**Status:** Accepted
**Date:** 2026-04-12
**Depends on:** ADR 003 (Pluggable Algorithms), ADR 004 (Configurable Models), ADR 010 (Batteries-Included Providers)
**Blocks:** ADR 011 (Proactive Enrichment) — the enrichment layer's triggers, sources, relevance gates, and cache policy all need a home before they can ship.

## Context

Configuration in extended-thinking has grown in bursts. Model selection (ADR 004) landed as environment variables in `extended_thinking.config.Settings` (Pydantic + `.env`). Plugin selection (ADR 003) ships as hardcoded dicts passed at call sites (e.g., `{"algorithms": {"resolution": ["sequence_matcher", "embedding_cosine"]}}` in `pipeline_v2.py`). Data locations are hardcoded to `~/.extended-thinking/` throughout the codebase. Provider detection uses hardcoded paths (`~/.claude/projects/`, `~/Downloads/`).

Three problems compound:

1. **Dispersion.** Config lives in `Settings`, inline literals, hardcoded paths, and `AlgorithmMeta.parameters` defaults. Changing a plugin's default requires editing the plugin source.

2. **No user surface.** A user wanting to disable `embedding_cosine` resolution has no way to do it without editing code. Same for changing the Physarum decay rate, turning off a provider, or pointing the data dir elsewhere.

3. **Non-standard location.** `~/.extended-thinking/` predates XDG Base Directory support. Modern Linux/macOS tooling expects `~/.config/extended-thinking/`.

ADR 011 (Proactive Enrichment) will introduce five new configuration axes (trigger kinds, source adapters, relevance gates, cache policy, per-source rate limits). Without a centralized config system they would land as more scattered dicts and more environment variables. ADR 012 needs to land first.

## Decision

**Adopt a single, typed, tiered TOML configuration system, XDG-compliant, with a drop-in directory for extensions and a separate file for secrets.**

### File format: TOML

- Typed (bool, int, float, string, list, table) without YAML's whitespace and implicit-conversion pitfalls.
- Python `tomllib` in stdlib (3.11+); we already require Python 3.12+.
- Hierarchical tables match the plugin family → plugin → parameter shape without contortion.
- Human-readable with comments; `pyproject.toml` has normalized TOML across the Python ecosystem.

### File locations: XDG Base Directory

- **User config:** `$XDG_CONFIG_HOME/extended-thinking/config.toml` (default `~/.config/extended-thinking/config.toml`).
- **Drop-ins:** `$XDG_CONFIG_HOME/extended-thinking/conf.d/*.toml` — processed in lexical order; later files override earlier. Matches the systemd/sshd pattern: a third-party plugin package can drop a single file to register itself without editing the main config.
- **Secrets:** `$XDG_CONFIG_HOME/extended-thinking/secrets.toml`, file mode 600, gitignored. Holds API keys and any credential-bearing values. Separate from `config.toml` so users can check `config.toml` into dotfiles repos.
- **Project override:** `./et.toml` discovered via upward directory walk from CWD, applied on top of user config. Lets a project pin its own algorithm set without touching global config.
- **Data:** `$XDG_DATA_HOME/extended-thinking/` (default `~/.local/share/extended-thinking/`) for the KG and vectors. Default moves from `~/.extended-thinking/`; migration is a one-time move at first run.

### Override tiers (lowest to highest precedence)

1. Built-in defaults (plugin `AlgorithmMeta.parameters`, provider protocol defaults).
2. User config (`~/.config/extended-thinking/config.toml`).
3. Drop-ins (`conf.d/*.toml`, lexical order).
4. Project config (`./et.toml` if found via upward walk).
5. Secrets file (`secrets.toml`) — separate parsing path, merged into the same tree.
6. Environment variables (`ET_*`, double-underscore for nesting, per Pydantic convention).
7. CLI flags (explicit `et --config-override key=value` or per-command args).

Higher tiers override lower tiers *per-key*, not wholesale. Merging is deep: setting `[algorithms.decay.physarum] decay_rate = 0.9` in a drop-in doesn't reset the rest of the algorithms tree.

### Schema owner: Pydantic

One typed `Settings` model is the authoritative schema. Loading walks the tiers in order, deep-merges TOML tables, overlays env vars, and validates the final dict through Pydantic. Validation errors are actionable (bad key, wrong type, unknown plugin name) and fail at startup, not at first use.

### Secrets are opaque to the main config

`secrets.toml` has the same shape as `config.toml` but only for fields marked `secret: true` in the schema. API keys cannot be set in `config.toml`; an attempt to do so is a validation error. This keeps `config.toml` safe to share and version.

### `et config` CLI surface

Match the git-config ergonomic:

- `et config init` — write a fully-commented default `config.toml` and an empty `secrets.toml` (mode 600).
- `et config path` — print resolved config file path(s).
- `et config show` — print the fully resolved effective config.
- `et config get <key>` — read one value (dotted path: `algorithms.decay.physarum.decay_rate`).
- `et config set <key> <value>` — write to user config (or `--project` / `--system`).
- `et config edit` — open resolved user config in `$EDITOR`.
- `et config validate` — load + validate without running anything.

### Shape (illustrative, not exhaustive)

```toml
[data]
root = "~/.local/share/extended-thinking"

[providers.claude_code]
enabled = true
projects_dir = "~/.claude/projects"

[providers.chatgpt_export]
enabled = true
scan_paths = ["~/Downloads", "~/Documents"]

[extraction]
provider = "anthropic"
model = "claude-haiku-4-5-20251001"

[wisdom]
provider = "anthropic"
model = "claude-opus-4-6"

[algorithms.decay.physarum]
active = true
decay_rate = 0.95
source_age_aware = true

[algorithms.activity_score.recency_weighted]
active = true
top_k = 10

[algorithms.resolution]
order = ["sequence_matcher", "embedding_cosine"]

# Future (ADR 011):
# [enrichment] enabled = true
# [enrichment.triggers.frequency_threshold] min_frequency = 3
# [enrichment.sources.wikipedia] enabled = true
# [enrichment.relevance_gate] strategies = ["embedding_cosine", "llm_judge"]
# [enrichment.cache] refresh_after_days = 30
```

`secrets.toml`:

```toml
[credentials]
anthropic_api_key = "..."
openai_api_key = "..."
```

## Non-decisions

- **No remote config server.** Config is local files. Teams wanting shared config use dotfiles repos or drop-ins; we don't ship a sync mechanism.
- **No hot reload initially.** Config loads at process start. A long-running MCP server restarts to pick up changes. Can be added later if needed, but we don't promise it.
- **No web UI for config.** The CLI is the interface. A frontend can read `et config show`.
- **No YAML import.** TOML only. Keeping one format avoids two dialects with subtly different semantics.
- **No global `/etc/extended-thinking/` tier initially.** Single-user tool; we can add system tier later if multi-user deployments need it.
- **No breaking change to env vars.** Existing `ET_*` variables continue to work as the top override tier. Documented mapping between env var names and TOML keys.

## Implementation approach

Migration is mechanical and incremental; nothing depends on a big-bang switch.

1. **Add the config loader.** New `extended_thinking/config/loader.py` that walks tiers, deep-merges TOML, overlays env, validates. `Settings` becomes this loader's output. Existing env-var behavior keeps working because env is the top tier.

2. **Add `et config init` and `et config show`.** Writing a default TOML forces us to enumerate every configurable knob in one place; showing the resolved config is the debugging surface.

3. **Migrate data dir default** from `~/.extended-thinking/` to `~/.local/share/extended-thinking/`. First-run migration detects the old location and moves it. Old path continues to work if explicitly set in config.

4. **Migrate plugin config.** Replace every inline `{"algorithms": ...}` dict in the codebase with a read from `settings.algorithms`. Each migration is one call site; tests stay green throughout.

5. **Migrate provider paths.** `~/.claude/projects` and `~/Downloads` etc. move from hardcoded to `settings.providers.*.{path,scan_paths}`. AutoProvider reads these.

6. **Secrets file.** Split API keys out of `.env` / `config.toml` into `secrets.toml`. Fail-loud if a secret is found in the main config.

7. **Complete `et config` subcommands** (get, set, edit, validate). Unblocks most user workflows.

At this point ADR 011 (enrichment) can land with all its knobs as TOML keys from day one.

## Consequences

**Positive:**

- One place to look. Users, tests, plugins, and docs all reference the same typed schema.
- Plugin defaults become meaningful: `AlgorithmMeta.parameters` is the lowest tier; overriding is a TOML edit.
- ADR 011 gets a clean surface for its five configurable axes with zero new dispersion.
- Follows platform conventions. Tools like `direnv`, `chezmoi`, and standard dotfiles management already understand XDG + TOML.
- Drop-ins give a clean extension story: a third-party plugin package ships a `conf.d/my-plugin.toml` and it composes with the user's config.
- Secrets split makes `config.toml` shareable. Screenshots, dotfiles repos, bug reports — none leak credentials.

**Negative:**

- One-time migration cost. Every hardcoded path and inline config dict in the current codebase has to move. The hardcoded paths are finite (handful of files) so the scope is bounded, but nothing's free.
- Users who had `~/.extended-thinking/` get a silent move to `~/.local/share/extended-thinking/` on first run after upgrade. Log line must be explicit.
- Pydantic schema becomes load-bearing. Schema changes that rename keys need migration code (additive is easy; rename is a deprecation window).
- TOML's rigidity: no expressions, no includes other than drop-ins. If someone wants computed config, they use env vars or a pre-processing step; we don't grow a DSL.

## Alternatives considered

- **Keep env vars + .env only.** Works for flat config; collapses the moment plugin selection needs nesting. Rejected.
- **YAML.** Rejected: whitespace sensitivity, type coercion surprises (`no` as bool, `2024-01-01` auto-parsed to date), security footguns with anchors and tags.
- **JSON.** Rejected: no comments, verbose, user-hostile.
- **Python config files (Django-style).** Rejected: executes arbitrary code, can't be linted without running, cross-platform path issues, no standard tooling expects it.
- **Single flat INI.** Rejected: no nesting, no typing. Dies at the first plugin parameter table.

## References

- ADR 003 (Pluggable Algorithms) — defines the plugin registry this config surfaces.
- ADR 004 (Configurable Models) — current env-var-only model selection; becomes top-tier override.
- ADR 010 (Batteries-Included Providers) — provider selection becomes TOML.
- ADR 011 (Proactive Enrichment) — blocked until this ships.
- XDG Base Directory Specification: https://specifications.freedesktop.org/basedir-spec/
- TOML spec: https://toml.io/
- git-config tiered model: user/system/local precedence ordering.
- systemd drop-ins: `*.conf.d/` pattern for distribution-friendly overrides.
