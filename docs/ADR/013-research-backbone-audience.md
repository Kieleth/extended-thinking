# ADR 013: Research-Backbone Audience & Typed-Node Framework

**Status:** Accepted (v2, revised 2026-04-12)
**Date:** 2026-04-12 (original) · revised 2026-04-12
**Depends on:** ADR 001 (Pluggable Memory), ADR 002 (Bitemporal Foundation), ADR 003 (Pluggable Algorithms), ADR 010 (Batteries-Included Providers), ADR 012 (Centralized Configuration), **[malleus](../../../malleus) root ontology** (constructor-level dependency)
**Reshapes:** ADR 011 (Proactive Enrichment) — `KnowledgeNode` becomes a typed node declared in the LinkML ontology, not a one-off schema addition. ADR 011 should be rewritten on top of 013 before its Phase B ships.

## Revision history

**v2 (2026-04-12).** v1 described typed nodes as runtime-registered via `GraphStore.register_node_type(...)`. That posture is incompatible with the malleus [Knowledge Graph Protocol](../../../malleus/KNOWLEDGE_GRAPH_PROTOCOL.md): the ontology is *constitutive*, not *descriptive*. Architecture A requires the ontology to be a constructor parameter, not loaded later, with writes validated as preconditions rather than post-hoc checks.

v2 commits to malleus as ET's root ontology — the same choice every other project in this author's portfolio (Shelob, Logomancers, Logosphere, Kieleth, MuevElCulo) already makes. Typed nodes are declared in LinkML, codegen produces Kuzu DDL + typed Python accessors at **build time**, and `GraphStore.__init__` takes the resulting `Ontology` as a constructor argument. The v1 runtime-registration API is gone.

## Context

Extended-thinking was built with one audience: humans synthesizing their own memory. Ingest conversations and notes, extract concepts, generate wisdom with evidence trails. The API, the MCP surface, and the async processing model all reflect that audience.

A second audience has appeared and is already writing code against ET:  **programmatic consumers that need a typed, bitemporal, queryable state store with algorithm hooks**. The driving concrete case is [autoresearch-ET](../../autoresearch-ET) — an LLM-driven research loop that models Hypothesis, Variant, Run, Metric, AuditEvent and needs to record typed state with full provenance, run ET's algorithms against scoped slices of that state, and retrieve similar prior runs by vector. autoresearch-ET is actively blocked on ET exposing this surface.

The requirements for this audience were captured in [`docs/research-backbone-requirements.md`](../research-backbone-requirements.md). autoresearch-ET's `ETClient` Protocol ([src/autoresearch_et/et_client.py](../../../autoresearch-ET/src/autoresearch_et/et_client.py)) is the concrete contract they need. This ADR accepts that audience as first-class and commits ET to supplying the substrate they depend on.

The pattern generalizes beyond autoresearch-ET. A workflow engine modeling Job/Step/Artifact, a typed archive of experiment data, an evaluation harness, all need the same shape: typed nodes, namespace isolation, synchronous writes, bitemporal queries, algorithm exposure, grounded LLM writes. ET either serves this pattern or becomes a dead-end for programmatic consumers.

## Decision

**Commit to a second audience: programmatic consumers of ET as a typed, bitemporal, queryable state store. Formalize the contract via nine additive capabilities. Keep consumer logic (loop control, cost, decisions) outside ET.**

### Guiding principle

**ET is the substrate. The brain lives outside.** ET stores typed state, tracks bitemporal evolution, runs pluggable algorithms, refuses ungrounded writes. Loop control, decision logic, cost functions, and domain generators live in the consumer. Every capability below is about making ET a better substrate, not pulling consumer logic into ET.

### The nine capabilities

#### C1 — Typed node framework, ontology-driven (R1)

The type system is declared in LinkML, imported from malleus, and materialized at build time into Kuzu DDL + typed Python accessors. No runtime registration.

**Constructor-level dependency.** `GraphStore(db_path, ontology=et_ontology)`. The ontology parameter is required in spirit (defaults to ET's canonical ontology). Without it, GraphStore cannot construct — per the malleus Knowledge Graph Protocol:

> "The Knowledge Graph cannot be constructed without an ontology. The ontology is not optional configuration. It is a required constructor parameter. No ontology, no KG. This is not a runtime check that warns; it is a structural impossibility."

**Declaration flow:**

```yaml
# schema/extended_thinking.yaml — ET's types
imports:
  - imports/malleus     # symlink to ../malleus/ontology/malleus.yaml

classes:
  Concept:
    is_a: Entity        # malleus root
    mixins: [Statusable]
    attributes:
      category: { range: ConceptCategory, required: true }
      ...

  RelatesTo:
    is_a: Relation      # malleus root
    slot_usage:
      source_id: { range: Concept }
      target_id: { range: Concept }
    attributes:
      weight: { range: float }
      ...
```

**Codegen (`make schema-kuzu`)** walks the LinkML and emits:
- `schema/generated/kuzu_ddl.py` — Kuzu `CREATE NODE TABLE` / `CREATE REL TABLE` statements, with ET system columns (`t_valid_from`, `t_valid_to`, `t_created`, `t_expired`, `t_superseded_by`, `namespace`, `et_source`, `vectors_pending`) appended to every row. Edge tables pin `FROM`/`TO` from `slot_usage.source_id.range` / `target_id.range` so Kuzu rejects wrong-pair inserts at the binder — the constitutive guarantee.
- `schema/generated/kuzu_types.py` — a bridge between the Pydantic types (already generated from the same LinkML via `gen-pydantic`) and Kuzu storage: `to_kuzu_row(obj)`, `from_kuzu_row(cls, row)`, `edge_endpoints(obj)`. Column renames (e.g., Kuzu's reserved `description` → `desc_text`) live here and are reversed on read.

**Public API (the consumer experience):**

```python
from schema.generated import models as m
from extended_thinking.storage import GraphStore, default_ontology

kg = GraphStore(db_path, ontology=default_ontology())

# Pydantic types are the source of truth for shape + validation.
c = m.Concept(id="c1", name="sparse attention",
              category=m.ConceptCategory.topic,
              description="a technique")
kg.insert(c)                           # dispatches to_kuzu_row + Cypher

# Edges validate domain/range at Kuzu's binder.
e = m.RelatesTo(id="e1", source_id="c1", target_id="c2",
                relation_type="semantic", weight=0.9)
kg.insert(e)
```

**Built-in types share the machinery.** `Concept`, `Session`, `Fragment`, `Chunk`, `Insight`, `Wisdom`, `Suggestion`, `Source` plus edges (`RelatesTo`, `InformedBy`, `HasProvenance`, `Supersedes`) are all declared in ET's LinkML, all descended from a malleus root (Entity/Event/Signal/Relation), all generated through the same pipeline. No hand-rolled schema survives.

**Consumer types compose cleanly.** autoresearch-ET ships `schema/autoresearch.yaml` importing malleus, declaring Hypothesis/Variant/Run/Metric. Codegen runs inside autoresearch-ET's repo producing its own generated types. At runtime: `Ontology.from_module(autoresearch_ddl).merged_with(et_ontology)` gives GraphStore the union.

#### C2 — Namespace isolation (R9)

Every node and edge carries a `namespace` string property. Queries and algorithms scope to a namespace.

- **Default for new programmatic writes:** `"default"` (matches autoresearch-ET's `ETClient` Protocol default).
- **Existing ET data migrates to `"memory"` namespace.** One-time pass on first run after upgrade; the pre-013 data is semantically about memory synthesis and gets its own namespace rather than mixing with future research data.
- **Query semantics.** Omitting namespace = the caller's scoped namespace (for MCP tools, declared via config or tool arg). Explicit `namespace=None` = all namespaces (admin/debug).
- **Algorithm context.** `AlgorithmContext` gains a `namespace` field; algorithms iterate only nodes/edges in that namespace unless explicitly told otherwise.

Implementation: property-per-node, not schema-level partition. Simpler, doesn't multiply Kuzu schemas, lets a single algorithm run either scoped or unscoped. Schema-level partition can come later if it becomes a perf or blast-radius concern.

#### C3 — Synchronous write path (R4)

A separate write path that returns only after Kuzu has committed. Consumers depending on write-then-read coherence use this; the async memory-ingest path stays intact for conversation ingestion.

**Two-phase visibility, honestly named:**

- Kuzu commit is synchronous. The call returns after the transaction is durable and the next query sees the write.
- Vector index (ChromaDB) updates are asynchronous. Each node carries a `vectors_pending: bool` property that's `true` between the Kuzu commit and the vector write. `et_find_similar` can optionally filter on `vectors_pending=false` to guarantee indexed results.

HTTP endpoints:

```
POST /api/v2/graph/node  → {id, vectors_pending: true}
POST /api/v2/graph/edge  → {id}
```

This is a deliberate choice over "fully sync vectors too". Embedding generation and ChromaDB writes can add seconds of latency; hiding that inside the write path makes consumers pay for the slowest dependency even when they don't need vector visibility. Exposing the pending-vector state lets consumers query whichever surface they need, when they need it.

#### C4 — Write-side MCP surface (R3 + R8 merged)

MCP has been read-only. Two new tools, both namespace-aware:

```
et_add_node(type, properties, namespace="default") -> {id, vectors_pending}
et_write_rationale(subject_node_id, rationale_text,
                   cited_node_ids=[...], namespace="default") -> {edge_id}
```

`et_write_rationale` enforces the **grounded-rationale guarantee**: every id in `cited_node_ids` must resolve to an existing node in the given namespace before commit. Unresolved citations → error, no write. This extends ET's existing "grounded or refuse" invariant (invariant #1) to external LLM writes.

`et_add_edge`, `et_find_similar`, and `et_query_nodes` ship as separate MCP tools under the same contract. (R3 listed more tools than strictly needed; this is the minimal surface consumers actually need.)

#### C5 — Filtered bitemporal queries (R5)

```
et_shift(from_ts, to_ts, node_types=[...], edge_types=[...],
         property_match={...}, namespace="default")
    -> {nodes_added, nodes_expired, edges_added, edges_expired}

GraphStore.diff(from_ts, to_ts, node_types, edge_types,
                property_match, namespace) -> same shape
```

A typed consumer watching their slice should not receive a river of concept noise.

#### C6 — First-class typed vector similarity (R6)

```
et_find_similar(query: str | list[float], node_type: str,
                threshold: float = 0.8, k: int = 10,
                namespace: str = "default")
    -> list[(node_id, score)]
```

Query text is embedded on the fly; a raw embedding can be passed directly. Results are filtered by `node_type` and `namespace` before vector search (not after), so scoring stays meaningful. Optional flag to join outcome edges (e.g., return the `Metric` node linked to each returned `Variant`) ships as an extension parameter, not a second tool.

#### C7 — Algorithm write-back (R7)

```
et_run_algorithm(algo_name, scope=..., write_back=False, namespace="default")
    -> algorithm result
```

When `write_back=True`, the algorithm's proposals are persisted as edges with type `PROPOSAL_BY`. Each `PROPOSAL_BY` edge carries:

```
algorithm: str        # plugin name
parameters: dict       # the AlgorithmContext.params used
invoked_at: str       # ISO timestamp
namespace: str
```

A consumer who records "at time T, spreading activation under scope S proposed V1, V2, V3" now has the provenance trail without implementing it themselves.

#### C8 — Non-extraction ingest mode (R2)

`MemoryProvider` protocol gains an `extract_concepts: bool = True` flag. Providers returning structured data (typed run records, telemetry, form submissions) set `False`. Chunks from such providers are stored with provenance but skip the LLM extraction loop.

Existing conversation providers (`claude_code`, `chatgpt_export`, etc.) default to `True`. No behavior change for the memory audience.

#### C9 — Documentation as a first-class contract

This ADR IS the documentation. `docs/configuration.md` gains a "programmatic-consumer API" section. `project-invariants.md` is cross-checked (see below) but not modified — the invariants still hold. autoresearch-ET is cited as the canonical reference implementation.

## Compatibility with existing invariants

Each invariant from `project-invariants.md`, checked:

- **#1 Every insight traces to evidence.** Preserved. Typed nodes carry provenance via `HAS_PROVENANCE` edges the same as concepts. `et_write_rationale` explicitly enforces the grounded-citation guarantee.
- **#2 Never silently lose data.** Preserved. All typed-node writes land in the bitemporal log; supersession, not deletion.
- **#3 Enrichment is always relevant.** N/A here — this ADR is about programmatic state, not enrichment. Consumers own their relevance logic.
- **#4 Distinguish user words from system inferences.** Preserved. Typed nodes are neither user-written nor system-inferred; they're external-system-authored and carry a `source` property saying which consumer wrote them. The three existing classes (raw/extracted/synthesized) stay disjoint; typed nodes are a fourth class with its own provenance.
- **#5 Processing never blocks the user.** Preserved for the memory path. C3's sync write path is for programmatic consumers only, does not block any user-facing ingest, and is documented as a separate surface.

No invariant is weakened. Everything is additive.

## What is explicitly NOT in scope

The requirements doc's "NOT required" list stands:

- Model routing (which LLM for which role).
- Cost functions.
- Loop control (generate, score, promote, terminate).
- Decision logic (accept/reject variants).
- Domain-specific generators (prompt templates, mutation operators).
- Pre-registration enforcement.
- Budget accounting.

Any of these sneaking into ET is a design error. Caller-side.

Also out of scope for **this ADR specifically**:

- Row-level access control. Namespace isolation is scope, not security. A process that can write to `"memory"` can also write to `"research"` if it has the Kuzu handle. Multi-tenant-with-auth is a separate concern.
- Cross-namespace queries with joins. Consumers query their own namespace; cross-namespace work is admin (explicit `namespace=None`), not everyday.
- Per-namespace algorithm config. Initially all namespaces see the same `settings.algorithms`. Per-namespace plugin config, if needed, comes later via drop-ins.

## Implementation sequence

Four phases, dependencies explicit. No time estimates.

**Phase 0 — Malleus integration & codegen** (prerequisite for C1; discovered necessary after v1):

- P0.1: Symlink malleus into `schema/imports/malleus.yaml`.
- P0.2: Retrofit `schema/extended_thinking.yaml` — every ET class declares `is_a:` a malleus root; `Wisdom`, `Chunk` added; edge classes subclass `Relation` with `slot_usage` pinning `source_id`/`target_id`.
- P0.3: `scripts/gen_kuzu.py` — LinkML → Kuzu DDL. Reserved-word renaming (`description → desc_text`), system columns, FROM/TO resolution from `slot_usage`.
- P0.4: `scripts/gen_kuzu_types.py` — LinkML → typed accessors. `to_kuzu_row`, `from_kuzu_row`, `edge_endpoints`. Pydantic stays the shape-of-truth; this module adds serialization.
- P0.5: `make schema-kuzu` target + `schema-check` drift guard.
- P0.6: `GraphStore(db_path, ontology=...)`. `_init_schema` deleted; the ontology's generated DDL is applied. All legacy column names migrated; edge names standardized PascalCase.
- P0.7: Remove any pre-v2 runtime-registration artifacts.
- P0.8: This revision (v2).
- P0.9: End-to-end tests against the typed accessors + ontology-driven GraphStore.

**Phase A — Foundations:**

1. C1: the typed-node framework described above — shipped via Phase 0 + a `GraphStore.insert(instance)` dispatcher for typed writes.
2. C2: `namespace` property on every node and edge, including existing ones. One-time migration assigns `"memory"` to all pre-013 data. `AlgorithmContext.namespace`. Query-level filtering on every existing MCP tool.

**Phase B — Programmatic write surface:**

3. C3: sync HTTP write path. `POST /api/v2/graph/node` + `POST /api/v2/graph/edge`. `vectors_pending` marker. Async ChromaDB indexer picks up pending nodes and clears the flag.
4. C4: `et_add_node` + `et_add_edge` + `et_write_rationale` MCP tools. Citation verification enforced before commit.
5. C8: `MemoryProvider.extract_concepts` flag. Pipeline honors it.

**Phase C — Query and algorithm exposure:**

6. C5: filtered `et_shift` + `GraphStore.diff` with `node_types`, `edge_types`, `property_match`, `namespace`.
7. C6: `et_find_similar` + `GraphStore.find_similar_typed`. ChromaDB queries filtered by `node_type` and `namespace` pre-search.
8. C7: `PROPOSAL_BY` edge type. `et_run_algorithm(write_back=True)` path. Every built-in algorithm's plugin wrapped to support write-back uniformly.
9. C9: publish the docs section, update `CHANGELOG`, link autoresearch-ET as reference consumer.

Each numbered item ships with tests. Phase 0 + A unblocks autoresearch-ET at minimum; phases B and C widen the surface.

**Rewrite checkpoint:** before ADR 011's phase B (first Wikipedia source) ships, ADR 011 is rewritten to use this framework. `KnowledgeNode` gets declared as a LinkML class (`is_a: Signal`) in ET's or a dedicated enrichment ontology, not a runtime registration. `ENRICHES` becomes a typed edge subclassing `Relation`. Enrichment sources write via `GraphStore.insert` / `et_add_node`; relevance gates that cite concepts use `et_write_rationale`. The enrichment layer becomes one consumer of the research-backbone framework.

## Consequences

**Positive:**

- ET becomes a substrate, not just a product. Second (and third, fourth) audiences are possible without forking.
- autoresearch-ET unblocks. The concrete `ETClient` Protocol they already wrote can be implemented against real ET rather than a mock.
- ADR 011 simplifies. `KnowledgeNode` stops being a special schema addition and becomes a use case of C1.
- Bitemporal guarantees extend to every typed node automatically. Consumers inherit the full ET auditability story without having to reimplement it.
- Namespaces give clean blast-radius boundaries. A research run cannot corrupt memory concepts; a memory sync cannot pollute research nodes.
- Grounded-rationale guarantee extends ET's existing "refuse to hallucinate" contract to programmatic LLM writes, not just internal wisdom generation.

**Negative:**

- Surface area grows. More MCP tools, more HTTP endpoints, more concepts to document.
- Migration is non-trivial. Namespace property has to land on every existing node and edge. Kuzu schema changes are not free; the migration is atomic but has to be tested carefully.
- Two write paths (sync and async) mean two surfaces to maintain. Discipline needed so they don't drift semantically.
- `vectors_pending` is an honest compromise but still a compromise. Consumers have to know about it or they might query before vectors land.
- Namespace-as-property means every query pays a predicate cost. For small graphs, irrelevant. For multi-million-node graphs with many namespaces, a schema-partition revisit may be needed.

## Open questions resolved

Restating the requirement doc's open questions with this ADR's answers:

1. *Typed nodes in the same Kuzu schema or parallel?* **Same schema.** All types declared in one LinkML hierarchy rooted in malleus; build-time codegen emits unified DDL. Built-in and consumer types are peers; consumers compose via `Ontology.merged_with`.
2. *Namespace as property or schema-level partition?* **Property, default `"default"` for new writes, `"memory"` after migration for existing data.** Simpler. Revisit if perf demands it.
3. *Sync-write path connection pool?* **Shared with the async path.** Kuzu's transaction model serializes writes; a second pool buys complexity without throughput.
4. *(v2)* *Runtime vs build-time type registration?* **Build-time.** Architecture A from the malleus KG Protocol requires the ontology to be constitutive. Runtime registration was a v1 bug.
5. *(v2)* *How do consumers add their own types?* **They ship their own LinkML schema importing malleus, run the same codegen inside their own repo, and merge the generated ontology into ET's via `Ontology.merged_with` at boot.**

## Open questions (remaining)

- **Embedding cost for `vectors_pending` nodes.** Who pays for the embedding call? Probably the async indexer, with a budget ceiling. Not this ADR's scope; operational concern for the implementation phase.
- **Back-pressure when async vectors fall behind.** If consumers write faster than the indexer can embed, the pending queue grows. Needs a bounded queue and a strategy when it hits the bound. Again operational; not blocking the ADR.
- **`et_write_rationale` citation verification cost.** For N citations, N existence checks. Fine at small N. If a consumer starts citing hundreds of nodes per rationale, we batch the lookups. Defer optimization.
- **Per-namespace plugin configuration.** ADR 012 gives one config tree; consumers may want per-namespace plugin config. Drop-ins under `conf.d/namespace-<name>.toml` is a plausible extension. Defer until a consumer asks.

## References

- `docs/research-backbone-requirements.md` — the requirements this ADR accepts.
- [malleus root ontology](../../../malleus/ontology/malleus.yaml) — Entity/Event/Signal/Relation + mixins; constructor-level dependency.
- [malleus KNOWLEDGE_GRAPH_PROTOCOL.md](../../../malleus/KNOWLEDGE_GRAPH_PROTOCOL.md) — normative source for Architecture A (constitutive). "The ontology is not optional configuration. It is a required constructor parameter."
- [malleus ONTOLOGY_PROTOCOL.md](../../../malleus/ONTOLOGY_PROTOCOL.md) — how to adopt malleus in a new project.
- `../../autoresearch-ET/src/autoresearch_et/et_client.py` — the concrete `ETClient` Protocol this ADR's surface implements.
- `schema/extended_thinking.yaml` — ET's LinkML, imports malleus.
- `scripts/gen_kuzu.py` / `scripts/gen_kuzu_types.py` — the codegen this ADR depends on.
- `docs/configuration.md` — `make schema-kuzu` workflow.
- ADR 002 — bitemporal foundation; every typed node and edge inherits those guarantees.
- ADR 003 — pluggable algorithms; `AlgorithmContext` gains `namespace`.
- ADR 010 — batteries-included providers; `extract_concepts=False` is the programmatic-ingest counterpart.
- ADR 011 — proactive enrichment; blocked on this ADR, rewritten on top of it.
- ADR 012 — centralized configuration; consumer-ontology registration may land as TOML keys (`[ontologies.autoresearch]` etc.) in a future revision.
- `project-invariants.md` — cross-checked; no invariant modified.
