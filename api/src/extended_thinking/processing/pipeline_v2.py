"""Provider-based DIKW pipeline.

Clean replacement for the Silk-based pipeline. Reads from any MemoryProvider,
stores concepts in ConceptStore (SQLite), generates wisdom via Opus.

Usage:
    provider = get_provider({"provider": "auto"})
    store = ConceptStore(Path("~/.extended-thinking/concepts.db"))
    pipeline = Pipeline(provider, store)

    await pipeline.sync()          # Extract concepts from new memories
    wisdom = await pipeline.generate_wisdom()  # Opus synthesis
    insight = await pipeline.get_insight()      # Full flow
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from extended_thinking.providers.protocol import MemoryChunk, MemoryProvider
from extended_thinking.processing.concept_store import ConceptStore
from extended_thinking.processing.extractor import ExtractedConcept, extract_concepts_from_chunks
from extended_thinking.storage.vector_protocol import VectorStore

logger = logging.getLogger(__name__)

# Minimum concepts before wisdom generation is meaningful
MIN_CONCEPTS_FOR_WISDOM = 5


def get_ai_provider(name: str | None = None):
    """Get the AI provider (Anthropic/OpenAI) for LLM calls."""
    from extended_thinking.ai.registry import get_provider
    return get_provider(name)


class Pipeline:
    """DIKW pipeline: MemoryProvider → concepts → patterns → wisdom.

    Stateless except for the ConceptStore. Can be called repeatedly.
    Each sync() is idempotent — re-extracting the same chunks merges
    concepts by name (frequency increments).
    """

    def __init__(self, provider: MemoryProvider, concept_store: ConceptStore,
                 vectors: VectorStore | None = None):
        self._provider = provider
        self._store = concept_store
        self._vectors = vectors
        self._last_sync: str | None = None

    @classmethod
    def from_storage(cls, provider: MemoryProvider, storage) -> Pipeline:
        """Create pipeline from a StorageLayer."""
        return cls(provider, storage.kg, vectors=storage.vectors)

    @property
    def store(self) -> ConceptStore:
        return self._store

    @property
    def provider(self) -> MemoryProvider:
        return self._provider

    @property
    def vectors(self) -> VectorStore | None:
        return self._vectors

    # ── Sync: extract concepts from new memories ─────────────────────

    async def sync(
        self,
        limit: int = 100,
        *,
        on_progress=None,
    ) -> dict:
        """Pull recent memories from provider, extract concepts.

        Idempotent: tracks processed chunk IDs in SQLite. Same chunks
        are never re-processed, even across restarts.

        Args:
            limit: provider get_recent() cap.
            on_progress: optional callback `(phase: str, detail: str) -> None`
                called at each phase boundary. Phases in order:
                    read, filter, index, extract, resolve, relate, enrich.
                Lets the CLI narrate what's happening without coupling
                Pipeline to a specific display layer.

        Returns summary: chunks_processed, concepts_extracted.
        """
        def _emit(event: str, phase: str, detail: str = "") -> None:
            if on_progress is not None:
                try:
                    on_progress(event, phase, detail)
                except Exception:
                    logger.exception("on_progress callback raised; continuing")

        def _start(phase: str) -> None:
            _emit("start", phase)

        def _tick(phase: str, detail: str) -> None:
            _emit("tick", phase, detail)

        def _progress(phase: str, detail: str) -> None:
            """Phase-done shortcut (kept for backwards compat inside this file)."""
            _emit("done", phase, detail)

        _start("read")
        all_chunks = self._provider.get_recent(since=self._last_sync, limit=limit)
        _progress("read", f"{len(all_chunks)} chunks")
        if not all_chunks:
            # No provider data at all — enrichment (ADR 011 v2) still runs
            # because triggers watch the existing graph, not new chunks.
            summary = self._run_enrichment_if_enabled()
            if summary is not None:
                _progress("enrich",
                          f"{summary.knowledge_nodes_created} knowledge nodes, "
                          f"{summary.runs_recorded} runs")
            return self._build_sync_result(
                {"chunks_processed": 0, "concepts_extracted": 0,
                 "status": "no_new_data"},
                summary,
            )

        # Filter out already-processed chunks
        _start("filter")
        unprocessed_ids = self._store.filter_unprocessed([c.id for c in all_chunks])
        chunks = [c for c in all_chunks if c.id in set(unprocessed_ids)]

        # Filter out source code chunks (code is ephemeral, not thinking)
        pre_filter = len(chunks)
        chunks = [c for c in chunks if _is_thinking_content(c)]
        filtered_out = pre_filter - len(chunks)
        if filtered_out:
            logger.info("Content filter: %d thinking, %d code skipped", len(chunks), filtered_out)
        deduped = len(all_chunks) - pre_filter
        _progress(
            "filter",
            f"{len(chunks)} thinking"
            + (f", {filtered_out} code skipped" if filtered_out else "")
            + (f", {deduped} already processed" if deduped else ""),
        )

        if not chunks:
            summary = self._run_enrichment_if_enabled()
            if summary is not None:
                _progress("enrich",
                          f"{summary.knowledge_nodes_created} knowledge nodes, "
                          f"{summary.runs_recorded} runs")
            return self._build_sync_result(
                {"chunks_processed": 0, "concepts_extracted": 0,
                 "filtered_code": filtered_out, "status": "no_new_data"},
                summary,
            )

        # Store chunks in VectorStore for semantic retrieval
        if self._vectors is not None:
            _start("index")
            for i, chunk in enumerate(chunks, 1):
                self._vectors.add(
                    id=chunk.id,
                    text=chunk.content,
                    metadata={
                        "source": chunk.source or "",
                        "timestamp": chunk.timestamp or "",
                        "source_type": "provider",
                        "chunk_type": "episodic",
                        **(
                            {k: v for k, v in chunk.metadata.items() if isinstance(v, (str, int, float, bool))}
                            if chunk.metadata else {}
                        ),
                    },
                )
                # Tick every 5 chunks so the reporter has fresh state for
                # its 80ms redraw loop; cheap (just updates a variable).
                if i % 5 == 0 or i == len(chunks):
                    _tick("index", f"{i}/{len(chunks)}")
            _progress("index", f"{len(chunks)} embeddings")

        # ADR 013 C8: providers returning structured data can skip extraction.
        # Conversation providers (default True) still run the Haiku pass.
        # Providers opt out by setting `extract_concepts = False`.
        extract_enabled = getattr(self._provider, "extract_concepts", True)

        # Get existing concept names for dedup hints
        existing_names = [c["name"] for c in self._store.list_concepts(limit=200)]

        concepts: list[ExtractedConcept] = []
        all_chunk_batches: list[list] = []

        if extract_enabled:
            # Batch extraction: ~20 chunks per call for richer, more diverse concepts
            # (one giant batch yields sparse high-level concepts; small batches yield specifics)
            BATCH_SIZE = 20
            n_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE
            _start("extract")
            for batch_idx, i in enumerate(range(0, len(chunks), BATCH_SIZE), 1):
                _tick("extract", f"batch {batch_idx}/{n_batches} · haiku")
                batch = chunks[i:i + BATCH_SIZE]
                batch_concepts = await extract_concepts_from_chunks(
                    batch,
                    existing_concept_names=existing_names,
                )
                concepts.extend(batch_concepts)
                all_chunk_batches.append((batch, batch_concepts))
            _progress(
                "extract",
                f"{len(concepts)} concepts · {n_batches} batch{'es' if n_batches != 1 else ''} · haiku",
            )
        else:
            # Structured-ingest mode: chunks still get stored + provenance
            # recorded downstream, but we don't emit Concept nodes for them.
            logger.info(
                "provider %s opted out of concept extraction (extract_concepts=False); "
                "storing %d chunks with provenance only",
                self._provider.name, len(chunks),
            )

        # Store extracted concepts with entity resolution + provenance (per batch)
        if extract_enabled and all_chunk_batches:
            _start("resolve")
        supersession_count = 0
        merge_count = 0
        new_count = 0
        concepts_seen = 0
        total_concepts = sum(len(bc) for _, bc in all_chunk_batches)
        for batch_chunks, batch_concepts in all_chunk_batches:
            # Resolve resolution plugins once per batch (cheap, reused across concepts)
            resolution_algs = _get_resolution_algorithms(self._vectors is not None)
            alg_ctx_base = _build_algorithm_context(self._store, self._vectors)

            for concept in batch_concepts:
                concept_id = _normalize_id(concept.name)

                # Entity resolution: try each active plugin, first match wins
                similar = _try_resolve(resolution_algs, alg_ctx_base,
                                       concept.name, concept.description)
                if similar and similar["id"] != concept_id:
                    logger.info("Entity resolution [%s]: '%s' merged into '%s'",
                                similar.get("_resolved_by", "?"), concept.name, similar["name"])
                    self._store.merge_concept(concept_id, similar["id"])
                    concept_id = similar["id"]
                    merge_count += 1
                else:
                    self._store.add_concept(
                        concept_id=concept_id,
                        name=concept.name,
                        category=concept.category,
                        description=concept.description,
                        source_quote=concept.source_quote,
                    )
                    new_count += 1

                # Contradiction detection: expire edges from superseded concepts
                # (ADR 002: bitemporal with supersession)
                for old_name in (concept.supersedes or []):
                    old_id = _normalize_id(old_name)
                    # Only supersede if the old concept actually exists
                    if self._store.get_concept(old_id) and old_id != concept_id:
                        # Mark deprecated: the old concept no longer represents current thinking
                        # (we don't delete, we just flag the transition)
                        if hasattr(self._store, "supersede_edge"):
                            # Expire any RelatesTo edges from the old concept
                            existing_rels = self._store.get_relationships(old_id)
                            for rel in existing_rels:
                                if self._store.supersede_edge(
                                    rel["source_id"], rel["target_id"],
                                    new_edge_ref=concept_id,
                                    reason=f"superseded by new concept: {concept.name}",
                                ):
                                    supersession_count += 1

                # Record provenance: match source_quote to the chunk in THIS batch
                source_chunk = None
                quote = concept.source_quote.strip()
                if quote:
                    for chunk in batch_chunks:
                        if quote[:80] in chunk.content:
                            source_chunk = chunk
                            break
                if source_chunk is None and batch_chunks:
                    source_chunk = batch_chunks[0]

                self._store.add_provenance(
                    entity_id=concept_id,
                    source_provider=self._provider.name,
                    source_chunk_id=source_chunk.id,
                    llm_model="haiku",
                    source=source_chunk.source or "",
                    source_type=_infer_source_type(source_chunk),
                )
                concepts_seen += 1
                if total_concepts and (concepts_seen % 5 == 0 or concepts_seen == total_concepts):
                    _tick("resolve", f"{concepts_seen}/{total_concepts}")

        if extract_enabled and (merge_count or new_count):
            _progress(
                "resolve",
                f"{merge_count} merged, {new_count} new"
                + (f", {supersession_count} superseded" if supersession_count else ""),
            )

        # Mark chunks as processed (idempotent across restarts).
        # t_source_created carries the original write-time (conversation
        # timestamp, file mtime). t_ingested is set to now inside the store.
        for chunk in chunks:
            self._store.mark_chunk_processed(
                chunk.id,
                source=chunk.source or "",
                source_type=_infer_source_type(chunk),
                t_source_created=chunk.timestamp or "",
            )

        # Detect co-occurrence relationships per batch
        # (each extraction call sees a batch, concepts within a batch are related)
        n_rels_before = self._store.get_stats().get("total_relationships", 0)
        if all_chunk_batches:
            _start("relate")
        for batch_chunks, batch_concepts in all_chunk_batches:
            self._detect_relationships(batch_chunks, batch_concepts)
        n_rels_after = self._store.get_stats().get("total_relationships", 0)
        rel_delta = n_rels_after - n_rels_before
        if rel_delta:
            _progress("relate", f"{rel_delta} co-occurrence edge{'s' if rel_delta != 1 else ''}")

        # Update sync marker
        if chunks:
            self._last_sync = max(c.timestamp for c in chunks)

        # ADR 011 v2 — proactive enrichment at sync end.
        # Entirely gated on [enrichment] enabled; internal-only users see
        # no external calls, no runner invocation, no telemetry writes.
        from extended_thinking.config import settings as _s
        if _s.enrichment.enabled:
            _start("enrich")
        enrichment_summary = self._run_enrichment_if_enabled()
        if enrichment_summary is not None:
            _progress(
                "enrich",
                f"{enrichment_summary.knowledge_nodes_created} knowledge nodes, "
                f"{enrichment_summary.runs_recorded} run{'s' if enrichment_summary.runs_recorded != 1 else ''}",
            )

        logger.info(
            "Synced: %d chunks → %d concepts (%d code filtered, %d superseded, "
            "%d enrichment runs)",
            len(chunks), len(concepts), filtered_out, supersession_count,
            enrichment_summary.runs_recorded if enrichment_summary else 0,
        )
        return self._build_sync_result({
            "chunks_processed": len(chunks),
            "concepts_extracted": len(concepts),
            "filtered_code": filtered_out,
            "superseded": supersession_count,
            "status": "synced",
        }, enrichment_summary)

    def _build_sync_result(self, base: dict, enrichment_summary) -> dict:
        """Attach an enrichment subsection to a sync() result when the
        runner produced one. Kept as a method so every exit point — the
        happy path and the two early-returns — wires enrichment the same
        way. A None summary means the master toggle was off; the key is
        elided so callers that don't care see the same shape as pre-011."""
        if enrichment_summary is None:
            return base
        base["enrichment"] = {
            "triggers_fired": enrichment_summary.triggers_fired,
            "candidates_returned": enrichment_summary.candidates_returned,
            "candidates_accepted": enrichment_summary.candidates_accepted,
            "knowledge_nodes_created": enrichment_summary.knowledge_nodes_created,
            "edges_created": enrichment_summary.edges_created,
            "runs_recorded": enrichment_summary.runs_recorded,
            "errors": enrichment_summary.errors[:10],
        }
        return base

    def _run_enrichment_if_enabled(self):
        """Invoke the enrichment runner if `[enrichment] enabled = true`.

        Returns `EnrichmentRunSummary | None`. Returns None (silently)
        when the master toggle is off — zero external calls, zero new
        graph writes. ADR 011 v2's internal-only contract.
        """
        from extended_thinking.config import settings
        if not settings.enrichment.enabled:
            return None

        from extended_thinking.algorithms import (
            build_config_from_settings,
            get_active,
        )
        from extended_thinking.algorithms.enrichment.runner import (
            run_enrichment,
        )

        algo_config = build_config_from_settings(settings.algorithms)
        sources = get_active("enrichment.sources", algo_config)
        triggers = get_active("enrichment.triggers", algo_config)
        gates = get_active("enrichment.relevance_gates", algo_config)
        cache_plugins = get_active("enrichment.cache", algo_config)
        cache = cache_plugins[0] if cache_plugins else None

        if not sources or not triggers or not gates:
            logger.info(
                "enrichment enabled but missing plugins "
                "(sources=%d, triggers=%d, gates=%d) — skipping",
                len(sources), len(triggers), len(gates),
            )
            return None

        return run_enrichment(
            kg=self._store,
            sources=sources,
            triggers=triggers,
            gates=gates,
            cache=cache,
            concept_namespace=settings.enrichment.concept_namespace,
        )

    # ── Wisdom: Opus synthesis ───────────────────────────────────────

    async def generate_wisdom(self, force: bool = False) -> dict | None:
        """Generate wisdom from the concept graph via Opus.

        Returns wisdom dict or None if not enough data.
        """
        stats = self._store.get_stats()
        if stats["total_concepts"] < MIN_CONCEPTS_FOR_WISDOM and not force:
            logger.info("Not enough concepts (%d) for wisdom", stats["total_concepts"])
            return None

        from extended_thinking.config import settings
        wisdom_provider = settings.wisdom_provider or None
        wisdom_model = settings.wisdom_model

        try:
            ai = get_ai_provider(wisdom_provider)
        except RuntimeError:
            logger.error("No AI provider configured")
            return None

        # Build context from graph structure, not just frequency
        active = self._store.active_nodes(k=15)  # sparse active set
        overview = self._store.get_graph_overview()
        bridges = overview.get("bridges", [])[:10]  # rich-club hubs
        clusters = overview.get("clusters", [])[:8]  # top semantic clusters
        previous_wisdoms = self._store.list_wisdoms(limit=5)
        provider_stats = self._provider.get_stats()

        prompt = self._build_wisdom_prompt(
            active=active,
            bridges=bridges,
            clusters=clusters,
            previous=previous_wisdoms,
            provider_stats=provider_stats,
        )

        try:
            response = await ai.complete(
                messages=[{"role": "user", "content": prompt}],
                model=wisdom_model,
            )
        except Exception as e:
            logger.error("Opus call failed: %s", e)
            return None

        parsed = self._parse_wisdom(response)
        if not parsed:
            logger.error("Failed to parse Opus response")
            return None

        # F5: Opus can refuse to generate wisdom when the graph doesn't support it
        if parsed.get("type") == "nothing_novel":
            logger.info("Opus declined: %s", parsed.get("why", "no grounded insight"))
            return {
                "type": "nothing_novel",
                "title": parsed.get("title", "Nothing novel to surface"),
                "why": parsed.get("why", "Graph doesn't support a grounded insight right now"),
                "action": "none",
                "based_on_concepts": stats["total_concepts"],
            }

        # Resolve concept names → IDs via normalization + entity resolution
        related_ids = []
        for name in parsed.get("related_concepts", []):
            cid = _normalize_id(name)
            if self._store.get_concept(cid):
                related_ids.append(cid)
            else:
                # Try fuzzy match
                similar = self._store.find_similar_concept(name, threshold=0.75)
                if similar:
                    related_ids.append(similar["id"])

        # Fallback: link to currently active concepts
        if not related_ids:
            related_ids = [c["id"] for c in active[:6]]

        wisdom_id = self._store.add_wisdom(
            title=parsed.get("title", "Untitled"),
            description=f"**Why:** {parsed.get('why', '')}\n\n**Action:** {parsed.get('action', '')}",
            wisdom_type=parsed.get("type", "wisdom"),
            based_on_sessions=provider_stats.get("total_sessions", provider_stats.get("total_memories", 0)),
            based_on_concepts=stats["total_concepts"],
            related_concept_ids=related_ids,
        )

        # Writing wisdom back to providers is disabled by default to prevent echo loops.
        # ET's KG is the source of truth for wisdom, not the provider.

        result = {
            "id": wisdom_id,
            "type": parsed.get("type", "wisdom"),
            "title": parsed.get("title", ""),
            "why": parsed.get("why", ""),
            "action": parsed.get("action", ""),
            "based_on_concepts": stats["total_concepts"],
        }
        logger.info("Generated wisdom: %s", result["title"][:50])
        return result

    # ── Get insight: full flow ───────────────────────────────────────

    async def get_insight(self) -> dict:
        """Full flow: sync → check for pending → generate if needed → return."""

        # Check for pending wisdom first
        pending = self._store.list_wisdoms(status="pending")
        if pending:
            wisdom = pending[0]
            self._store.update_wisdom_status(wisdom["id"], "seen")
            return {
                "type": wisdom["wisdom_type"],
                "id": wisdom["id"],
                "insight": {"title": wisdom["title"], "description": wisdom["description"]},
                "status": "ready",
            }

        # Sync new data
        sync_result = await self.sync()

        # Generate wisdom if we have enough concepts
        if sync_result["concepts_extracted"] > 0 or self._store.get_stats()["total_concepts"] >= MIN_CONCEPTS_FOR_WISDOM:
            wisdom = await self.generate_wisdom()
            if wisdom:
                return {
                    "type": wisdom["type"],
                    "id": wisdom["id"],
                    "insight": {"title": wisdom["title"], "description": f"**Why:** {wisdom['why']}\n\n**Action:** {wisdom['action']}"},
                    "status": "fresh",
                }

        # Nothing new
        return {
            "type": "nothing_new",
            "insight": {
                "title": "Nothing new to think about",
                "description": f"No new data since last sync. {self._store.get_stats()['total_concepts']} concepts tracked.",
            },
            "status": "nothing_new",
        }

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "provider": self._provider.get_stats(),
            "concepts": self._store.get_stats(),
            "last_sync": self._last_sync,
        }

    # ── Private ──────────────────────────────────────────────────────

    def _detect_relationships(self, chunks: list[MemoryChunk],
                               concepts: list[ExtractedConcept]) -> None:
        """Detect co-occurrence: concepts extracted together are related.

        Each extraction call sees a batch of chunks and returns multiple concepts.
        Map each concept back to its originating chunk via source_quote matching,
        then build:
        - Strong edges between concepts in the same chunk
        - Weaker edges between concepts in the same batch (cross-chunk)
        """
        from collections import defaultdict

        # Map each concept to its source chunk via source_quote substring match
        concept_to_chunk: dict[str, str] = {}
        chunk_to_source: dict[str, str] = {c.id: c.source for c in chunks}
        # Per-concept override for source_created_at (if extractor detected an
        # inline date in the chunk content, honor it over the chunk's own ts).
        concept_source_ts: dict[str, str] = {}
        chunk_ts: dict[str, str] = {c.id: (c.timestamp or "") for c in chunks}

        for concept in concepts:
            cid = _normalize_id(concept.name)
            quote = concept.source_quote.strip()
            best_chunk_id = None
            if quote:
                # Find the chunk whose content contains the source_quote
                for chunk in chunks:
                    if quote[:80] in chunk.content:
                        best_chunk_id = chunk.id
                        break
            if best_chunk_id is None and chunks:
                best_chunk_id = chunks[0].id  # fallback: first chunk of batch
            concept_to_chunk[cid] = best_chunk_id
            # D: prefer extractor-detected inline date, else chunk timestamp
            inline = getattr(concept, "source_created_at", "") or ""
            concept_source_ts[cid] = inline or chunk_ts.get(best_chunk_id, "")

        # Group concepts by their source chunk
        chunk_concepts: dict[str, list[str]] = defaultdict(list)
        for cid, chunk_id in concept_to_chunk.items():
            chunk_concepts[chunk_id].append(cid)

        # Strong edges: concepts from the same chunk (weight 2.0).
        # Edge t_valid_from comes from the source chunk's timestamp so that
        # old conversations produce old edges (source-age-aware decay).
        for chunk_id, concept_ids in chunk_concepts.items():
            source = chunk_to_source.get(chunk_id, "")
            edge_vf = chunk_ts.get(chunk_id) or None
            for i, c1 in enumerate(concept_ids):
                # If either concept has an inline-detected source date, use
                # the older of the pair (stays faithful to earliest evidence).
                for c2 in concept_ids[i + 1:]:
                    ts_a = concept_source_ts.get(c1) or edge_vf
                    ts_b = concept_source_ts.get(c2) or edge_vf
                    pair_vf = _older_iso(ts_a, ts_b)
                    self._store.add_relationship(
                        c1, c2, weight=2.0,
                        context=f"Same chunk: {source}",
                        t_valid_from=pair_vf,
                    )
            if len(concept_ids) >= 2:
                self._store.add_co_occurrence(chunk_id, concept_ids, context=source)

        # F2: Same-batch edges removed. They were extraction-coincidence noise,
        # not semantic relatedness. Temporal correlation is already in timestamps.

    def _build_wisdom_prompt(self, active: list[dict],
                              bridges: list[dict],
                              clusters: list[dict],
                              previous: list[dict],
                              provider_stats: dict) -> str:
        """Build prompt using graph structure + source grounding."""

        def _format_concept_with_sources(c: dict) -> str:
            sources = self._store.get_concept_sources(c["id"]) if hasattr(self._store, "get_concept_sources") else []
            source_summary = ""
            if sources:
                unique_sources = {(s["source"], s["source_type"]) for s in sources if s["source"]}
                if unique_sources:
                    parts = [f"{stype}: {src}" for src, stype in list(unique_sources)[:3]]
                    source_summary = f" [sources: {'; '.join(parts)}]"
            return f"- {c['name']} ({c['category']}): {c['description'][:140]}{source_summary}"

        active_text = "\n".join(_format_concept_with_sources(c) for c in active) or "(no active concepts)"

        bridges_text = "\n".join(_format_concept_with_sources(c) for c in bridges) or "(no bridges yet)"

        clusters_text = ""
        for i, cl in enumerate(clusters):
            if cl["size"] < 3:
                continue
            member_names = [c["name"] for c in cl["concepts"][:8]]
            clusters_text += f"\nCluster {i+1} ({cl['size']} concepts): {', '.join(member_names)}"
        clusters_text = clusters_text or "(no clusters yet)"

        previous_text = "\n".join(
            f"- [{w['status']}] {w['title']}: {w['description'][:180]}"
            for w in previous
        ) or "(first wisdom, no prior advice)"

        return f"""You are a cognitive advisor analyzing the structure of someone's thinking.

Their thinking has been extracted into a knowledge graph. You see the graph's most important structural signals, with source paths for each concept (use these to reason about which systems the concepts live in).

## Currently Active (top concepts by recency + connectivity)
{active_text}

## Bridges (high-connectivity concepts)
{bridges_text}

## Semantic Clusters
{clusters_text}

## Previous Wisdom (for context, avoid repeating)
{previous_text}

## Memories analyzed
{provider_stats.get('total_memories', 0)} from {provider_stats.get('detected_provider', '?')}

## Your Task

Generate ONE grounded insight. Options:

**Cross-pollination (encouraged, but must be grounded):** Connect concepts from different sources if they genuinely relate. When you bridge concepts from different systems, describe HOW they'd interact in reality. If they can't interact directly, describe WHAT WOULD NEED TO EXIST for the bridge to become real (e.g. "a schema contract layer between X and Y"). Never invent systems or mechanisms. Never claim a composition works if it doesn't.

**Within-system pattern:** Something the user wouldn't easily see by reading one cluster. A tension, a blind spot, a hidden meta-theme.

**Refusal is valid:** If the graph doesn't support a grounded insight — concepts are too disconnected, too shallow, or any composition would be speculative — return `"type": "nothing_novel"` with a one-line explanation in `why`.

Rules:
- Use concept names verbatim. They must appear in the active/bridges/clusters lists above.
- Reference specific source paths when grounding cross-system claims.
- If you propose an action, it must be concrete and testable. No vague "explore X more."
- Don't hallucinate mechanisms. If concept A is a spec and concept B is runtime code, their interaction needs explicit translation.

Return JSON:
```json
{{
  "type": "wisdom" or "nothing_novel",
  "title": "Short sharp title (under 80 chars)",
  "why": "Specific. Reference concept names. If cross-source, name the sources. If grounded composition requires a missing piece, name that piece.",
  "action": "Concrete next step, or 'none' for nothing_novel",
  "related_concepts": ["exact name 1", "exact name 2", "exact name 3"]
}}
```

Return ONLY the JSON."""

    def _parse_wisdom(self, response: str) -> dict | None:
        """Parse Opus response. Delegates to the battle-tested wisdom parser."""
        from extended_thinking.processing.wisdom_parser import _parse_wisdom_response
        return _parse_wisdom_response(response)


def _normalize_id(name: str) -> str:
    """Normalize a concept name to a stable ID."""
    return name.lower().strip().replace(" ", "-").replace("/", "-")[:60]


def _older_iso(a: str | None, b: str | None) -> str | None:
    """Return the earlier of two ISO timestamps. None-safe: missing loses."""
    if not a:
        return b
    if not b:
        return a
    return a if a < b else b


def _get_resolution_algorithms(has_vectors: bool) -> list:
    """Get active resolution plugins from the registry.

    Order matters: pipeline tries each in turn, first match wins.
    Default: sequence_matcher first (fast), embedding_cosine second
    (catches synonyms). Users customize via `[algorithms.resolution] order`.

    If no VectorStore is configured, embedding-based plugins are dropped
    regardless of config (they would return None anyway).
    """
    from extended_thinking.algorithms import build_config_from_settings, get_active
    from extended_thinking.config import settings

    config = build_config_from_settings(settings.algorithms)
    # Guarantee a default order when the user hasn't configured one.
    config.setdefault("algorithms", {}).setdefault(
        "resolution", ["sequence_matcher", "embedding_cosine"],
    )

    algs = get_active("resolution", config)
    if not has_vectors:
        algs = [a for a in algs if getattr(a, "meta", None) and a.meta.name != "embedding_cosine"]
    return algs


def _build_algorithm_context(kg, vectors):
    from extended_thinking.algorithms import AlgorithmContext
    return AlgorithmContext(kg=kg, vectors=vectors)


def _try_resolve(algorithms: list, context, query_name: str,
                 query_description: str = "") -> dict | None:
    """Try each resolution plugin in order; return first match.

    Plugins' resolve() signatures vary (some take description, some don't).
    We use inspection to pass only accepted kwargs.
    """
    import inspect
    for alg in algorithms:
        if not hasattr(alg, "resolve"):
            continue
        sig = inspect.signature(alg.resolve)
        if "query_description" in sig.parameters:
            match = alg.resolve(context, query_name, query_description=query_description)
        else:
            match = alg.resolve(context, query_name)
        if match is not None:
            # Tag which plugin resolved for logging; don't mutate original
            tagged = dict(match)
            tagged["_resolved_by"] = alg.meta.name
            return tagged
    return None


# File extensions that indicate source code (ephemeral, not thinking)
_CODE_EXTENSIONS = {
    ".py", ".rs", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".c", ".cpp",
    ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".bash",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".csv", ".sql", ".lock",
    ".conf", ".cfg", ".ini", ".env", ".dockerfile",
}

# Extensions that represent thinking content
_THINKING_EXTENSIONS = {".md", ".txt", ".markdown", ".rst", ".org", ".text"}


def _infer_source_type(chunk: MemoryChunk) -> str:
    """Classify the chunk's source by type. Dimension, not project name.

    Types: conversation, documentation, spec, note, unknown.
    ET doesn't hardcode 'project' names — Opus infers boundaries from source paths.
    """
    source = chunk.source or ""
    if source.endswith(".jsonl"):
        return "conversation"
    if source.endswith(".md") or source.endswith(".markdown"):
        # README, CHANGELOG, docs vs specs/protocols
        lower = source.lower()
        if any(k in lower for k in ["spec", "protocol", "ontology", "rfc"]):
            return "spec"
        if any(k in lower for k in ["readme", "guide", "docs/", "tutorial"]):
            return "documentation"
        return "note"
    if source.endswith(".txt") or source.endswith(".rst"):
        return "note"
    return "unknown"


def _is_thinking_content(chunk: MemoryChunk) -> bool:
    """Filter: keep conversation transcripts, markdown, and text. Skip source code.

    Rules in order:
      1. Trust provider metadata: chunks tagged by a conversation-producing
         provider (claude-code, copilot-chat, chatgpt-export, cursor*, generic-openai-chat)
         are conversations even if stored in .json/.vscdb/etc.
      2. Claude Code JSONL source paths always pass.
      3. File extension: code extensions reject, thinking extensions pass.
      4. Unknown source: let it through (default-permissive on non-code).
    """
    # 1. Conversation providers take precedence over file extensions
    provider_tag = (chunk.metadata or {}).get("provider", "")
    if provider_tag in _CONVERSATION_PROVIDERS:
        return True

    source = chunk.source or ""

    # 2. Claude Code session transcripts always pass (they ARE thinking)
    if source.endswith(".jsonl"):
        return True

    # 3a. Code extensions reject
    for ext in _CODE_EXTENSIONS:
        if source.endswith(ext):
            return False

    # 3b. Thinking extensions pass
    for ext in _THINKING_EXTENSIONS:
        if source.endswith(ext):
            return True

    # 4. No source or unknown extension: let it through
    return True


# Providers whose chunks are ALWAYS conversation-shaped regardless of path extension.
# A chunk with metadata["provider"] in this set passes the thinking filter even if
# the source path has a "code-looking" suffix (Copilot Chat sessions are .json files,
# Cursor's state.vscdb is a SQLite file, etc.).
_CONVERSATION_PROVIDERS = frozenset({
    "claude-code",
    "chatgpt-export",
    "copilot-chat",
    "cursor-export",
    "cursor-sqlite",
    "generic-openai-chat",
})
