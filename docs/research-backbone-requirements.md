# Research-Backbone Requirements for ET

*Source: born from the autoresearch-et project, abstracted for ET's general use. Draft for review.*

## Motivation

ET was built with the human as the goal: ingest human memory chunks, extract concepts, surface wisdom. A parallel audience has become visible: LLM-driven research loops that need a bitemporal, typed, queryable state store with algorithm hooks. The overlap with ET's capabilities is large.

This doc captures what a research-backbone audience needs from ET. The requirements are abstracted so any consumer (research loop, workflow engine, typed archive) can use them, not just the first caller.

What this doc is not: an implementation spec. The shapes below are minimal. ET's own design taste decides final APIs.

## Guiding principle

ET stays the substrate. It stores typed state, tracks bitemporal evolution, runs pluggable algorithms, and refuses to hallucinate. Loop control, decision logic, cost functions, and domain generators live in the consumer, not in ET.

## Requirements

### R1. Typed non-concept nodes

Consumers want to register node types beyond Concept, Wisdom, Chunk, with arbitrary property bags, added to the Kuzu schema in a non-breaking way. A research loop models Hypothesis, Variant, Run, Metric, AuditEvent. A workflow engine might model Job, Step, Artifact. ET should not care about the names.

Minimal shape:
- `GraphStore.register_node_type(name, properties_schema)`
- `GraphStore.add_node(type, properties) -> node_id`
- `GraphStore.add_edge(src, dst, type, properties, valid_from, valid_to=None) -> edge_id`

Bitemporal fields on edges are already present. Extend to nodes if not.

### R2. Bypass concept extraction for structured input

`sync()` today runs LLM extraction on every provider pull. Structured input (typed runs, telemetry, form submissions) does not benefit from extraction; it is already structured.

Minimal shape: a `Provider` flag `extract_concepts: bool = True`. When `False`, the chunk/node is stored verbatim with provenance. Existing memory providers keep default behavior.

### R3. Write-side MCP tools

All 13 current MCP tools are read-only. MCP is the idiomatic Claude Code surface. Any consumer that wants to record state via MCP hits a wall and has to reach for HTTP.

Minimal shape:
- `et_add_node(type, properties) -> node_id`
- `et_add_edge(src, dst, type, properties) -> edge_id`
- `et_write_rationale(subject, text, cited=[...]) -> edge_id` (see R8)
- `et_find_similar(query, node_type, threshold, k)` (see R6)

Specific sugar tools (`et_record_run`, etc.) can be built outside ET by the consumer.

### R4. Synchronous write path

Invariant #5 says "processing never blocks the user." This is correct for the memory-ingest path. Consumers driving a loop need write-then-read coherence: after writing, the next query must see the write.

Minimal shape: `POST /api/graph/node` and `POST /api/graph/edge` return only after the Kuzu commit lands and any vector index is updated. Keep the async path for memory ingest. Document the two paths as distinct.

### R5. Bitemporal query filters

`diff`, `et_shift`, and similar bitemporal queries return the full graph delta. A typed consumer wants its own slice, not a river of concept noise.

Minimal shape: `et_shift(from, to, node_types=[...], edge_types=[...], property_match={...})`. Same filters on the underlying `GraphStore.diff()`.

### R6. Vector similarity as a first-class query

ChromaDB is used inside ET for entity resolution and link prediction. Consumers cannot query it directly for arbitrary typed nodes. A research loop wants "have we tried something close to this Variant before," dedup wants "is this near-duplicate to any prior Artifact," retrieval wants "nearest Job given this description."

Minimal shape: `et_find_similar(query_text_or_embedding, node_type, threshold, k) -> list[(node_id, score)]`. Optional flag to also return outcome edges (e.g., the Metric node linked to each returned Variant).

### R7. Algorithm outputs as write-back proposals

Pluggable algorithms (spreading activation, recombination, link prediction) return suggestions. Consumers that want to record "at time T, spreading activation proposed V1, V2, V3 under scope S" need an explicit persistence hook. Without it, the provenance of algorithm-informed decisions is lost.

Minimal shape: a `PROPOSAL_BY` edge type, written when an algorithm is invoked in write-back mode, carrying the algorithm name + parameters + timestamp. Opt-in per invocation.

### R8. Grounded-rationale guarantee for LLM writes

ET's "grounded wisdom or refuse" invariant is the feature that makes audit trails credible. Extend it explicitly to external consumers: any LLM-generated rationale written to the graph must cite real node IDs; if citations do not resolve, the write is rejected.

Minimal shape: `et_write_rationale(subject_node_id, rationale_text, cited_node_ids=[...])`. Before commit, verify every `cited_node_ids` entry resolves to an existing node. On failure, return an error. The consumer does not get to write an ungrounded rationale even by accident.

### R9. Multi-consumer isolation

Different consumers (memory-synthesis user, research loop, workflow engine) should partition their graphs so queries and algorithms scope to one consumer's data. Without this, ET's noise floor rises linearly with the number of consumers.

Minimal shape: every node and edge carries a `namespace` (or `workspace_id`) string property. Default namespace is `"default"` for backward compatibility. Queries accept a namespace filter. Algorithms accept a namespace-scoped `AlgorithmContext`.

### R10. Documentation of the research-backbone audience

Extend `project-invariants.md` or open ADR-013 to formalize ET as a first-class backend for typed research workflows, not only memory synthesis. Document:
- The sync-write path (R4) vs the async memory-ingest path.
- The non-extraction ingest mode (R2).
- The namespace convention (R9).
- The bitemporal guarantees consumers can rely on.
- Example: autoresearch-et as a canonical integration.

Without this, consumers guess. With this, they build against a stable contract.

### R11. Explicit close contract on GraphStore — **shipped**

**What.** `GraphStore.close()` — fully dispose the underlying Kuzu `Database` (not just any open Connection), releasing the OS file handle before the method returns. Ideally: also expose a context-manager form (`with GraphStore(...) as kg:`) so callers never hold a live handle past scope.

**Why it generalizes.** Kuzu's Python API has no explicit `Database.close()`; file handles release only when `__del__` runs. Any consumer that opens, uses, then re-opens the same DB inside one process has to trust garbage collection timing. Without a proper close(), a consumer that instantiates two GraphStore objects on the same file (a common mistake: pipeline stage A + stage B) ends up with two live Kuzu Database handles simultaneously, whose page-allocation views diverge, corrupting the file on write. This was the root cause of the 2026-04-12 autoresearch-et incident (seek-to-5.8-TB corruption). autoresearch-et added a belt-and-suspenders single-instance guard, but the underlying contract gap lives in ET.

**Implemented (2026-04-12):**
- `GraphStore.close()` — drops `_conn` + `_db` references and forces `gc.collect()` so Kuzu's `__del__` runs deterministically before the call returns. Idempotent.
- `GraphStore.__enter__` / `__exit__` — context-manager form.
- **Process-wide single-instance registry** keyed by resolved absolute path. Constructing a second `GraphStore` on a path that's already open raises `DuplicateGraphStoreError` with a clear message pointing at `close()`. Reopen-after-close is fine. Implemented as a `weakref` registry so a forgotten-but-GC'd instance doesn't false-block.
- `StorageLayer.close()` cascades to the underlying KG.
- The HTTP route's `_get_graph_store()` (api/routes/graph_v2.py) is now a process-wide cached singleton per resolved path — concurrent requests share one instance instead of constructing one each. FastAPI shutdown calls `close_graph_stores()` to release the file handles.
- Acceptance test: `tests/acceptance/test_graph_store_lifetime.py` (12 cases) — covers close idempotency, registry cleanup, double-open rejection, path-resolution collisions, context-manager semantics, StorageLayer cascade, and route singleton behaviour.

**Related schema-change hazard (still open).** When the DDL changes (new node/edge types added to the LinkML) on a populated DB, `CREATE TABLE IF NOT EXISTS` runs the migration online. If a second handle is open at that moment, the migration can corrupt — this is the same multi-handle class as R11 above, and the duplicate-handle guard prevents the case in practice. A separate `GraphStore.check_schema(expected_ontology)` that fails fast on schema drift (rather than silently migrating an already-populated DB) is **not yet implemented** — file as a follow-up if the bitemporal-historical case calls for it.

## What is explicitly NOT required from ET

These live in the consumer, not ET:

- Model routing (which LLM for which role).
- Cost functions.
- Loop control flow (generate, score, promote, terminate).
- Decision logic (accept/reject variants).
- Domain-specific generators (prompt templates, mutation operators).
- Pre-registration enforcement.
- Budget accounting.

ET is the substrate. The brain lives outside. Every requirement above is about making ET a better substrate, not about pulling consumer logic into ET.

## Compatibility with existing invariants

Cross-checked against `project-invariants.md`:

- **#1 evidence tracing:** preserved; typed nodes carry provenance the same as Concept nodes.
- **#2 no silent loss:** preserved; all writes land in the bitemporal log.
- **#3 source priority:** N/A; research nodes are not memory extractions.
- **#4 distinguish user vs inferred:** preserved; typed nodes are neither, and carry their own `source` property.
- **#5 processing never blocks the user:** preserved for the memory path; R4 adds a parallel sync path for programmatic consumers without affecting user-facing ingest.

No invariant is weakened. All additions are additive.

## Ordering suggestion (not prescriptive)

If phased rollout is desired, R1 + R4 + R2 unlock basic typed-storage use. R3 + R6 + R8 unlock the MCP-first consumer pattern. R5 + R7 + R9 round out the audit and multi-tenant story. R10 documents what has been built.

## Open questions for ET's own design

1. Are typed nodes stored in the same Kuzu schema or in a parallel schema? ET's answer likely depends on how much it wants to preserve the concept-extraction pipeline's invariants.
2. Is the namespace a property on every node/edge, or a schema-level partition? Performance and UX tradeoff.
3. Does the sync-write path share Kuzu connection pooling with the async path, or get its own pool?

These are ET's to answer on ET's terms.
