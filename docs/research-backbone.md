# Using ET as a Research Backbone

**Audience:** programs — research loops, workflow engines, typed archives — that want ET as a typed, bitemporal, queryable state store. Humans doing memory synthesis, see [README](../README.md) instead.

This doc complements [ADR 013](ADR/013-research-backbone-audience.md) (the design) with the practical consumer view: what you get, how to write, how to query, what contracts you can rely on.

Canonical reference consumer: [autoresearch-ET](../../autoresearch-ET). Its `ETClient` Protocol (`src/autoresearch_et/et_client.py`) is the concrete contract this doc implements.

## What ET provides

| Capability | What you get | Where |
|---|---|---|
| Typed nodes + edges | Malleus-rooted ontology, LinkML-declared, Kuzu-backed. Kuzu's binder enforces `FROM/TO` at write time — wrong pairs are rejected. | Declare classes in your own `schema/<consumer>.yaml` importing malleus; regenerate with the same codegen (ADR 013 Phase 0). |
| Synchronous writes | `POST /api/v2/graph/node` and `/edge` return after Kuzu commit. `vectors_pending: true` marker on nodes until async index catches up. | HTTP (C3) or MCP `et_add_node` / `et_add_edge` (C4). |
| Grounded-rationale guard | `et_write_rationale` rejects a write if any `cited_node_ids` entry doesn't resolve. Audit trails can't cite what doesn't exist. | MCP (C4). |
| Namespace isolation | Every node and edge carries a `namespace` column. Your `"research"` namespace stays disjoint from ET's `"memory"`. | `namespace` argument on every write / read (C2). |
| Bitemporal queries | `et_shift(from, to, *, node_types, edge_types, namespace, property_match)` returns only the slice you asked for. | MCP `et_shift`, `GraphStore.diff` (C5). |
| Typed vector similarity | `et_find_similar(query, node_type, namespace, threshold, k)` — "have we seen something close to this?". Auto-indexed on insert. | MCP (C6). |
| Algorithm write-back | `et_run_algorithm(algorithm, params, write_back=True)` persists proposals as `ProposalBy` edges with full provenance (algorithm, parameters, invoked_at, score). | MCP (C7). |
| Non-extraction ingest | `MemoryProvider.extract_concepts = False` — structured data lands as chunks + provenance without running the concept-extraction LLM. | Provider attribute (C8). |

## The minimum workflow

### 1. Declare your types

Your consumer project (e.g. autoresearch-ET) ships its own LinkML schema importing malleus:

```yaml
# autoresearch-ET/schema/autoresearch.yaml
id: https://autoresearch.dev/schema
name: autoresearch
imports:
  - linkml:types
  - ../../extended_thinking/schema/imports/malleus

classes:
  Hypothesis:
    is_a: Entity
    mixins: [Statusable]
    attributes:
      text:
        required: true
      status:
        range: HypothesisStatus

  Variant:
    is_a: Entity
    attributes:
      code:
        required: true

  Produced:
    is_a: Relation
    slot_usage:
      source_id: { range: Hypothesis }
      target_id: { range: Variant }
```

Regenerate DDL + typed accessors with the same codegen scripts ET ships (`scripts/gen_kuzu.py`, `scripts/gen_kuzu_types.py`). Commit the generated `kuzu_ddl.py` and `kuzu_types.py`.

### 2. Compose ontologies at boot

```python
from extended_thinking.storage import GraphStore
from extended_thinking.storage.ontology import Ontology, default_ontology
from autoresearch_et.schema.generated import kuzu_ddl as autoresearch_ddl

et_ontology = default_ontology()
consumer_ontology = Ontology.from_module(autoresearch_ddl, name="autoresearch")
combined = et_ontology.merged_with(consumer_ontology)

kg = GraphStore(db_path, ontology=combined)
```

Both schemas' tables exist in the same Kuzu database, namespace-isolated.

### 3. Write typed state

Over HTTP:

```python
import httpx

httpx.post("http://localhost:8000/api/v2/graph/node", json={
    "type": "Hypothesis",
    "properties": {
        "id": "hyp-001",
        "name": "sparse attention speeds up inference on K<256",
        "text": "sparse attention speeds up inference on K<256",
        "status": "open",
    },
    "namespace": "autoresearch",
    "source": "autoresearch-et",
})
# -> {"id": "hyp-001", "vectors_pending": true}
```

Over MCP (from Claude Code or any MCP client):

```json
{"tool": "et_add_node",
 "arguments": {
   "type": "Hypothesis",
   "properties": {"id": "hyp-001", "name": "...", "text": "...", "status": "open"},
   "namespace": "autoresearch",
   "source": "autoresearch-et"
 }}
```

Both return synchronously after Kuzu commits. `vectors_pending` tracks the async ChromaDB index.

### 4. Record grounded rationales

When an LLM in your loop writes a justification, every citation must resolve:

```json
{"tool": "et_write_rationale",
 "arguments": {
   "subject_node_id": "hyp-001",
   "text": "This hypothesis is plausible because Variant v-3 beat baseline on Metric m-7.",
   "cited_node_ids": ["v-3", "m-7"],
   "namespace": "autoresearch"
 }}
```

If `v-3` or `m-7` doesn't exist, the call returns an error listing every missing id. No half-baked Rationale nodes land.

### 5. Retrieve by vector similarity

"Have we tried a Variant close to this?":

```json
{"tool": "et_find_similar",
 "arguments": {
   "query": "multi-head attention with top-k sparsity",
   "node_type": "Variant",
   "namespace": "autoresearch",
   "threshold": 0.7,
   "k": 5
 }}
```

Returns `[{"id": ..., "score": ...}, ...]`. Typed nodes are auto-indexed at insert; metadata filters by `node_type` and `namespace` before cosine, so scoring stays meaningful.

### 6. Watch a bitemporal slice

"What changed in my research namespace since yesterday?":

```json
{"tool": "et_shift",
 "arguments": {
   "from_date": "2026-04-11",
   "to_date": "2026-04-12",
   "node_types": ["Hypothesis", "Variant", "Run"],
   "edge_types": ["Produced"],
   "namespace": "autoresearch"
 }}
```

Returns `{nodes_added, nodes_expired, edges_added, edges_expired}` each carrying a `_type` tag. No memory-side noise.

### 7. Run an algorithm with provenance

"Rank Variants by similarity to this Hypothesis, and keep the result for audit":

```json
{"tool": "et_run_algorithm",
 "arguments": {
   "algorithm": "embedding_similarity",
   "params": {"threshold": 0.7},
   "namespace": "autoresearch",
   "write_back": true
 }}
```

With `write_back=true`, every proposed pair lands as a `ProposalBy` edge carrying `algorithm`, `parameters_json`, `invoked_at`, `score`. Later you can query "what did embedding_similarity say at 2026-04-12T14:22:00?" without re-running it.

## Contracts you can rely on

1. **Every insight traces to evidence** (invariant #1). `et_write_rationale` enforces this for LLM writes. Unresolved citations → rejected before commit.
2. **Never silently lose data** (invariant #2). All writes land in the bitemporal log. Supersession, not deletion.
3. **User inferences stay labeled** (invariant #4). Typed nodes are externally authored (`et_source` column records who wrote them). They're a distinct class from ET's memory-side `raw` / `extracted` / `synthesized` triad.
4. **Write-then-read coherence on the sync path** (C3). After `POST /api/v2/graph/node` returns, the next read sees the write. Vector visibility follows asynchronously via `vectors_pending`.
5. **Ontology is constitutive** (ADR 013 v2, malleus KG Protocol). Domain/range validated at write time by Kuzu's binder. You cannot assert a relation ET's ontology doesn't allow.

## Common mistakes and how ET signals them

| Mistake | Status / error | What ET says |
|---|---|---|
| POST a node to `/edge` or an edge to `/node` | 400 | "'RelatesTo' is an edge type; use POST /api/v2/graph/edge" |
| Unknown type in `type` field | 400 | "unknown node type 'Nonsense'. Registered node types: [...]" |
| Required field missing in `properties` | 422 | Full pydantic error list |
| `source_id` doesn't resolve | 400 | "proposal source not found: 'does-not-exist'" |
| Edge violates ontology FROM/TO | 409 | "edge rejected by ontology constraint: Expected labels are Concept" |
| Unresolved `cited_node_ids` on rationale | error | "grounded-rationale guarantee violated. The following cited_node_ids do not resolve to existing nodes: ..." |
| Writing `credentials.*` to `config.toml` | RuntimeError at load | "credentials found in config file: ... Move them to secrets.toml." |

## What ET does NOT do

Per ADR 013's "not in scope":

- Model routing, cost functions, loop control, decision logic — **consumer-side.**
- Domain-specific generators (prompt templates, mutation operators) — **consumer-side.**
- Pre-registration enforcement, budget accounting — **consumer-side.**
- Row-level access control — namespace is scope, not security.
- Cross-namespace queries with joins — admin-only, not everyday.

If you find yourself pushing any of these into ET, stop. That's a consumer-side concern, and it lives there for a reason.

## References

- [ADR 013 v2](ADR/013-research-backbone-audience.md) — the design
- [research-backbone-requirements.md](research-backbone-requirements.md) — the input requirements (R1-R10)
- [configuration.md](configuration.md) — TOML config, schema codegen, `make schema-kuzu`
- [autoresearch-ET](../../autoresearch-ET) — canonical reference consumer
- [malleus](../../malleus) — the root ontology every class extends
- [KNOWLEDGE_GRAPH_PROTOCOL.md](../../malleus/KNOWLEDGE_GRAPH_PROTOCOL.md) — why the ontology is constitutive (Architecture A)
