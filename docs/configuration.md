# Configuration

extended-thinking is configured through a tiered TOML system that follows the XDG Base Directory specification. This document is the full reference: where files live, how tiers merge, every key, every environment variable, and how to extend the system with drop-ins.

**Design decision:** see [ADR 012](./ADR/012-centralized-config.md) for the rationale behind this layout (why TOML, why XDG, why drop-ins, why a separate secrets file).

## Quick start

```bash
et config init              # scaffold a default config
et config path              # show every file ET will consult
et config show              # print the resolved, effective config
et config validate          # fail loud if anything is malformed
```

That's enough for most users. The rest of this document is for when you want to tune plugins, override specific values, or ship configuration as part of a dotfiles repo.

## Where files live

ET follows the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html):

| What | Environment | Default |
|---|---|---|
| User config dir | `$XDG_CONFIG_HOME/extended-thinking` | `~/.config/extended-thinking` |
| Data dir (KG, vectors) | `$XDG_DATA_HOME/extended-thinking` | `~/.local/share/extended-thinking` |

Inside the config dir:

```
~/.config/extended-thinking/
  config.toml        ← your settings (safe to share, no secrets)
  secrets.toml       ← API keys (mode 0600, gitignore this)
  conf.d/            ← drop-in overrides (lexical order, later wins)
    10-plugins.toml
    20-my-canons.toml
```

A per-project override lives outside the XDG dir:

```
<any project root>/et.toml     ← discovered via upward walk from CWD
```

### Migration from pre-XDG locations

If you had `~/.extended-thinking/` from before this layout landed, ET performs a one-time atomic move to `~/.local/share/extended-thinking/` on first run. Look for this log line:

```
migrating data dir: ~/.extended-thinking -> ~/.local/share/extended-thinking (one-time XDG move, ADR 012)
```

The old directory is removed (contents moved, not copied). If both locations exist with data simultaneously, ET refuses to touch either and logs a warning — you must reconcile manually.

If you have explicitly set `data.root` in your config to something else, ET leaves both locations alone.

## Tier precedence

Every configuration value resolves through seven tiers, lowest to highest precedence. Higher tiers override lower tiers **per-key**, not wholesale — a drop-in that sets `[algorithms.decay.physarum] decay_rate = 0.9` does not reset the rest of the `[algorithms]` tree.

1. **Schema defaults.** Built into the Pydantic model. What you get from a completely empty config.
2. **User config.** `$XDG_CONFIG_HOME/extended-thinking/config.toml`.
3. **Drop-ins.** `$XDG_CONFIG_HOME/extended-thinking/conf.d/*.toml`, processed in **lexical** filename order. Use numeric prefixes (`10-`, `20-`, `99-`) to control ordering.
4. **Project config.** `./et.toml` found via upward walk from the current working directory. The first match wins; there is no recursive merge of multiple project files.
5. **Secrets file.** `$XDG_CONFIG_HOME/extended-thinking/secrets.toml`. Loaded with the same shape as `config.toml`; only `[credentials]` values actually matter there in practice.
6. **Environment variables.** Prefix `ET_`, with double-underscore `__` separating nested keys. Legacy flat names also supported (see below).
7. **Explicit overrides.** Anything passed into `load_settings(overrides=...)`. Primarily for tests and CLI flags.

Run `et config show` to see the fully merged result.

## The schema

Defaults shown inline. Everything is optional; omit what you don't care about.

### `[data]` — where ET stores its own artifacts

```toml
[data]
root = "~/.local/share/extended-thinking"
```

Subdirectories created under `data.root`:

```
data.root/
  knowledge/        ← Kuzu GraphStore (bitemporal KG)
  vectors/          ← ChromaDB (semantic search, optional)
  insights/         ← wisdom cards (JSON)
```

### `[providers.*]` — memory provider toggles and paths

All providers are enabled by default. Turn off sources you don't want ET to scan.

```toml
[providers.claude_code]
enabled = true
projects_dir = "~/.claude/projects"

[providers.chatgpt_export]
enabled = true
scan_paths = ["~/Downloads", "~/Documents"]

[providers.copilot_chat]
enabled = true

[providers.cursor]
enabled = true

[providers.folder]
enabled = true
paths = []        # extra folders of .md/.txt to index

[providers.generic_openai_chat]
enabled = true

[providers.mempalace]
enabled = true
```

See [ADR 010](./ADR/010-batteries-included-providers.md) for provider philosophy.

### `[extraction]` and `[wisdom]` — LLM model selection

Extraction runs per batch of chunks (many calls, cheap tier).  
Wisdom runs once per insight (one call, strong reasoning tier).

```toml
[extraction]
provider = ""                            # "" = auto-detect from available keys
model = "claude-haiku-4-5-20251001"

[wisdom]
provider = ""
model = "claude-opus-4-6"
```

Valid `provider` values today: `anthropic`, `openai`. Leave empty to pick whichever API key is set. See [ADR 004](./ADR/004-configurable-models.md).

### `[server]` — FastAPI / CORS

```toml
[server]
cors_origins = "http://localhost:3000"
```

### `[credentials]` — API keys (secrets.toml, not config.toml)

```toml
[credentials]
anthropic_api_key = ""
openai_api_key = ""
```

**Put these in `secrets.toml`, never `config.toml`.** `et config init` scaffolds `secrets.toml` with mode 0600 so only you can read it.

Environment variables `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` still work and override `secrets.toml`.

### `[algorithms.*.*]` — plugin parameters

Every plugin in the ADR 003 registry is configurable via TOML. The table path is `algorithms.<family>.<plugin>`:

```toml
[algorithms.decay.physarum]
active = true
decay_rate = 0.95
source_age_aware = true

[algorithms.activity_score.recency_weighted]
active = true
top_k = 10

[algorithms.resolution]
order = ["sequence_matcher", "embedding_cosine"]

[algorithms.bow_tie.in_out_degree]
active = true
top_k = 10
min_in_degree = 2
min_out_degree = 2
```

The `[algorithms.*]` tree is intentionally **untyped** in the config schema. Plugins register themselves at runtime and validate their own parameters via `AlgorithmMeta.parameters`. This means third-party plugins can add new knobs without modifying the core schema.

To discover what's available: `et catalog` (once wired) or read `docs/ALGORITHMS.md`.

## Environment variables

ET honors two conventions simultaneously:

**New style** (nested, recommended):

```
ET_EXTRACTION__MODEL=claude-opus-4-6
ET_ALGORITHMS__DECAY__PHYSARUM__DECAY_RATE=0.9
```

Each `__` (double underscore) becomes a level of nesting. So `ET_A__B__C` maps to `[a.b] c = ...`.

**Legacy flat names**, preserved for continuity with older `.env` files:

| Env var | Maps to |
|---|---|
| `ANTHROPIC_API_KEY` | `credentials.anthropic_api_key` |
| `OPENAI_API_KEY` | `credentials.openai_api_key` |
| `ET_EXTRACTION_MODEL` | `extraction.model` |
| `ET_EXTRACTION_PROVIDER` | `extraction.provider` |
| `ET_WISDOM_MODEL` | `wisdom.model` |
| `ET_WISDOM_PROVIDER` | `wisdom.provider` |
| `CORS_ORIGINS` | `server.cors_origins` |

When both styles are set for the same key, new-style (`ET_EXTRACTION__MODEL`) wins.

A `.env` file at the repository root is also read (compatibility with the old setup); real environment variables override `.env`.

## Drop-in pattern

Drop-ins solve two problems:

1. **Third-party plugin packages** ship a single TOML file you can place in `conf.d/` to register themselves, without editing `config.toml`.
2. **Local experiments** get their own file you can add or remove without touching your main config.

Example: tuning Physarum decay in a dedicated drop-in.

```toml
# ~/.config/extended-thinking/conf.d/20-physarum-tighter.toml
[algorithms.decay.physarum]
decay_rate = 0.90
```

Lexical ordering means `20-physarum-tighter.toml` beats `10-defaults.toml` but loses to `99-debug.toml`. Prefix numerically by intent: `10-` for baselines, `20-`-`80-` for user customizations, `90+` for experiments.

## Per-project overrides

Drop an `et.toml` at the root of a project and ET will pick it up whenever it's run from anywhere inside that project tree:

```toml
# ./et.toml — only applies when ET runs from inside this project
[extraction]
model = "claude-haiku-4-5-20251001"   # stay cheap here

[algorithms.resolution]
order = ["sequence_matcher"]           # skip embedding-based resolution
```

Use this for project-specific experiments without polluting your global config.

## `et config` reference

| Command | What it does |
|---|---|
| `et config init` | Scaffold `config.toml` + `secrets.toml` (mode 0600) in XDG config dir |
| `et config init --force` | Overwrite existing files |
| `et config path` | Print every config source ET consults, marked `✓` for existing, `·` for missing |
| `et config show` | Print the fully resolved effective config (credentials redacted) |
| `et config show --show-secrets` | Same, but reveal credential values |
| `et config show --format json` | Machine-readable output |
| `et config validate` | Load + validate without running anything. Exit 0 on success, 2 on error |
| `et config get <key>` | Read a single value. Dotted path, e.g. `extraction.model` |
| `et config set <key> <value>` | Write a value. `--scope user` (default) / `project` / `secrets` |
| `et config edit` | Open the target file in `$EDITOR`. `--scope` like `set` |

### Examples

```bash
# Read the current model
et config get extraction.model

# Tune Physarum decay
et config set algorithms.decay.physarum.decay_rate 0.88

# Disable a plugin
et config set algorithms.resolution.embedding_cosine.active false

# Pin a model for just this project (./et.toml)
et config set extraction.model claude-haiku-4-5-20251001 --scope project

# Add an API key (goes to secrets.toml, mode 0600)
et config set credentials.anthropic_api_key sk-... --scope secrets
```

`set` coerces values: `true`/`false` → bool; numeric strings → int or float; comma-separated → list; anything else → string. For complex TOML edits use `et config edit`.

**Secret scope guard.** `et config set credentials.* --scope user` or `--scope project` refuses with exit code 2. Credentials only write to `secrets.toml`. Conversely, `--scope secrets key` refuses anything outside `credentials.*`.

**Post-write validation.** After `set` writes the file, ET reloads the full config to validate. If the resulting tree fails validation, the value is left on disk but ET warns and exits with a nonzero status.

## Gotchas and conventions

- **Comments are allowed in TOML.** ET's default template uses them to document every knob.
- **Paths accept `~`** — they are expanded at load time.
- **Lists are replaced wholesale, not appended.** Setting `scan_paths = ["~/somewhere"]` in a drop-in fully overrides an earlier `scan_paths = ["~/elsewhere"]`. This is deliberate: it avoids surprising accumulation.
- **Unknown top-level keys are rejected** by the Pydantic schema. Typos fail fast with a readable error. The `[algorithms.*]` subtree is the one exception: it's intentionally free-form so plugins validate their own params.
- **Do not check `secrets.toml` into version control.** `et config init` sets mode 0600 for a reason. Add `secrets.toml` to `.gitignore` if you version your dotfiles.
- **Config changes require a restart** of long-running processes (the MCP server, `uvicorn`). There is no hot-reload, by design.

## Extending the system

Writing a plugin that users can configure is straightforward:

1. Register the plugin via `extended_thinking.algorithms.registry.register(YourPlugin)` as usual (see [ADR 003](./ADR/003-pluggable-algorithms.md)).
2. Declare its default parameters in `AlgorithmMeta.parameters`.
3. That's it — users now configure it via `[algorithms.<family>.<your_plugin>] key = value` in their config.toml (or in a drop-in).

Writing a plugin that **adds a new config section** (not under `[algorithms.*]`): edit `config/schema.py` to add the new Pydantic model. Third-party extensions that can't modify the core schema should use the `[algorithms.*]` tree, or ship their own provider under the MemoryProvider protocol.

## Typed nodes & ontologies

Types in ET come from [malleus](../../malleus) (the root ontology) through ET's LinkML (`schema/extended_thinking.yaml`). The ontology is **constitutive** (Architecture A): `GraphStore(db_path, ontology=default_ontology())` won't construct without one. See [ADR 013](./ADR/013-research-backbone-audience.md) v2 for the full rationale.

**Regenerating the schema:**

```bash
make schema-kuzu       # LinkML → Kuzu DDL + typed Python accessors
make schema            # All artifacts (pydantic, JSON, TS, Kuzu)
make schema-check      # CI drift guard — regenerate + diff against commit
```

Generated files live in `schema/generated/` and are committed: `models.py` (Pydantic), `kuzu_ddl.py` (CREATE TABLE statements), `kuzu_types.py` (Pydantic ↔ Kuzu bridge).

**Adding a new node type:**

1. Edit `schema/extended_thinking.yaml`:
   ```yaml
   classes:
     MyThing:
       is_a: Entity            # pick a malleus root
       mixins: [Statusable]    # only what Entity doesn't already provide
       attributes:
         kind: { range: string, required: true }
         ...
   ```
2. `make schema-kuzu` regenerates DDL and typed accessors.
3. Use it: `kg.insert(m.MyThing(id=..., kind=..., ...))`.

**Adding a typed edge:**

```yaml
classes:
  MyEdge:
    is_a: Relation
    slot_usage:
      source_id: { range: MyThing }
      target_id: { range: OtherThing }
    attributes:
      strength: { range: float }
```

Kuzu's binder enforces the `source_id` / `target_id` ranges at write time — a wrong-pair insert is rejected, not silently accepted.

**Consumer-owned schemas:** a separate project (e.g. autoresearch-ET) ships its own LinkML importing malleus, generates its own typed accessors, and merges its ontology onto ET's at boot via `Ontology.merged_with`. See ADR 013 for the flow.

**Namespaces (ADR 013 C2).** Every node and edge carries a `namespace` string column. Memory-pipeline writes default to `"memory"`; `GraphStore.insert` defaults to `"default"` (matches autoresearch-ET's `ETClient`). Queries scope via `list_concepts(namespace=...)` etc. Algorithms scope via `AlgorithmContext.namespace`.

## Troubleshooting

**"config validation failed" when loading ET.** Run `et config validate` to see the full error. Common causes: typo in a key name (top-level keys are strict; check spelling), wrong type (booleans vs strings), invalid TOML syntax (mismatched brackets, unquoted strings).

**A setting I put in `config.toml` is being ignored.** Another tier is overriding it. Run `et config path` to see which tiers are active, then `et config show` to see who won. Environment variables override everything except explicit CLI overrides.

**ET is still reading from `~/.extended-thinking/`.** Either migration hasn't run yet (run anything: `et stats`, or start the MCP server), or your config still points `data.root` at the old location. Check with `et config show | grep root`.

**Secrets end up in shell history / screenshots.** Keep them out of `config.toml` entirely. `et config show` redacts them by default.

**Third-party plugin says "unknown algorithm parameter".** The plugin validates its own params; the core schema doesn't. Check the plugin's docs for the spelling of keys under `[algorithms.<family>.<plugin>]`.

## Related documents

- [ADR 012 — Centralized Configuration](./ADR/012-centralized-config.md) — the design decision for this file
- [ADR 013 — Research-Backbone Audience](./ADR/013-research-backbone-audience.md) — typed-node framework, malleus, programmatic consumers
- [ADR 003 — Pluggable Algorithms](./ADR/003-pluggable-algorithms.md) — how plugins register
- [ADR 004 — Configurable Models](./ADR/004-configurable-models.md) — model/provider selection
- [ADR 010 — Batteries-Included Providers](./ADR/010-batteries-included-providers.md) — provider philosophy
- [malleus KNOWLEDGE_GRAPH_PROTOCOL.md](../../malleus/KNOWLEDGE_GRAPH_PROTOCOL.md) — why the ontology is constitutive (Architecture A)
