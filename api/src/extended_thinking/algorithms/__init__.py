"""Pluggable algorithms at the K+W layer (ADR 003).

Each algorithm is a class implementing the Algorithm protocol, registered
by name, discoverable via metadata, and configurable at runtime.

Structure:
    algorithms/
      protocol.py         # Algorithm protocol + AlgorithmContext + result types
      registry.py         # registration and lookup
      decay/              # edge weight decay
      activation/         # spreading activation variants
      resolution/         # entity matching/merging
      bridges/            # rich-club hub detection
      bow_tie/            # core concept identification
      recombination/      # DMN-inspired serendipity

Third parties ship algorithms via:
  - direct import + `registry.register(YourAlgo)`
  - pip package with `extended_thinking.algorithms` entry point
"""

from extended_thinking.algorithms.protocol import (
    Algorithm,
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import (
    build_config_from_settings,
    get_active,
    get_by_name,
    list_available,
    register,
)

__all__ = [
    "Algorithm",
    "AlgorithmContext",
    "AlgorithmMeta",
    "register",
    "list_available",
    "get_active",
    "get_by_name",
    "build_config_from_settings",
]

# Built-in algorithms auto-register at import time
from extended_thinking.algorithms.decay import physarum  # noqa: F401, E402
from extended_thinking.algorithms.bow_tie import in_out_degree  # noqa: F401, E402
from extended_thinking.algorithms.recombination import cross_cluster_grounded  # noqa: F401, E402
from extended_thinking.algorithms.link_prediction import textual_similarity  # noqa: F401, E402
from extended_thinking.algorithms.resolution import sequence_matcher  # noqa: F401, E402
from extended_thinking.algorithms.resolution import embedding_cosine  # noqa: F401, E402
from extended_thinking.algorithms.activation import weighted_bfs  # noqa: F401, E402
from extended_thinking.algorithms.bridges import top_percentile  # noqa: F401, E402
from extended_thinking.algorithms.link_prediction import embedding_similarity  # noqa: F401, E402
from extended_thinking.algorithms.activity_score import recency_weighted  # noqa: F401, E402

# ADR 011 v2 enrichment plugins (Phase B MVP).
# Each registers itself on import; `[enrichment] enabled = false` by default
# keeps them inert until the user opts in.
from extended_thinking.algorithms.enrichment.triggers import frequency_threshold  # noqa: F401, E402
from extended_thinking.algorithms.enrichment.relevance_gates import embedding_cosine as _enrichment_embedding_cosine  # noqa: F401, E402
from extended_thinking.algorithms.enrichment.cache import time_to_refresh  # noqa: F401, E402
from extended_thinking.algorithms.enrichment.sources import wikipedia  # noqa: F401, E402
