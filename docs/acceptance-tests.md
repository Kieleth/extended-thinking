# Acceptance Test Framework

This is the fast iteration loop for ET behavior. It loads realistic fixtures
(Claude Code session JSONL, markdown folders, synthetic graph shapes), runs
the full sync → extract → graph-build → algorithm → wisdom pipeline, and
asserts outputs match known-good expectations. No MCP round-trip, no
per-iteration API spend, no live LLM flakiness in the default path.

The target is sub-30-second iteration. Default run clocks ~27s on a laptop,
35 tests, zero network, zero LLM cost.

## Three layers

| Layer | Trigger | LLM | Network | When |
|---|---|---|---|---|
| **A. Fast path** | `make at` | FakeListLLM / DummyLM | none | default, every change |
| **B. Cassette** | `make at-vcr` | real model, replayed | none (cassette) | pre-PR verification |
| **C. Live** | `make at-live` | real Anthropic | yes | pre-release only |

Layer A is the hot loop. Layer B proves the code still handles real model
output shapes (cassettes are committed YAML). Layer C hits the API and
should stay rare.

## Commands

```bash
make at                   # fast path, default (~27s, 35 tests)
make at-vcr               # fast path + replay cassette tests (~28s total)
make at-live              # fast path + actually hit Anthropic (LIVE_API=1)
make at-record            # regenerate cassettes (needs real API key)
make at-update-snapshots  # accept new syrupy snapshot outputs
```

Markers, selectable via `-m`:
- `acceptance` — everything in `tests/acceptance/` (default)
- `vcr` — cassette-backed tests (opt-in)
- `live` — hits live API (skipped unless `LIVE_API=1`)
- `slow` — deselect with `-m "not slow"`

## Layout

```
api/tests/
├── conftest.py                        Shared fixtures, session + function scope
├── fixtures/
│   ├── cc_sessions/session_small.jsonl    6 msgs, Kuzu-vs-SQLite decision
│   ├── folders/notes_small/               3 .md files on same topic
│   ├── expected/concepts_small.json       ground-truth concepts
│   ├── expected/graph_small.json          loadable graph shape + weights
│   └── cassettes/                         VCR YAML, one per test
├── helpers/
│   ├── fake_llm.py                    FakeListLLM + DummyLM (satisfy AIProvider)
│   ├── dummy_embed.py                 DSPy-style DummyVectorizer, seeded
│   ├── fixture_loader.py              JSON → GraphStore
│   ├── assertions.py                  KG + algorithm + wisdom assertions
│   └── snapshot_matchers.py           stabilize(): round floats, scrub volatile
└── acceptance/
    ├── test_end_to_end_pipeline.py          full sync + idempotency
    ├── test_algorithm_outputs.py            syrupy snapshot per algorithm
    ├── test_invariants_at_scale.py          ontology + bitemporal + no dangling
    ├── test_provider_fusion.py              AutoProvider merge / dedup / sort
    ├── test_cassette_real_model.py          real Anthropic via cassette
    ├── test_decay_properties.py             Hypothesis (3 properties)
    ├── test_reinforcement_properties.py     Hypothesis (3 properties)
    ├── test_activation_properties.py        Hypothesis (3 properties)
    └── test_link_prediction_properties.py   Hypothesis (3 properties)
```

## Fixtures available in conftest.py

Session-scoped (one per test session, shared):

- `dummy_embed` — deterministic `DummyVectorizer`
- `fixtures_dir` — `Path` to `api/tests/fixtures/`
- `cc_session_small_projects_dir` — staged CC-projects tree with session_small
- `cc_session_small` — `ClaudeCodeProvider` over that dir
- `folder_notes_small` — `FolderProvider` over `fixtures/folders/notes_small/`

Function-scoped (fresh per test, safe to mutate):

- `tmp_data_dir` — isolated data dir under pytest's `tmp_path`
- `fake_llm` — empty `FakeListLLM`; tests script responses
- `dummy_lm_factory` — callable that builds `DummyLM(responses=...)`
- `loaded_graph_small` — `GraphStore` pre-populated from `expected/graph_small.json`

## Writing a new acceptance test

### End-to-end pipeline test

Pattern: stage fixtures via conftest, patch the extractor's LLM provider with
a `DummyLM`, run `Pipeline.sync()`, assert against the concept store.

```python
from unittest.mock import patch
from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.storage import StorageLayer
from tests.helpers.fake_llm import DummyLM

async def test_something(tmp_data_dir, cc_session_small):
    storage = StorageLayer.lite(tmp_data_dir / "storage")
    pipeline = Pipeline.from_storage(cc_session_small, storage)

    fake = DummyLM({"CONVERSATION:": EXTRACTION_JSON}, default=EXTRACTION_JSON)
    with patch("extended_thinking.processing.extractor.get_provider",
               return_value=fake):
        result = await pipeline.sync()

    assert result["chunks_processed"] >= 1
    assert any("kuzu" in c["name"].lower()
               for c in pipeline.store.list_concepts(limit=50))
```

Key points:

- Patch at `extended_thinking.processing.extractor.get_provider`, the same
  patch point existing unit tests use. FakeListLLM / DummyLM satisfy the
  `AIProvider` protocol structurally.
- Use `StorageLayer.lite()` to get a Kuzu-backed store without ChromaDB.
  Use `StorageLayer.default()` when the test needs vectors.
- Extractor's prompt always contains the substring `CONVERSATION:`; key on it
  for a catch-all response, or on a domain-specific phrase for per-prompt
  routing.

### Algorithm snapshot test

Algorithm outputs are committed as syrupy snapshots, auto-generated. Diffs
surface in PR review when an algorithm's output changes.

`test_algorithm_outputs.py` walks the registry and snapshots every fast-path
algorithm. Adding a new algorithm to the registry makes it auto-covered on
the next `make at-update-snapshots`.

For tests where you want one algorithm's output rendered stably:

```python
from tests.helpers.snapshot_matchers import stabilize

def test_my_thing(loaded_graph_small, snapshot):
    result = MyAlgo().run(AlgorithmContext(kg=loaded_graph_small, now=FIXED_NOW))
    assert stabilize(result, ndigits=4) == snapshot
```

`stabilize()` rounds floats to 4 digits and scrubs these keys (which drift
per run for reasons unrelated to correctness):

- `first_seen`, `last_seen`
- `t_first_observed`, `t_last_observed`
- `t_created`, `t_expired`
- `t_valid_from`, `t_valid_to`, `t_superseded_by`
- `last_accessed`, `extracted_at`
- `created_at`, `updated_at`
- `namespace`

If an algorithm's output grows a new volatile field, add it to `VOLATILE_KEYS`
in `snapshot_matchers.py` rather than accepting a flaky snapshot.

### Hypothesis property test

One file per algorithm family. Properties are claims that hold across all
inputs, not against a specific fixture.

Template in `test_decay_properties.py`:

```python
@given(edges=st.lists(edge_st, min_size=1, max_size=10, unique_by=...))
@settings(max_examples=10, deadline=3000,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_decay_is_monotonic_decrease(edges):
    store = _make_store(edges)
    before = _edge_weights(store)
    PhysarumDecay().run(AlgorithmContext(kg=store, now=FIXED_NOW))
    after = _edge_weights(store)
    for b, a in zip(before, after):
        assert a <= b + 1e-9
```

Keep `max_examples` low (5-10) for fast-path budget. Each example creates a
fresh `GraphStore`, which dominates cost.

### Cassette test

`test_cassette_real_model.py` shows the pattern. Use `@pytest.mark.vcr`; the
module-level `vcr_config` fixture scrubs `x-api-key` and `authorization`
from recorded requests.

```python
pytestmark = [pytest.mark.acceptance, pytest.mark.vcr]

@pytest.fixture(scope="module")
def vcr_config():
    return {"filter_headers": [("authorization", "REDACTED"),
                               ("x-api-key", "REDACTED")]}
```

Recording: `make at-record` (sources `.env` automatically is NOT implied;
see "Recording cassettes" below).

## Fixtures, in detail

### session_small.jsonl

Six messages (3 user + 3 assistant), single topic: choosing Kuzu over SQLite
for a bitemporal KG. Provider yields 3 exchange-pair chunks.

Each entry follows the Claude Code JSONL format:

```json
{"type": "user", "message": {"content": "..."}, "timestamp": "2026-04-11T10:00:00Z", "slug": "..."}
{"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}, "timestamp": "..."}
```

### graph_small.json

Loadable shape for algorithm-only tests that skip extraction. 7 concepts, 8
relationships, hand-weighted to reflect post-reinforcement bias (strong
decision edges, medium support edges, weak tangent edges). Loaded via
`fixture_loader.load_graph_from_json()`.

Every relationship has `t_valid_from` in full ISO 8601 with timezone
(`"2026-04-11T10:00:00+00:00"`). Date-only strings break Physarum decay because
`datetime.fromisoformat()` returns a naive datetime that cannot subtract from
tz-aware `now`.

### Growing fixtures

Add fixtures only when a real regression surfaces that existing fixtures do
not catch. The v1 position is deliberately minimal: one small CC session,
one small folder, one loaded graph. Add `session_medium.jsonl` or
`graph_medium.json` when you have a concrete test that needs them. Do not
pre-build.

If you add a new fixture file, wire a session-scoped fixture in `conftest.py`
for it so tests share one load.

## Recording cassettes

Cassettes are committed under `api/tests/fixtures/cassettes/`. One YAML per
test, credentials scrubbed.

One-time setup (or when cassettes need refresh):

```bash
set -a; source /Users/luis/Projects/extended_thinking/.env; set +a
cd /Users/luis/Projects/extended_thinking/api
python -m pytest tests/acceptance/ -m vcr --record-mode=rewrite
```

`--record-mode=once` appends new interactions without overwriting existing
ones; `rewrite` replaces. Either works for a first record.

After recording:

1. Verify `x-api-key: REDACTED` in the YAML. The `vcr_config` fixture handles
   this automatically; sanity-check anyway.
2. `make at-vcr` should replay cleanly with no `ANTHROPIC_API_KEY` set.
3. Commit the cassette YAMLs alongside the test changes.

When the extraction prompt, model, or response-shape expectation changes,
the cassette drifts. Re-record with `--record-mode=rewrite`, diff the YAML
in PR review.

## Snapshot workflow

```bash
# After changing an algorithm's output intentionally:
make at-update-snapshots

# Snapshots live here, diff them in PR:
api/tests/acceptance/__snapshots__/
```

Review the diff. If the change is what you intended, commit. If not, the
algorithm changed in a way you did not expect.

## Determinism rules

Snapshots and Hypothesis tests only stay deterministic if callers respect
these:

- **Pass `FIXED_NOW` into `AlgorithmContext.now`** when the algorithm reads
  time. `FIXED_NOW = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)`.
  Defined in `test_algorithm_outputs.py` and `test_decay_properties.py`;
  reuse that constant if you add new snapshot tests.
- **Use `DummyVectorizer`** for embeddings, not real ones. Seeded hash, same
  input gives same vector, related texts cluster loosely.
- **Use `stabilize()`** on any result you snapshot. Skips the floating-point
  last-bit drift and timestamp/config noise.
- **Kuzu paths must be unique per fixture.** `tmp_data_dir` is function-
  scoped. If you build a shared store via `tmp_path_factory.mktemp(...)`,
  session-scope it.

## Assertion helpers

`tests/helpers/assertions.py`:

- `assert_entity_exists(store, concept_id, category=None)` — row presence +
  optional category match.
- `assert_relation(store, src, tgt, min_weight=None)` — edge exists,
  optionally at least this weight.
- `assert_bitemporal(edge, valid_from=None)` — `t_valid_from` / `t_created`
  fields present.
- `assert_top_k_contains(results, expected_ids, k=None)` — ranked results
  have these ids in the top.
- `assert_weights_monotonic_decay(weights)` / `_increase(weights)` — sequence
  behaves as expected.
- `assert_active_set_shape(active_set, min_size, max_size, required_ids)` —
  active-set sanity.
- `assert_no_cross_store_leak(unified_graph, et_store, provider_kg)` —
  ET-originated facts are not echoed into the provider view.
- `assert_wisdom_grounded(wisdom, known_concepts)` — every claim cites a
  known concept.
- `assert_wisdom_refuses_on_empty(pipeline_result)` — pipeline returns
  `nothing_new` / `nothing_novel` / `empty` when given nothing.

Prefer these over hand-rolled assertions. They give readable failure
messages and document intent.

## Known gotchas

**Kuzu mmap exhaustion in combined runs.** Each `GraphStore()` reserves an
8TB virtual region. Running `pytest api/tests/` end-to-end (unit + AT)
spins up enough stores to hit address-space limits on macOS. Workaround:
run `make test` and `make at` separately. Pre-existing issue with the
unit suite's fixture density.

**Physarum `run()` is a no-op by design.** Decay is a read-time transform
via `PhysarumDecay.compute_effective_weight()`. The `run(context)` method
exists only for registry uniformity and returns `None`. Consequences:

- The parametrized algorithm-snapshot test skips physarum (via `_NO_OP_RUN`
  in `test_algorithm_outputs.py`). A dedicated
  `test_physarum_compute_effective_weight_snapshot` exercises real decay
  math over fixture edges and snapshots the decayed weight matrix.
- `test_decay_properties.py` calls `compute_effective_weight()` directly
  instead of `.run()`. If a future author adds another no-op-run algorithm,
  extend `_NO_OP_RUN` and write a dedicated snapshot test for it.

**`recency_weighted` returns concept dicts with embedded temporal fields.**
Hence the long `VOLATILE_KEYS` list. If a new algorithm returns dicts with
additional time-stamped fields, extend `VOLATILE_KEYS`.

**Editable install lives in two pythons.** The repo's `.venv` is the
authoritative runtime for tests. Miniconda base may still carry an older
editable install that Python imports if `which python` resolves there. Run
`which python` or call `.venv/bin/python` explicitly if behavior looks off.

**Silk removed at v0.1.0.** Earlier versions of ET had a `from silk import GraphStore` dependency for legacy API routes and capture modules. These were deleted during publishing prep because the current stack (Kuzu GraphStore + pipeline_v2 + MCP) does not need silk. If you reintroduce silk, update `pyproject.toml` runtime deps and rebuild the venv.

## Extending the framework

New algorithm family:

1. Implement the algorithm under `api/src/extended_thinking/algorithms/<family>/`.
2. `register()` it at import time.
3. `make at-update-snapshots` — `test_algorithm_outputs.py` auto-covers it.
4. Add a property-based test file mirroring the pattern in
   `test_decay_properties.py`: one file per family, 3 to 5 properties,
   `max_examples=5-10`, `deadline=3000-4000`.

New provider:

1. Implement `MemoryProvider` under `providers/`.
2. Register in `providers/__init__.get_provider()`.
3. Add a fixture in `conftest.py` if the provider has a non-trivial read-path
   worth sharing.
4. Extend `test_provider_fusion.py` if the new provider participates in
   `AutoProvider` detection.

New invariant:

1. Add a test to `test_invariants_at_scale.py`. Express it against
   `loaded_graph_small`, which is the canonical realistic-but-small fixture.
2. Prefer introspection (`CALL show_tables()`) over hard-coded constants;
   constants drift silently when the schema changes.

New wisdom shape:

1. Extend `assert_wisdom_grounded()` or `assert_wisdom_refuses_on_empty()`.
2. Add a new assertion helper if the shape needs structural checks that do
   not fit existing helpers. Keep them focused.

## Referenced OSS patterns

- **FakeListLLM / DummyLM**: DSPy's `DummyLM`, LangChain's `FakeListLLM`, Mem0
  `side_effect=[...]` pattern. Our impl is in
  `tests/helpers/fake_llm.py`.
- **DummyVectorizer**: DSPy's `DummyVectorizer`. Ported implementation in
  `tests/helpers/dummy_embed.py` with attribution.
- **syrupy snapshot + custom scrubbing**: `stabilize()` in
  `tests/helpers/snapshot_matchers.py`.
- **pytest-recording (VCR)**: cassette-backed real-model tests. Config lives
  inline per test module via the `vcr_config` fixture.
- **Hypothesis**: property-based tests. Stateful machines (`RuleBasedStateMachine`)
  are deferred until the simple properties stop catching bugs.
