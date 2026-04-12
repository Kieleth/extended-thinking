# Contributing to extended-thinking

Thanks for considering a contribution. This project is in active development and open to serious engagement.

## Before you start

Read these, in order:

1. [README.md](README.md) — the pitch and current state
2. [project-invariants.md](project-invariants.md) — five non-negotiable product truths
3. [docs/configuration.md](docs/configuration.md) — config surface, tiers, drop-ins, every key
4. [docs/research-kg-nature-inspiration.md](docs/research-kg-nature-inspiration.md) — the research foundations
5. [docs/ADR/](docs/ADR/) — architecture decision records

Every change that affects architecture needs an ADR. We keep decisions legible so future contributors (including our future selves) understand why, not just what.

## Adding a new algorithm

Algorithms are the primary extension point. They slot into the pluggable architecture (ADR 003) without touching core code.

### Step 1: Decide the family

| Family | Purpose |
|--------|---------|
| `decay` | Edge weight decay over time |
| `activation` | Spreading activation / find-related ranking |
| `resolution` | Entity matching / merging |
| `bridges` | Rich-club hub detection |
| `bow_tie` | Core-periphery structural analysis |
| `recombination` | DMN-inspired serendipity |

If your idea doesn't fit, propose a new family in an ADR.

### Step 2: Cite your research

Algorithms without research citations are heuristics and should not be shipped. If you're inventing something, cite a related paper as conceptual grounding, or mark the algorithm clearly as experimental.

### Step 3: Implement the protocol

```python
from extended_thinking.algorithms.protocol import AlgorithmContext, AlgorithmMeta
from extended_thinking.algorithms.registry import register


class MyAlgorithm:
    meta = AlgorithmMeta(
        name="my_algorithm",
        family="decay",  # or one of: activation, resolution, bridges, bow_tie, recombination
        description="One-line purpose",
        paper_citation="Author et al. Year. Journal or arxiv ID.",
        parameters={"threshold": 0.5},  # defaults
        temporal_aware=True,  # if you honor context.as_of
    )

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def run(self, context: AlgorithmContext):
        kg = context.kg
        # ... your logic
        return result


register(MyAlgorithm)
```

### Step 4: Tests

Every algorithm needs tests against:

- Empty graph (returns empty, doesn't crash)
- Minimal valid input (sanity check)
- Boundary conditions (min thresholds, top_k limits)
- Temporal behavior if `temporal_aware=True`
- Deterministic output (same input → same output)

See `api/tests/test_algorithms.py` for examples.

### Step 5: Write an ADR

If your algorithm changes how a family works, or if it's the first in a new family, write an ADR in `docs/ADR/`. Template:

```markdown
# ADR NNN: Title

**Status:** Proposed → Accepted when merged
**Date:** YYYY-MM-DD
**Depends on:** ADR 00X

## Context
Why does this need to exist?

## Decision
What are we doing?

## Consequences
Positive, negative, non-consequences. Be honest about tradeoffs.

## References
Research + related ADRs.
```

### Step 6: Update ALGORITHMS.md

Add an entry describing your algorithm, its parameters, and its citation. Same format as existing entries.

## Adding a new MCP tool

MCP tools are the primary user surface. Adding one is heavier than adding an algorithm because it's a stable contract — once published, users will build workflows on the name and schema.

Checklist:

1. New tool names start fresh — never rename existing tools. Users depend on them.
2. Response formats are additive only. Add keys; never remove or rename.
3. Inputs should have sensible defaults. Required args should be minimal.
4. Write an ADR if the tool represents a new product capability.
5. Tests for the tool go in `api/tests/test_mcp_tools.py` (or add new file for a tool family).

## Adding a new MemoryProvider

Providers are data-source adapters (D+I layer per ADR 001). Each provider reads from a specific memory system and yields `MemoryChunk` objects.

Contract is in `api/src/extended_thinking/providers/protocol.py`. The minimum is:

- `name: str`
- `get_recent(since, limit) -> list[MemoryChunk]`
- `get_stats() -> dict`

`get_knowledge_graph() -> KnowledgeGraphView | None` is optional; return None if your source has no structured KG.

See `providers/folder.py` for the simplest reference implementation.

## Testing

```bash
pip install -e api/
python -m pytest api/tests/
```

The suite should stay green on every PR. If a pre-existing test breaks unrelated to your change, flag it in the PR description; don't silently fix it.

## Commits and PRs

- One logical change per PR. Splitting is cheap; mixed PRs are hard to review.
- Commit messages: what changed, why. Avoid "fix bug" — name the bug.
- PR description: link the ADR if there is one, summarize user-visible changes.
- Tests required for new code paths. Exceptions need justification.

## Code style

- Python 3.12+ syntax. Modern types (`list[str]`, `str | None`).
- Ruff for linting (configuration in `pyproject.toml`).
- Docstrings on public classes and functions. Brief — describe the contract, not the implementation.
- Avoid abbreviations in identifiers unless they're already standard (`kg` is fine, `cncpt` is not).

## License

By contributing, you agree that your contributions will be licensed under the Functional Source License 1.1 (Apache-2.0 Future), the same license as the rest of the project. See [LICENSE.md](LICENSE.md).

## Questions

Open an issue to discuss. For design questions, prefer an ADR draft in your PR over a comment thread.
