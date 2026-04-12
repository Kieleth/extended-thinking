"""Link prediction: suggest edges that should exist but don't.

Two concepts might be semantically about the same thing but live in
different parts of the graph — maybe they came from different sources,
or the user just hasn't made the connection explicit.

Link prediction algorithms find these missing edges. Each plugin defines
its own similarity metric:

  - textual_similarity: SequenceMatcher on name + description (fast, no deps)
  - embedding_similarity: ChromaDB cosine on concept signatures (semantic, needs vectors)
  - graph_structure: Adamic-Adar / common-neighbors (topological, no text needed)

Link prediction outputs candidates. Like DMN recombination, it does NOT
auto-commit edges. Users review candidates and accept the real ones.

Pairs well with `recombination/cross_cluster_grounded`: use link
prediction as a cheap filter (many candidates), then feed top candidates
to DMN recombination for LLM-grounded verdicts.

Research:
  - Adamic & Adar (2003). Friends and neighbors on the web. Social Networks 25(3).
  - Liben-Nowell & Kleinberg (2007). The link-prediction problem for social networks.
  - TransE/RotatE families for embedding-based KG completion (modern deep learning approach).
"""

from extended_thinking.algorithms.link_prediction.textual_similarity import (
    TextualSimilarityLinkPrediction,
)

__all__ = ["TextualSimilarityLinkPrediction"]
