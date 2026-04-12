# Product Invariants

These are non-negotiable truths about extended-thinking. If any is violated, the product is broken — regardless of whether the code compiles or the tests pass.

> **Note on audiences.** ET serves two audiences: humans synthesizing their memory, and programmatic consumers using ET as a typed bitemporal state store (see [ADR 013](docs/ADR/013-research-backbone-audience.md)). Every invariant below applies to both. Where the mechanism differs by audience it is called out inline — but the invariant itself never weakens.

---

## 1. Every insight must trace back to evidence

If the system says "you tend toward X" or "consider Y," there must be a concrete trail: which sessions, which conversations, which concepts led to this conclusion. An insight without evidence is a hallucination — it's the system making things up. The user must be able to verify any claim the system makes about their thinking.

*Programmatic consumers:* the guarantee extends verbatim. `et_write_rationale` (ADR 013 C4) verifies every cited node id resolves before commit; an ungrounded rationale cannot enter the graph even by accident.

**Violation = system is untrustworthy.**

---

## 2. The system never forgets what it's seen

Once a session is captured and processed, its concepts and connections persist. The user's cognitive graph only grows (or evolves) — it never silently loses data. If the user had 200 concepts yesterday, they have at least 200 today. Concepts can be merged or reclassified, but never vanish.

*Programmatic consumers:* every typed-node write lands in the bitemporal log (ADR 013 C1). Supersession, never deletion. A consumer's audit trail is as durable as the memory graph's.

**Violation = user loses trust, stops relying on it.**

---

## 3. Enrichment is always relevant to the user's actual thinking

Suggested reads, new concepts, external content must connect to concepts the user has actually engaged with — not generic recommendations. If the system suggests an article about Kubernetes but the user has never thought about container orchestration, that's noise. Every suggestion must link to at least one concept in their graph.

**Violation = system becomes spam, user ignores it.**

---

## 4. The system distinguishes what the user said from what it inferred

Raw fragments (what the user actually typed/said) must be clearly separated from extracted concepts (what the system interpreted) and from insights (what the system synthesized). The user must always know: is this MY thought or the SYSTEM's interpretation of my thought?

*Programmatic consumers:* typed nodes are a fourth class — externally authored — and carry a `source` property identifying the writing consumer. The three memory-side classes (raw/extracted/synthesized) stay disjoint from the typed-node class.

**Violation = the system puts words in the user's mouth, erodes agency.**

---

## 5. Processing never blocks the user

The user should never wait for the system to finish thinking before they can see their graph, read an insight, or navigate. All heavy work (capture, extraction, wisdom) runs asynchronously. The UI is always responsive, always shows the latest available state.

*Programmatic consumers:* their separate sync-write path (ADR 013 C3) is opt-in and only affects consumers that need write-then-read coherence. The user-facing memory-ingest path stays async. Two paths, one invariant: the human user never waits.

**Violation = the system feels slow, user abandons it.**
