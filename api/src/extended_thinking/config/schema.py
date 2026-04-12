"""Typed configuration schema (ADR 012).

Shape matches the TOML config file 1:1. The root `Settings` model is what
`load_settings()` returns. Legacy flat attrs (`settings.anthropic_api_key`
etc.) are preserved as compatibility properties that reach into the nested
fields so existing callers keep working.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from extended_thinking.config.paths import default_data_root


def _expand(p: Path | str) -> Path:
    return Path(p).expanduser()


class DataConfig(BaseModel):
    """Where ET stores its own artifacts (KG, vectors, insights).

    Default follows the XDG Base Directory Specification:
    `$XDG_DATA_HOME/extended-thinking` (typically `~/.local/share/extended-thinking`).

    If a pre-XDG `~/.extended-thinking/` exists from before ADR 012, the
    loader performs a one-time move via `migrate_data_dir()`.
    """
    model_config = ConfigDict(extra="forbid")

    root: Path = Field(default_factory=default_data_root)


class ClaudeCodeProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    projects_dir: Path = Field(default_factory=lambda: _expand("~/.claude/projects"))


class ChatgptExportProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    scan_paths: list[Path] = Field(default_factory=list)


class CopilotChatProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class CursorProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class FolderProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    paths: list[Path] = Field(default_factory=list)


class GenericOpenaiChatProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class MempalaceProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claude_code: ClaudeCodeProviderConfig = Field(default_factory=ClaudeCodeProviderConfig)
    chatgpt_export: ChatgptExportProviderConfig = Field(default_factory=ChatgptExportProviderConfig)
    copilot_chat: CopilotChatProviderConfig = Field(default_factory=CopilotChatProviderConfig)
    cursor: CursorProviderConfig = Field(default_factory=CursorProviderConfig)
    folder: FolderProviderConfig = Field(default_factory=FolderProviderConfig)
    generic_openai_chat: GenericOpenaiChatProviderConfig = Field(default_factory=GenericOpenaiChatProviderConfig)
    mempalace: MempalaceProviderConfig = Field(default_factory=MempalaceProviderConfig)


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = ""  # empty = auto-detect
    model: str = "claude-haiku-4-5-20251001"


class WisdomConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = ""
    model: str = "claude-opus-4-6"


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cors_origins: str = "http://localhost:3000"


class SilkConfig(BaseModel):
    """Legacy Silk graph store. Kept only for the FastAPI routes that still
    reference it; new code path is Kuzu GraphStore under data.root."""
    model_config = ConfigDict(extra="forbid")
    instance_id: str = "extended-thinking-01"


class CredentialsConfig(BaseModel):
    """Secrets. Belongs in secrets.toml or env, never in config.toml."""
    model_config = ConfigDict(extra="forbid")
    anthropic_api_key: str = ""
    openai_api_key: str = ""


class EnrichmentConfig(BaseModel):
    """Proactive-enrichment master toggle (ADR 011 v2).

    Per-plugin parameters (sources, triggers, gates, cache) live under
    `[algorithms.enrichment.<family>.<plugin>]` tables in TOML and are
    parsed as part of the free-form `Settings.algorithms` tree. This
    block holds only the top-level toggle plus cross-cutting defaults.

    Default: disabled. Internal-only UX mode. Flip `enabled = true` to
    allow the runner to fetch. Individual sources still need their own
    `[algorithms.enrichment.sources.<name>] active = true`.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    """Master toggle. When False, the runner is never invoked and no
    external calls are made, regardless of per-plugin settings."""

    concept_namespace: str = "memory"
    """The namespace whose concepts trigger enrichment. Usually 'memory'
    so enrichment watches the user's thinking graph. Set to a
    consumer namespace (e.g. 'research') to enrich autoresearch-ET
    hypotheses instead."""

    max_runs_per_sync: int = 100
    """Cap on EnrichmentRun telemetry nodes per Pipeline.sync() call.
    Prevents a pathological trigger from creating thousands of runs
    per invocation before someone notices."""


class Settings(BaseModel):
    """Root configuration. Free-form `algorithms` table holds plugin params
    (`[algorithms.<family>.<plugin>]` tables in TOML) — it's untyped here
    because plugins register themselves dynamically and validate their own
    params via `AlgorithmMeta.parameters`.
    """
    model_config = ConfigDict(extra="forbid")

    data: DataConfig = Field(default_factory=DataConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    wisdom: WisdomConfig = Field(default_factory=WisdomConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    silk: SilkConfig = Field(default_factory=SilkConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    algorithms: dict[str, dict] = Field(default_factory=dict)

    # ── Legacy compatibility properties ──────────────────────────────
    # Existing call sites read `settings.anthropic_api_key` etc. Keep them
    # working by forwarding to nested fields. New code should reach into
    # the nested structure directly.

    @property
    def anthropic_api_key(self) -> str:
        return self.credentials.anthropic_api_key

    @property
    def openai_api_key(self) -> str:
        return self.credentials.openai_api_key

    @property
    def extraction_provider(self) -> str:
        return self.extraction.provider

    @property
    def extraction_model(self) -> str:
        return self.extraction.model

    @property
    def wisdom_provider(self) -> str:
        return self.wisdom.provider

    @property
    def wisdom_model(self) -> str:
        return self.wisdom.model

    @property
    def cors_origins(self) -> str:
        return self.server.cors_origins

    @property
    def silk_instance_id(self) -> str:
        return self.silk.instance_id

    @property
    def silk_data_dir(self) -> Path:
        return self.data.root / "silk"

    @property
    def silk_store_path(self) -> str:
        p = self.silk_data_dir
        p.mkdir(parents=True, exist_ok=True)
        return str(p / "graph.redb")
