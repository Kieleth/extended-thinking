"""Memory providers — pluggable adapters for any memory system.

Usage:
    from extended_thinking.providers import get_provider

    provider = get_provider({"provider": "auto"})
    chunks = provider.get_recent(limit=20)
"""

from __future__ import annotations

from pathlib import Path

from extended_thinking.providers.protocol import Entity, Fact, KnowledgeGraphView, MemoryChunk, MemoryProvider

__all__ = ["Entity", "Fact", "KnowledgeGraphView", "MemoryChunk", "MemoryProvider", "get_provider"]


def get_provider(config: dict | None = None) -> MemoryProvider:
    """Instantiate a MemoryProvider from configuration.

    Config keys:
        provider: "auto" | "folder" | "claude-code" | "mempalace" | "mem0" | "graphiti" (default: "auto")
        path: directory path (for folder and claude-code providers)
        home_dir: override home directory (for auto provider, testing)
        user_id: Mem0 user scope (required for mem0 provider)
        uri / user / password / group_id: Graphiti Neo4j connection (graphiti provider)

    Raises:
        ValueError: if the provider name is unknown or required config is missing.
    """
    config = config or {}
    provider_name = config.get("provider", "auto")

    if provider_name == "auto":
        from extended_thinking.providers.auto import AutoProvider
        home_dir = Path(config["home_dir"]) if "home_dir" in config else None
        return AutoProvider(home_dir=home_dir)

    if provider_name == "folder":
        from extended_thinking.providers.folder import FolderProvider
        path = Path(config.get("path", "."))
        return FolderProvider(path)

    if provider_name == "claude-code":
        from extended_thinking.providers.claude_code import ClaudeCodeProvider
        path = Path(config["path"]) if "path" in config else None
        return ClaudeCodeProvider(projects_dir=path)

    if provider_name == "mempalace":
        from extended_thinking.providers.mempalace import MemPalaceProvider
        path = Path(config["path"]) if "path" in config else None
        return MemPalaceProvider(palace_dir=path)

    if provider_name == "mem0":
        from extended_thinking.providers.mem0 import Mem0Provider
        user_id = config.get("user_id")
        if not user_id:
            raise ValueError("mem0 provider requires config['user_id']")
        return Mem0Provider(user_id=user_id, config=config.get("mem0_config"))

    if provider_name == "graphiti":
        from extended_thinking.providers.graphiti import GraphitiProvider
        return GraphitiProvider(
            uri=config.get("uri", "bolt://localhost:7687"),
            user=config.get("user", "neo4j"),
            password=config.get("password", "neo4j"),
            group_id=config.get("group_id"),
        )

    raise ValueError(
        f"Unknown provider: {provider_name}. "
        "Available: auto, folder, claude-code, mempalace, mem0, graphiti"
    )
