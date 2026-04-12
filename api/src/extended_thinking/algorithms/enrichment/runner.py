"""Enrichment runner (ADR 011 v2).

Composes triggers + sources + gates + cache into a single pass.
Called from `Pipeline.sync()` after the existing concept-extraction
step, gated on `[enrichment] enabled`. Records an `EnrichmentRun`
telemetry node per trigger-fire-per-source-per-concept so thresholds
can be tuned from data.

Orchestration pseudocode (see ADR 011 v2 for full narrative):

    for trigger in active_triggers:
        for (concept_id, reason) in trigger.fired_concepts(ctx):
            for source in active_sources:
                candidates = source.search(...)
                for cand in candidates:
                    if cache says stale: (re)fetch elided for MVP
                    for gate in ordered_gates:
                        verdict = gate.judge(concept, cand)
                        if verdict.reject: break
                        if verdict.auto_accept: break
                    if not rejected:
                        commit KnowledgeNode + Enriches
                record EnrichmentRun with gate_trace + counts

This module stays thin and orchestration-only; the interesting logic
lives in the plugins themselves.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from extended_thinking.algorithms import AlgorithmContext
from extended_thinking.algorithms.enrichment.protocol import (
    Candidate,
    EnrichmentCachePlugin,
    EnrichmentGatePlugin,
    EnrichmentSourcePlugin,
    EnrichmentTriggerPlugin,
    GateVerdict,
    assert_family,
)

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentRunSummary:
    """Per-invocation telemetry aggregated across triggers/sources/concepts.

    Not persisted by itself — the runner writes one `EnrichmentRun`
    node per (trigger, source, concept) tuple, and this in-memory
    summary reports totals back to the caller for logging / API
    responses.
    """

    triggers_fired: int = 0
    candidates_returned: int = 0
    candidates_accepted: int = 0
    knowledge_nodes_created: int = 0
    edges_created: int = 0
    runs_recorded: int = 0
    errors: list[str] = field(default_factory=list)


def run_enrichment(
    *,
    kg,                             # GraphStore
    sources: list[EnrichmentSourcePlugin],
    triggers: list[EnrichmentTriggerPlugin],
    gates: list[EnrichmentGatePlugin],
    cache: EnrichmentCachePlugin | None,
    concept_namespace: str = "memory",
    context_overrides: dict | None = None,
) -> EnrichmentRunSummary:
    """Run one enrichment pass. Safe to call synchronously at sync end.

    Per the ADR, the runner NEVER fetches if `settings.enrichment.enabled`
    is False — callers check that flag before invoking this. Once called,
    we trust that every plugin passed in is active by user config.
    """
    from schema.generated import models as _m

    for s in sources:
        assert_family(s, "enrichment.sources")
    for t in triggers:
        assert_family(t, "enrichment.triggers")
    for g in gates:
        assert_family(g, "enrichment.relevance_gates")
    if cache is not None:
        assert_family(cache, "enrichment.cache")

    summary = EnrichmentRunSummary()

    ctx = AlgorithmContext(
        kg=kg,
        namespace=concept_namespace,
        params=dict(context_overrides or {}),
        now=datetime.now(timezone.utc),
    )

    for trigger in triggers:
        try:
            fired = trigger.fired_concepts(ctx)
        except Exception as e:
            logger.exception("trigger %s failed", trigger.meta.name)
            summary.errors.append(f"trigger {trigger.meta.name}: {e}")
            continue
        if not fired:
            continue
        summary.triggers_fired += len(fired)

        for concept_id, reason in fired:
            concept = kg.get_concept(concept_id)
            if not concept:
                continue

            for source in sources:
                source_kind = source.source_kind()
                enrichment_ns = f"enrichment:{source_kind}"
                started = time.monotonic()
                error_msg = ""
                gate_trace: list[dict] = []
                candidates: list[Candidate] = []

                try:
                    candidates = source.search(
                        concept_id=concept_id,
                        concept_name=concept.get("name", ""),
                        concept_description=concept.get("description", ""),
                        context=ctx,
                    )
                except Exception as e:
                    logger.exception(
                        "source %s failed for %s", source_kind, concept_id,
                    )
                    error_msg = f"{type(e).__name__}: {e}"

                summary.candidates_returned += len(candidates)
                accepted_here = 0

                for cand in candidates:
                    verdict = _run_gate_chain(
                        gates=gates, concept=concept,
                        candidate=cand, context=ctx,
                        trace=gate_trace,
                    )
                    if verdict.outcome == "reject":
                        continue
                    # Accept (or auto_accept) — commit the enrichment.
                    try:
                        _commit_enrichment(
                            kg=kg,
                            concept=concept,
                            candidate=cand,
                            source_kind=source_kind,
                            verdict=verdict,
                            gate_trace=gate_trace,
                            trigger_name=trigger.meta.name,
                            enrichment_namespace=enrichment_ns,
                            summary=summary,
                        )
                        accepted_here += 1
                    except Exception as e:
                        logger.exception(
                            "commit failed for %s <- %s:%s",
                            concept_id, source_kind, cand.external_id,
                        )
                        summary.errors.append(f"commit: {e}")

                summary.candidates_accepted += accepted_here
                duration_ms = int((time.monotonic() - started) * 1000)

                # Telemetry: one EnrichmentRun per (trigger, source, concept).
                try:
                    run_node = _m.EnrichmentRun(
                        id=f"run-{uuid.uuid4().hex[:12]}",
                        name=f"{trigger.meta.name}@{source_kind} for {concept_id}",
                        description=reason,
                        event_type="enrichment_run",
                        trigger_name=trigger.meta.name,
                        source_kind=source_kind,
                        concept_id=concept_id,
                        candidates_returned=len(candidates),
                        candidates_accepted=accepted_here,
                        gate_trace=json.dumps(gate_trace),
                        duration_ms=duration_ms,
                        error=error_msg,
                    )
                    kg.insert(run_node, namespace=enrichment_ns, source="enrichment-runner")
                    summary.runs_recorded += 1
                except Exception as e:
                    logger.exception("EnrichmentRun telemetry failed")
                    summary.errors.append(f"telemetry: {e}")

    return summary


# ── Internal helpers ─────────────────────────────────────────────────

def _run_gate_chain(
    *,
    gates: list[EnrichmentGatePlugin],
    concept: dict,
    candidate: Candidate,
    context: AlgorithmContext,
    trace: list[dict],
) -> GateVerdict:
    """Run gates in order. Short-circuit on reject or auto_accept."""
    last: GateVerdict | None = None
    for gate in gates:
        try:
            v = gate.judge(concept=concept, candidate=candidate, context=context)
        except Exception as e:
            logger.exception("gate %s raised", gate.meta.name)
            trace.append({
                "gate": gate.meta.name, "outcome": "reject",
                "score": 0.0, "reason": f"gate raised: {e}",
            })
            return GateVerdict(outcome="reject", score=0.0,
                               reason=str(e), plugin_name=gate.meta.name)
        trace.append({
            "gate": gate.meta.name,
            "outcome": v.outcome,
            "score": round(v.score, 4),
            "reason": v.reason[:160] if v.reason else "",
        })
        last = GateVerdict(
            outcome=v.outcome, score=v.score, reason=v.reason,
            plugin_name=gate.meta.name,
        )
        if v.outcome in ("reject", "auto_accept"):
            return last
    if last is None:
        # No gates ran — treat as reject to be conservative.
        return GateVerdict(outcome="reject", score=0.0, reason="no gates")
    # All gates passed with plain "accept".
    return last


def _commit_enrichment(
    *,
    kg,
    concept: dict,
    candidate: Candidate,
    source_kind: str,
    verdict: GateVerdict,
    gate_trace: list[dict],
    trigger_name: str,
    enrichment_namespace: str,
    summary: EnrichmentRunSummary,
) -> None:
    """Upsert a KnowledgeNode and attach an Enriches edge."""
    from schema.generated import models as _m

    kn_id = f"kn-{source_kind}-{candidate.external_id}"
    # Idempotent insert: skip if the KnowledgeNode already exists.
    existing = kg.get_node(kn_id) if hasattr(kg, "get_node") else None
    if existing is None:
        kn = _m.KnowledgeNode(
            id=kn_id,
            name=candidate.title,
            description=candidate.abstract[:500] if candidate.abstract else "",
            source_kind=source_kind,
            external_id=candidate.external_id,
            url=candidate.url,
            title=candidate.title,
            abstract=candidate.abstract,
            theme=json.dumps(candidate.themes),
            signal_type=source_kind,
            bearer_id=concept["id"],
        )
        kg.insert(kn, namespace=enrichment_namespace, source="enrichment-runner")
        summary.knowledge_nodes_created += 1

    edge = _m.Enriches(
        id=f"enr-{uuid.uuid4().hex[:12]}",
        source_id=concept["id"],
        target_id=kn_id,
        relation_type="enriches",
        relevance=verdict.score,
        trigger=trigger_name,
        gate_verdicts=json.dumps(gate_trace),
    )
    kg.insert(edge, namespace=enrichment_namespace, source="enrichment-runner")
    summary.edges_created += 1
