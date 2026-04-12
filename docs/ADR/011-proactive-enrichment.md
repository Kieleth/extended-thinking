# ADR 011: Proactive Knowledge Enrichment

**Status:** Accepted (v2, revised 2026-04-12)
**Date:** 2026-04-12 (original), revised 2026-04-12
**Depends on:** ADR 001 (Pluggable Memory), ADR 002 (Bitemporal Foundation), ADR 003 (Pluggable Algorithms), ADR 012 (Centralized Configuration), **ADR 013 (Research-Backbone Audience) — all nine C's must be shipped before this ADR starts**

## Revision history

**v2 (2026-04-12).** v1 predated ADR 013 Phase 0. It modeled `KnowledgeNode` as a one-off schema addition and described enrichment as if runtime registration were available. That's wrong under the malleus Knowledge Graph Protocol (Architecture A, constitutive).

v2 reframes enrichment as a **consumer of the shipped ADR 013 infrastructure**:
- `KnowledgeNode` is a LinkML class (`is_a: Signal`) declared in ET's schema and codegen'd like every other typed class.
- `Enriches` is a `Relation` subclass. Kuzu's binder enforces its FROM/TO.
- Sources write via `GraphStore.insert` (ADR 013 C1).
- LLM relevance gates cite via `et_write_rationale` (ADR 013 C4) — the grounded-rationale guarantee extends to "why this external content matters."
- Vector-based gates use `find_similar_typed` (ADR 013 C6) for pre-filter.
- Each enrichment source gets its own namespace (`enrichment:wikipedia`, `enrichment:arxiv`, ...) — per-source purge is a namespace op; per-theme filtering is a property op.

## Context

Until now ET reasons only over evidence the user produced. The "extended" in extended-thinking was always meant to be more than that: human knowledge is a field, and a thinking layer that ignores the context around a user's concepts stops short of its own name.

On-demand linking misses the core value. By the time the user knows to ask "show me Wikipedia for X," they've already done the hardest part — noticing that something outside their own head might be relevant. The killer feature is **preemption**: the system fetches context for concepts the user is thinking about before they ask, so the relevant external knowledge is already connected when they need it.

Two concrete UX modes shape the design:

1. **Internal-only.** User opts out; enrichment is fully disabled. Nothing leaves the machine. ET is a closed typed graph. `[enrichment] enabled = false` (default).
2. **Internal + world.** User opts in per-source. Wikipedia fetches concept-adjacent articles. arXiv pulls paper abstracts. Curated canons (Fowler, GoF, CS textbooks) add domain-specific grounding. Each source is a separate toggle, a separate namespace, a separate purge-able unit.

Wikipedia and arXiv are table stakes — every system eventually has them. The differentiator is curated canons plus the discipline to stay proactive without being invasive: configurable triggers, relevance gates with the grounded-rationale guarantee, per-source namespaces for auditability.

## Decision

**Enrichment is a plugin layer built on ADR 013's shipped infrastructure. Every external knowledge item becomes a typed `KnowledgeNode` connected to user concepts via `Enriches` edges. Every knob is a TOML key. The master toggle is `[enrichment] enabled`.**

### LinkML additions

Declared in `schema/extended_thinking.yaml`, regenerated via `make schema-kuzu`:

```yaml
KnowledgeNode:
  is_a: Signal
  mixins: [Describable]
  description: >-
    An external knowledge item (Wikipedia article, arXiv paper, Fowler
    refactoring, etc.) attached to the user's graph. Signal because it
    is a derived quality — the source's representation of a topic —
    inhering in the user concept it enriches.
  attributes:
    source_kind:
      range: string
      required: true
      description: "The canonical source name (e.g. 'wikipedia', 'arxiv', 'fowler')."
    external_id:
      range: string
      required: true
      description: "Source-native id (Wikidata QID, arXiv ID, Fowler slug)."
    url:
      range: string
    title:
      range: string
      required: true
    abstract:
      range: string
      description: "Concise text representation — what gets indexed for semantic recall."
    theme:
      range: string
      description: >-
        JSON array of theme tags. Source plugins emit these when they
        fetch (Wikipedia categories → ET themes; arXiv category IDs
        directly). Lets users filter within a source: "Wikipedia
        articles tagged cs.ai only." Multi-theme membership allowed.

Enriches:
  is_a: Relation
  description: >-
    Connects a user-facing node to external knowledge (ADR 011). FROM
    types span every node kind that benefits from enrichment — memory's
    Concept and research-backbone Hypothesis / Variant / Wisdom / etc.
  slot_usage:
    source_id:
      any_of:
        - range: Concept
        - range: Hypothesis
        - range: Variant
        - range: Wisdom
    target_id:
      range: KnowledgeNode
  attributes:
    relevance:
      range: float
      description: "Final relevance score from the gate sequence (0..1)."
    trigger:
      range: string
      description: "Which trigger fired (frequency_threshold, cluster_formed, ...)."
    gate_verdicts:
      range: string
      description: "JSON array of per-gate verdicts (plugin + score + outcome)."

EnrichmentRun:
  is_a: Event
  mixins: [Describable]
  description: >-
    Telemetry for a single enrichment invocation: which trigger fired,
    which source was asked, how many candidates came back, how the gate
    sequence filtered them, how long it took. Queryable via et_shift so
    the user (or an LLM) can tune triggers/gates/sources based on real
    data rather than vibes. See ADR 011 "Enrichment telemetry" below.
  attributes:
    trigger_name:
      range: string
      required: true
    source_kind:
      range: string
      required: true
    concept_id:
      range: string
      required: true
      description: "The user node whose context fired the enrichment."
    candidates_returned:
      range: integer
    candidates_accepted:
      range: integer
    gate_trace:
      range: string
      description: "JSON: per-gate counts_in, counts_out, score histogram."
    duration_ms:
      range: integer
    error:
      range: string
      description: "Non-empty if the run failed (e.g. Wikipedia 5xx). Enables retry queries."
```

Consumer projects (autoresearch-ET etc.) can extend the FROM side by adding their own class to `Enriches.slot_usage.source_id.any_of` in their own LinkML delta; the codegen produces the matching REL DDL.

`KnowledgeNode` extends `Signal` (malleus) — it has a `bearer_id` (the user concept being enriched), `signal_type` (source kind), `value` (relevance), `algorithm` (the gate sequence or source plugin), and `computed_at` (fetch time) inherited. That aligns with malleus semantics and avoids a parallel timestamp story.

### Per-source namespaces + theme properties

Per-source namespaces are the unit of purge and scope: `enrichment:wikipedia`, `enrichment:arxiv`, `enrichment:fowler_refactorings`, etc. A user who wants to drop Wikipedia keeps arXiv intact.

Theme is a property (JSON array of strings). Wikipedia is humongous; without sub-classification, ingesting enrichment for a CS concept pollutes the graph with biology articles. Source plugins map their native taxonomy to ET theme tags:

- **Wikipedia**: abstract + categories → LLM classifier (Haiku) picks 1-3 theme tags per article. Free-form theme vocabulary — Haiku emits what fits rather than mapping into a fixed enum. Over time a consumer can cluster the accumulated theme set and canonicalize. Classifier is toggleable; `[enrichment.sources.wikipedia] theme_classifier = "llm" | "raw_categories" | "off"`.
- **arXiv**: category IDs directly (`cs.LG`, `stat.ML`, `cs.AI`). No classifier needed — arXiv's taxonomy IS the theme.
- **Fowler refactorings**: category from the catalog (`composing-methods`, `moving-features-between-objects`, etc.). No classifier needed.

Queries filter on both axes:

```python
# All Wikipedia enrichment
kg.list_concepts(namespace="enrichment:wikipedia")

# Wikipedia enrichment tagged as ML/AI
# (via find_similar_typed or property match in et_shift)
```

Multi-theme membership is allowed — one article can carry `["cs.ai", "cs.systems"]`.

### The four plugin families

Same shape as v1 (ADR 003 pattern), adapted to run on the ADR 013 substrate:

```
algorithms/enrichment/
  sources/               # who we fetch from
    wikipedia.py         (MVP)
    arxiv.py             (next)
    semantic_scholar.py
    fowler_refactorings.py
    gof_patterns.py
    custom_canon.py      # folder-of-markdown adapter
  triggers/              # when we fire — the MVP is one strategy, not THE strategy
    on_extract.py        # every new concept (noisy; off by default)
    frequency_threshold.py  # concept.frequency >= N (MVP default)
    cluster_formed.py    # new cluster of size >= M detected
    rising_activation.py # concept entering the top-K active set
    on_wisdom.py         # wisdom generation triggers enrichment of its cited concepts
    scheduled.py         # periodic background tick
    # Future: pagerank_rank, user_marked, interaction_driven.
    # Plugin registry — third parties ship their own with zero core changes.
  relevance_gates/       # does this belong?
    embedding_cosine.py  (MVP; runs against find_similar_typed)
    source_type_match.py
    llm_judge.py         # via et_write_rationale for grounded verdicts
  cache/                 # freshness policy
    time_to_refresh.py   # per-source; default 30d, Wikipedia 90d, arXiv 'never'
    never.py
    on_access.py
```

**Only `frequency_threshold` + `embedding_cosine` + `time_to_refresh` ship as MVP defaults.** Every other trigger/gate/cache is a plugin waiting for its need to materialize. Consumers register additional strategies via the same entry-points mechanism ADR 003 defines.

Every plugin in these families is configurable via `[algorithms.enrichment.<family>.<name>]` in the central TOML config (ADR 012). Default state: every family present, `[enrichment] enabled = false` kills everything.

### Pipeline integration

Enrichment runs at the end of `Pipeline.sync()` after concept extraction and relationship detection. Pseudocode:

```
if not settings.enrichment.enabled:
    return

for concept in newly_extracted_or_updated_concepts:
    for trigger in active_triggers:
        if not trigger.should_fire(concept):
            continue
        for source in active_sources:
            if not source.enabled:
                continue
            candidates = source.search(concept)           # external fetch
            for cand in candidates:
                if cache.hit(cand.external_id) and not cache.stale(cand):
                    continue
                # theme filter (per-source config)
                if source.themes_include and not any(
                    t in source.themes_include for t in cand.themes
                ):
                    continue
                # relevance gate sequence, cheapest first
                verdict = None
                for gate in active_gates:
                    v = gate.judge(concept, cand)
                    if v.rejects:
                        verdict = v
                        break
                    if v.auto_accepts:
                        verdict = v
                        break
                    verdict = v
                if verdict.rejects:
                    continue
                # commit as typed KnowledgeNode + Enriches edge
                kn_id = kg.insert(KnowledgeNode(
                    id=f"kn-{source.kind}-{cand.external_id}",
                    source_kind=source.kind,
                    external_id=cand.external_id,
                    url=cand.url,
                    title=cand.title,
                    abstract=cand.abstract,
                    theme=json.dumps(cand.themes),
                    bearer_id=concept.id,
                    signal_type=source.kind,
                    algorithm=gate_chain_name,
                    value=verdict.score,
                ), namespace=f"enrichment:{source.kind}")
                kg.insert(Enriches(
                    id=f"enr-{uuid()}",
                    source_id=concept.id,
                    target_id=kn_id,
                    relation_type="enriches",
                    relevance=verdict.score,
                    trigger=trigger.name,
                    gate_verdicts=json.dumps(verdict.trace),
                ), namespace=f"enrichment:{source.kind}")
                # optional: if the gate was LLM-based, persist its rationale
                if gate.is_llm_judge and gate.wants_rationale:
                    kg.insert(Rationale(
                        text=verdict.rationale_text,
                        cited_node_ids=json.dumps([concept.id, kn_id]),
                        bearer_id=kn_id,
                        signal_type="enrichment_rationale",
                    ), namespace=f"enrichment:{source.kind}")
```

I/O-bound (HTTP calls out); wrapped in bounded per-source concurrency and a wall-clock budget per sync so a slow canon doesn't hold up the pipeline.

### Enrichment telemetry (stats as first-class data)

Every trigger-fire-per-source writes an `EnrichmentRun` Event carrying
`{trigger_name, source_kind, concept_id, candidates_returned,
candidates_accepted, gate_trace, duration_ms, error}`. This is not
ad-hoc logging — it's queryable state.

Why it matters: thresholds in this ADR (`min_frequency=3`,
`min_similarity=0.72`, cache refresh cadence) are starting defaults.
After real usage the right move is adjusting them based on data, not
opinion. The user or an LLM inspects:

```cypher
MATCH (r:EnrichmentRun) WHERE r.source_kind = 'wikipedia'
  AND r.t_created > $since
RETURN r.trigger_name,
       avg(r.candidates_returned) AS avg_candidates,
       avg(r.candidates_accepted) AS avg_accepted,
       sum(CASE WHEN r.error <> '' THEN 1 ELSE 0 END) AS error_count
```

...and sees "Wikipedia returned 8.4 candidates per fire, embedding gate
accepted 1.2, 4% error rate" — actionable signal to tighten or loosen
the gate, extend cache, or disable a trigger that's too noisy.

Exposed via `et_shift(node_types=["EnrichmentRun"], namespace="enrichment:*")`
and, later, a dedicated `et_extend_stats` tool if users find the
et_shift route too coarse.

### MCP surface

Three tools:

- **`et_extend <concept_id>`** — list `KnowledgeNode`s attached to a concept, optionally filter by `source` or `theme`. Renders source, title, abstract snippet, relevance, url. Built from typed queries on `Enriches` edges (no new storage work).
- **`et_extend_force <concept_id>`** — trigger enrichment on-demand bypassing all triggers. First-time ingestion, testing, and "I really want Wikipedia on this specific concept right now" flows.
- **`et_extend_purge <source_kind>`** — supersede every `KnowledgeNode` and `Enriches` edge in `namespace="enrichment:<source>"`. Bitemporal (not deletion) for auditability. `et_extend_compact <source>` lands later for users who want to genuinely reclaim disk after a grace period.

### Memory-side read surfaces auto-include enrichment

Existing tools scope to memory (`"memory"` namespace) to keep memory-side stats clean, **except** the concept-detail surface `et_explore`, which automatically includes attached `KnowledgeNode`s (across all `enrichment:*` namespaces) when rendering a concept. Enrichment is meant to be seen alongside user concepts — hiding it behind a separate tool defeats the point of making it proactive.

- `et_concepts` → memory-only (unchanged).
- `et_stats` → memory-only (unchanged).
- `et_explore <concept>` → concept + its `Enriches` targets (auto-include).
- `et_shift` → user-scoped by default; pass `namespace="enrichment:wikipedia"` etc. to inspect enrichment activity.

### Privacy & reversibility

- **Only concept name + short description** leave the machine. Never source quotes or conversation text.
- **Master toggle in config** (`[enrichment] enabled = false`). One-line full disable.
- **Per-source toggles** (`[enrichment.sources.wikipedia] enabled = true|false`).
- **Per-theme include/exclude** (`themes_include`, `themes_exclude`) limit what gets fetched within a source.
- **`et_extend_purge <source>`** removes all enrichment from that source. Bitemporal supersession keeps the audit trail.
- **Namespace boundary** (`enrichment:<source>`) is auditable. Users run `et_shift(from, to, namespace="enrichment:wikipedia")` to see exactly what Wikipedia brought into the graph.

## Non-decisions (stand from v1)

- **No full-text ingestion** — abstracts only. Licensing + scale problem.
- **No paid APIs in core** — Semantic Scholar (free), arXiv (free), Wikipedia/Wikidata (free). Paid sources ship as separate packages.
- **No cross-user enrichment** — one user's enrichments are their own.
- **No automatic translation** — English-centric day one. Source plugins declare language.
- **No embedding-space graph structure** — embeddings power gates and recall; actual edges are typed and constitutive.

## Implementation sequence

Prerequisite: ADR 013 all nine C's shipped. ✅ done.

**Phase A — LinkML + infrastructure:**
1. Add `KnowledgeNode` and `Enriches` to `schema/extended_thinking.yaml`. Regenerate.
2. Scaffold `algorithms/enrichment/` — `sources/`, `triggers/`, `relevance_gates/`, `cache/` — with protocol classes and registry wiring. Zero built-in plugins yet.
3. Add the enrichment pipeline step at the end of `Pipeline.sync()`, gated on `[enrichment] enabled`.
4. `[enrichment]` TOML schema in `config/schema.py`.

**Phase B — MVP: Wikipedia:**
5. `algorithms/enrichment/sources/wikipedia.py` — REST API client, article → (title, abstract, url, themes). Theme mapping from Wikipedia categories to ET tags documented in the plugin.
6. `algorithms/enrichment/triggers/frequency_threshold.py` (default on, `min_frequency=3`).
7. `algorithms/enrichment/relevance_gates/embedding_cosine.py` (default on, `min_similarity=0.72`).
8. `algorithms/enrichment/cache/time_to_refresh.py` (default 30d; Wikipedia per-source override 7d).
9. MCP tool `et_extend` + `et_extend_purge`.
10. First dogfood: run against a real concept graph, inspect what Wikipedia brought in.

**Phase C — more canons:**
11. `arxiv.py` — validates the plugin boundary with a different auth pattern and immutable cache policy.
12. `cluster_formed.py` trigger — validates multi-trigger composition.
13. `llm_judge.py` gate — wired to `et_write_rationale` so LLM verdicts land as Rationale nodes.
14. `fowler_refactorings.py` — first curated canon. Validates source plugin shape for pre-bundled corpora.
15. `custom_canon.py` — generic folder-of-markdown adapter.

**Phase D — flip defaults:**
16. Once signal-to-noise is vetted with real usage, consider flipping `[enrichment] enabled = true` in the shipped default config. Until then: opt-in.

Each numbered item ships with tests.

## Consequences

**Positive:**

- Proactive context: opening `et_insight` on a concept surfaces Wikipedia grounding, relevant papers, pattern catalog entries already attached.
- Differentiated value from curated canons — Wikipedia is table stakes, a Fowler catalog wired into the user's graph is not.
- Graph semantics for external knowledge. Cross-canon paths traversable via `et_path`.
- Uses only shipped ADR 013 machinery — no new primitives, no parallel write paths, no forked schemas.
- Per-source namespaces give clean purge, clean scope, clean audit.
- Internal-only and internal+world modes are both first-class.

**Negative:**

- I/O cost. Rate limiting, retry, bounded concurrency needed.
- Relevance gates are fallible. False positives attach noise; false negatives miss connections. Tunable thresholds help, no silver bullet.
- Storage growth. `KnowledgeNode`s and abstracts accumulate. Per-source `max_per_concept` caps + bitemporal supersession mitigate.
- Canon maintenance. A bundled Fowler catalog is a snapshot; new editions need plugin updates.
- Privacy surface — even sending a concept name externally is a privacy event. Opt-in by default config + `et_extend_purge` are the mitigations.

## Open questions

- **Wikipedia category → ET theme mapping** is source-specific. Shipping a default mapping for the top ~200 Wikipedia categories is reasonable; letting users override via config (`[algorithms.enrichment.sources.wikipedia.theme_mapping]`) is a natural extension if the default proves too narrow.
- **Cache refresh scheduling.** On-access vs. scheduled sweep. MVP: on-access check against `cache.refresh_after_days`. Scheduled sweep follows if users find stale abstracts in practice.
- **Gate-chain verdict schema.** Each gate produces `{name, score, outcome: accept|reject|auto_accept, reason: str}`. Stored as JSON in `Enriches.gate_verdicts` for inspection. Keep schema lightweight; consumers who want structured verdicts can query the Rationale nodes.

## References

- ADR 013 v2 — every capability this ADR depends on
- ADR 012 — central config for every knob
- `docs/research-backbone.md` — consumer primer that enrichment extends
- Wikidata: https://www.wikidata.org/
- arXiv API: https://info.arxiv.org/help/api/
- Wikipedia REST: https://en.wikipedia.org/api/rest_v1/
- Fowler, M. *Refactoring: Improving the Design of Existing Code* (2nd ed., 2018)
- Beaty, R. et al. (2024) — Default Mode Network / cognitive basis for cross-cluster fetching
