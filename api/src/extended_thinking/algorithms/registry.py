"""Algorithm registry (ADR 003).

Algorithms register at import time (via the plugin's module-level code)
or explicitly via register(). The registry is a flat mapping keyed by
algorithm name, segmented by family.

Config-driven selection: get_active(family, config) returns the
instantiated algorithms the user has enabled for that family.
"""

from __future__ import annotations

import logging
from typing import Any

from extended_thinking.algorithms.protocol import Algorithm, AlgorithmMeta

logger = logging.getLogger(__name__)

_registry: dict[str, type[Algorithm]] = {}


def register(alg_cls: type[Algorithm]) -> None:
    """Register an algorithm class. Called at module import time by built-ins
    and by third-party packages that ship plugins.

    Name collisions overwrite silently with a warning — later registrations
    win. Third parties should use distinctive names (e.g. `my_company.bow_tie`).
    """
    meta = getattr(alg_cls, "meta", None)
    if meta is None or not isinstance(meta, AlgorithmMeta):
        raise TypeError(f"{alg_cls.__name__} must define a class-level `meta: AlgorithmMeta`")
    if meta.name in _registry:
        logger.warning("Algorithm name collision: '%s' re-registered", meta.name)
    _registry[meta.name] = alg_cls
    logger.debug("Registered algorithm: %s (%s)", meta.name, meta.family)


def list_available(family: str | None = None) -> list[AlgorithmMeta]:
    """All registered algorithms, optionally filtered by family.

    Used by `et_catalog` MCP tool and documentation generation.
    """
    metas = [cls.meta for cls in _registry.values()]
    if family:
        metas = [m for m in metas if m.family == family]
    return sorted(metas, key=lambda m: (m.family, m.name))


def get_active(family: str, config: dict | None = None) -> list[Algorithm]:
    """Return instantiated algorithms for a family, per user config.

    Config shape:
      {"algorithms": {"decay": ["physarum"], "bow_tie": ["in_out_degree"]},
       "parameters": {"physarum": {"decay_rate": 0.95}}}

    If no config, returns all registered algorithms in the family (auto-enable).
    This lets the system work out of the box without user configuration.
    """
    config = config or {}
    enabled = config.get("algorithms", {}).get(family)

    if enabled is None:
        # Default: every registered algorithm in this family
        candidates = [cls for cls in _registry.values() if cls.meta.family == family]
    else:
        candidates = [_registry[name] for name in enabled if name in _registry]

    params_by_name = config.get("parameters", {})
    instances = []
    for cls in candidates:
        default_params = dict(cls.meta.parameters)
        default_params.update(params_by_name.get(cls.meta.name, {}))
        instance = cls(**default_params) if default_params else cls()
        instances.append(instance)
    return instances


def build_config_from_settings(algorithms_tree: dict | None) -> dict:
    """Translate TOML `[algorithms.*.*]` into get_active()'s config shape.

    The TOML layout is family-first, plugin-first:

        [algorithms.decay.physarum]
        active = true
        decay_rate = 0.95

        [algorithms.resolution]
        order = ["sequence_matcher", "embedding_cosine"]

    get_active() expects:

        {
          "algorithms": {"decay": ["physarum"], "resolution": ["..."]},
          "parameters": {"physarum": {"decay_rate": 0.95}},
        }

    Rules applied per family:
      - If the family table has an "order" key → use that list of names.
      - Else iterate plugin sub-tables: include each that doesn't set
        `active = false` (default behavior: opted in by being listed).
      - Per-plugin params are every key on the plugin table minus "active".

    Returns the registry-shaped config (may be empty for unconfigured
    families, in which case get_active() auto-selects all registered ones).
    """
    if not algorithms_tree:
        return {}

    enabled_by_family: dict[str, list[str]] = {}
    params_by_plugin: dict[str, dict] = {}

    for family, family_tbl in algorithms_tree.items():
        if not isinstance(family_tbl, dict):
            continue

        # Family-level `order` wins if present.
        order = family_tbl.get("order")
        if isinstance(order, list) and all(isinstance(n, str) for n in order):
            enabled_by_family[family] = list(order)

        names_from_subtables: list[str] = []
        for name, plugin_tbl in family_tbl.items():
            if name == "order" or not isinstance(plugin_tbl, dict):
                continue
            active = plugin_tbl.get("active", True)
            if active is False:
                continue
            names_from_subtables.append(name)
            params = {k: v for k, v in plugin_tbl.items() if k != "active"}
            if params:
                params_by_plugin[name] = params

        if family not in enabled_by_family:
            # Always emit the family key if the user configured it (even if
            # the list is empty). An explicit empty list tells get_active()
            # "user disabled all plugins here" — different from "user didn't
            # mention this family" which auto-enables everything registered.
            enabled_by_family[family] = names_from_subtables

    return {"algorithms": enabled_by_family, "parameters": params_by_plugin}


def get_by_name(name: str, config: dict | None = None) -> Algorithm | None:
    """Get a single algorithm by name, instantiated with its params."""
    cls = _registry.get(name)
    if cls is None:
        return None
    config = config or {}
    params_by_name = config.get("parameters", {})
    default_params = dict(cls.meta.parameters)
    default_params.update(params_by_name.get(name, {}))
    return cls(**default_params) if default_params else cls()
