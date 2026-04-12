"""Cross-cluster grounded recombination (DMN-inspired serendipity).

Algorithm:
  1. Enumerate clusters (connected components).
  2. Sample N pairs of (concept_a, concept_b) from DIFFERENT clusters.
     Bias sampling toward high-in-degree concepts (well-grounded).
  3. For each pair, gather context: source chunks, source types,
     cluster neighbors.
  4. Ask a strong reasoning model: "Is there a real bridge here?
     If yes, describe the mechanism. If speculative, describe what
     would need to exist. If no connection, say so."
  5. Return candidates, ranked by verdict (grounded > speculative >
     no_connection), then by confidence.

Output is candidates, not committed edges. User decides via
`et_insight_accept` / `et_insight_reject` (future) what to incorporate.

Reference:
  Beaty et al. (2024). Brain networks underlying novel metaphor
    production. Brain 147(10).
  Comm Bio 2025: DMN-ECN dynamic switching correlates with creativity
    across 2,433 participants.
  Granovetter, M. (1973). The strength of weak ties. AJS 78(6):1360.
  RecSys 2024: Serendipity = relevant + novel + unexpected; weak ties
    are the strongest predictor.
"""

from __future__ import annotations

import json
import logging
import random
from typing import Any

from extended_thinking.algorithms.protocol import (
    Algorithm,
    AlgorithmContext,
    AlgorithmMeta,
)
from extended_thinking.algorithms.registry import register

logger = logging.getLogger(__name__)


RECOMBINATION_PROMPT = """\
You are the Default Mode Network of someone's knowledge graph: a background
recombiner looking for meaningful cross-domain connections. You see two concepts
that currently live in DIFFERENT clusters of their thinking (no path connects them).

Your job is to decide whether there's a real, grounded bridge — or if the
distance between them is principled.

## Concept A: {concept_a_name} ({concept_a_category})
Description: {concept_a_description}
Source quote: "{concept_a_quote}"
Sources: {concept_a_sources}
Cluster neighbors: {concept_a_neighbors}

## Concept B: {concept_b_name} ({concept_b_category})
Description: {concept_b_description}
Source quote: "{concept_b_quote}"
Sources: {concept_b_sources}
Cluster neighbors: {concept_b_neighbors}

## Decide

Three possible verdicts:

**"grounded"** — A and B genuinely relate via a mechanism that exists in reality.
Describe the specific mechanism. Reference source paths where the evidence lies.
The bridge is real, not wished into existence.

**"speculative"** — A and B COULD connect, but would require a specific missing
piece to become real. Name the missing piece. Example: "To connect [A] from
Silk's CRDT layer with [B] from Malleus ontology specs, you'd need a
cross-boundary schema contract — which doesn't exist today."

**"no_connection"** — The distance is principled. These concepts live in
different cognitive territories that don't genuinely meet. That's a valid
finding; don't force it.

Rules:
- Use concept names verbatim.
- If you cite a source path, use the actual path string shown above.
- Do not invent systems, mechanisms, or features that are not in the context.
- Confidence is your honest estimate (0.0 to 1.0). Err lower, not higher.

Return JSON only:
```json
{{
  "verdict": "grounded" | "speculative" | "no_connection",
  "bridge": "One clear sentence describing the connection or its absence.",
  "mechanism": "For 'grounded' verdicts: the real mechanism. Empty otherwise.",
  "requires": "For 'speculative' verdicts: what would need to exist. Empty otherwise.",
  "confidence": 0.0
}}
```
"""


class CrossClusterGroundedRecombination:
    """DMN-inspired: sample cross-cluster pairs, ask LLM for grounded verdicts.

    This is expensive (one LLM call per candidate). Default 3 candidates.
    """

    meta = AlgorithmMeta(
        name="cross_cluster_grounded",
        family="recombination",
        description="Cross-cluster candidate connections with LLM-judged grounding (DMN pattern)",
        paper_citation="Beaty et al. 2024, Brain 147(10). Comm Bio 2025 (DMN-ECN switching). Granovetter 1973, AJS 78(6).",
        parameters={
            "candidates_per_run": 3,       # number of cross-cluster pairs to evaluate
            "min_in_degree": 2,            # bias toward well-grounded concepts
            "max_neighbors_shown": 5,      # how many cluster-neighbors to show LLM
            "random_seed": None,           # for reproducible tests
        },
        temporal_aware=True,
    )

    def __init__(self, candidates_per_run: int = 3, min_in_degree: int = 2,
                 max_neighbors_shown: int = 5, random_seed: int | None = None):
        self.candidates_per_run = candidates_per_run
        self.min_in_degree = min_in_degree
        self.max_neighbors_shown = max_neighbors_shown
        self._rng = random.Random(random_seed) if random_seed is not None else random.Random()

    def run(self, context: AlgorithmContext) -> list[dict]:
        """Return a list of candidate cross-cluster bridges with verdicts.

        Each result:
          {"from": {...}, "to": {...}, "verdict": "grounded"|"speculative"|"no_connection",
           "bridge": str, "mechanism": str, "requires": str, "confidence": float}
        """
        kg = context.kg

        overview = kg.get_graph_overview() if hasattr(kg, "get_graph_overview") else {}
        clusters = overview.get("clusters", [])
        if len(clusters) < 2:
            logger.info("DMN recombination: need >=2 clusters, have %d", len(clusters))
            return []

        pairs = self._sample_cross_cluster_pairs(kg, clusters)
        if not pairs:
            return []

        llm_caller = context.params.get("llm_caller")
        if llm_caller is None:
            logger.warning("DMN recombination: no llm_caller in context.params, cannot judge.")
            return [self._candidate_without_llm(a, b) for a, b in pairs]

        results = []
        for concept_a, concept_b in pairs:
            verdict = self._ask_llm(llm_caller, kg, concept_a, concept_b)
            if verdict is None:
                continue
            results.append(verdict)

        # Rank: grounded first, then speculative, then no_connection.
        verdict_order = {"grounded": 0, "speculative": 1, "no_connection": 2}
        results.sort(key=lambda r: (verdict_order.get(r.get("verdict", "no_connection"), 3),
                                    -r.get("confidence", 0.0)))
        return results

    def _sample_cross_cluster_pairs(self, kg, clusters: list[dict]) -> list[tuple[dict, dict]]:
        """Pick concept pairs from different clusters, biased toward grounded ones."""
        valid_clusters = [c for c in clusters if c.get("concepts")]
        if len(valid_clusters) < 2:
            return []

        pairs = []
        attempts = 0
        max_attempts = self.candidates_per_run * 10
        seen_pairs: set[tuple[str, str]] = set()

        while len(pairs) < self.candidates_per_run and attempts < max_attempts:
            attempts += 1
            c1, c2 = self._rng.sample(valid_clusters, 2)
            a = self._pick_weighted_concept(kg, c1["concepts"])
            b = self._pick_weighted_concept(kg, c2["concepts"])
            if a is None or b is None:
                continue
            pair_key = tuple(sorted([a["id"], b["id"]]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            pairs.append((a, b))

        return pairs

    def _pick_weighted_concept(self, kg, concepts: list[dict]) -> dict | None:
        """Bias selection toward concepts with many source chunks (well-grounded).

        Falls back to uniform random if provenance info unavailable.
        """
        if not concepts:
            return None

        weighted: list[tuple[dict, int]] = []
        for c in concepts:
            if hasattr(kg, "get_provenance"):
                prov = kg.get_provenance(c["id"])
                weight = max(1, len(prov))
            else:
                weight = max(1, c.get("frequency", 1))
            weighted.append((c, weight))

        total = sum(w for _, w in weighted)
        pick = self._rng.uniform(0, total)
        running = 0
        for c, w in weighted:
            running += w
            if running >= pick:
                return c
        return weighted[-1][0]

    def _gather_context(self, kg, concept: dict) -> dict:
        """Collect source paths, cluster neighbors, and source quote for the LLM."""
        sources: list[str] = []
        if hasattr(kg, "get_concept_sources"):
            for s in kg.get_concept_sources(concept["id"]):
                entry = f"{s.get('source_type', 'unknown')}: {s.get('source', '?')}"
                if entry not in sources:
                    sources.append(entry)

        neighbors: list[str] = []
        if hasattr(kg, "get_relationships"):
            for r in kg.get_relationships(concept["id"])[:self.max_neighbors_shown]:
                other_id = r["target_id"] if r.get("source_id") == concept["id"] else r["source_id"]
                if hasattr(kg, "get_concept"):
                    other = kg.get_concept(other_id)
                    if other:
                        neighbors.append(other["name"])

        return {
            "sources": sources[:5] if sources else ["(no source info)"],
            "neighbors": neighbors if neighbors else ["(no cluster neighbors)"],
        }

    def _ask_llm(self, llm_caller, kg, concept_a: dict, concept_b: dict) -> dict | None:
        """Call the LLM with structured context, parse the verdict."""
        ctx_a = self._gather_context(kg, concept_a)
        ctx_b = self._gather_context(kg, concept_b)

        prompt = RECOMBINATION_PROMPT.format(
            concept_a_name=concept_a["name"],
            concept_a_category=concept_a.get("category", ""),
            concept_a_description=concept_a.get("description", "")[:300],
            concept_a_quote=concept_a.get("source_quote", "")[:200],
            concept_a_sources="; ".join(ctx_a["sources"]),
            concept_a_neighbors=", ".join(ctx_a["neighbors"]),
            concept_b_name=concept_b["name"],
            concept_b_category=concept_b.get("category", ""),
            concept_b_description=concept_b.get("description", "")[:300],
            concept_b_quote=concept_b.get("source_quote", "")[:200],
            concept_b_sources="; ".join(ctx_b["sources"]),
            concept_b_neighbors=", ".join(ctx_b["neighbors"]),
        )

        try:
            response = llm_caller(prompt)
        except Exception as e:
            logger.error("Recombination LLM call failed: %s", e)
            return None

        parsed = _parse_verdict(response)
        if parsed is None:
            return None

        return {
            "from": {"id": concept_a["id"], "name": concept_a["name"]},
            "to": {"id": concept_b["id"], "name": concept_b["name"]},
            "verdict": parsed.get("verdict", "no_connection"),
            "bridge": parsed.get("bridge", ""),
            "mechanism": parsed.get("mechanism", ""),
            "requires": parsed.get("requires", ""),
            "confidence": float(parsed.get("confidence", 0.0)),
        }

    def _candidate_without_llm(self, concept_a: dict, concept_b: dict) -> dict:
        """Stub result when no LLM caller provided — used for graph-only sampling."""
        return {
            "from": {"id": concept_a["id"], "name": concept_a["name"]},
            "to": {"id": concept_b["id"], "name": concept_b["name"]},
            "verdict": "unevaluated",
            "bridge": "",
            "mechanism": "",
            "requires": "LLM caller not provided; cannot judge grounding.",
            "confidence": 0.0,
        }


def _parse_verdict(response: str) -> dict | None:
    """Parse LLM JSON response. Tolerant of markdown fences and minor noise."""
    text = response.strip()
    # Strip code fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Failed to parse recombination verdict")
                return None
        else:
            return None

    if not isinstance(data, dict):
        return None

    valid_verdicts = {"grounded", "speculative", "no_connection"}
    verdict = data.get("verdict", "").strip().lower()
    if verdict not in valid_verdicts:
        return None

    return data


register(CrossClusterGroundedRecombination)
