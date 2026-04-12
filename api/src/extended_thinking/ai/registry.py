"""AI provider registry — auto-registers based on env vars."""

import logging

from extended_thinking.ai.base import AIProvider
from extended_thinking.config import settings

logger = logging.getLogger(__name__)

_providers: dict[str, AIProvider] = {}


def _init_providers() -> None:
    """Initialize providers based on available API keys."""
    if settings.anthropic_api_key:
        from extended_thinking.ai.anthropic import AnthropicProvider
        _providers["anthropic"] = AnthropicProvider(settings.anthropic_api_key)
        logger.info("Registered Anthropic provider")

    if settings.openai_api_key:
        from extended_thinking.ai.openai import OpenAIProvider
        _providers["openai"] = OpenAIProvider(settings.openai_api_key)
        logger.info("Registered OpenAI provider")


def get_provider(name: str | None = None) -> AIProvider:
    """Get a provider by name, or the first available one."""
    if not _providers:
        _init_providers()

    if not _providers:
        raise RuntimeError("No AI providers configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")

    if name:
        if name not in _providers:
            raise KeyError(f"Provider '{name}' not configured. Available: {list(_providers.keys())}")
        return _providers[name]

    # Return first available (prefer anthropic)
    for pref in ("anthropic", "openai"):
        if pref in _providers:
            return _providers[pref]

    return next(iter(_providers.values()))


def list_providers() -> list[dict]:
    """List all configured providers and their models."""
    if not _providers:
        _init_providers()

    return [
        {"name": p.name, "models": p.list_models()}
        for p in _providers.values()
    ]
