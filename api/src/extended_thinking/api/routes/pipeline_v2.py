"""API routes for the provider-based DIKW pipeline.

Clean replacement for the Silk-based insight routes. Uses Pipeline v2
with MemoryProvider + ConceptStore.

Routes:
  POST /api/v2/sync          — pull from provider, extract concepts
  POST /api/v2/insight        — full flow: sync + wisdom
  GET  /api/v2/concepts       — list extracted concepts
  GET  /api/v2/wisdoms        — list generated wisdoms
  GET  /api/v2/stats          — pipeline + provider stats
  POST /api/v2/feedback       — submit feedback on wisdom
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from extended_thinking.processing.concept_store import ConceptStore
from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.providers import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["pipeline-v2"])

# ── Singleton pipeline instance ──────────────────────────────────────

_pipeline: Pipeline | None = None
_state: dict[str, Any] = {
    "running": False,
    "phase": None,
    "result": None,
    "error": None,
}


def _get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        from extended_thinking.config import PROJECT_ROOT
        provider = get_provider()  # auto-detect
        store = ConceptStore(PROJECT_ROOT / "data" / "concepts.db")
        _pipeline = Pipeline(provider, store)
    return _pipeline


# ── Routes ───────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_endpoint() -> dict:
    """Pull new memories from provider, extract concepts."""
    pipeline = _get_pipeline()
    result = await pipeline.sync()
    return result


@router.post("/insight")
async def insight_endpoint() -> dict:
    """Get a fresh insight. Syncs first, generates wisdom if needed."""
    global _state

    if _state["running"]:
        return {"status": "already_running", "phase": _state.get("phase")}

    _state = {"running": True, "phase": "syncing", "result": None, "error": None}

    # Run in background thread (Opus can take 30-60s)
    pipeline = _get_pipeline()

    def _run():
        try:
            loop = asyncio.new_event_loop()
            _state["phase"] = "syncing"
            sync_result = loop.run_until_complete(pipeline.sync())

            _state["phase"] = "thinking"
            insight = loop.run_until_complete(pipeline.get_insight())

            _state["result"] = insight
            _state["phase"] = "done"
            loop.close()
        except Exception as e:
            logger.error("Pipeline failed: %s", e, exc_info=True)
            _state["error"] = str(e)
            _state["phase"] = "error"
        finally:
            _state["running"] = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"status": "started", "phase": "syncing"}


@router.get("/insight/status")
def insight_status() -> dict:
    """Poll insight generation status."""
    pipeline = _get_pipeline()
    stats = pipeline.get_stats()

    return {
        "running": _state["running"],
        "phase": _state.get("phase"),
        "result": _state.get("result"),
        "error": _state.get("error"),
        "concepts": stats["concepts"]["total_concepts"],
        "wisdoms": stats["concepts"]["total_wisdoms"],
    }


@router.get("/concepts")
def list_concepts(order_by: str = "frequency", limit: int = 50) -> list[dict]:
    """List extracted concepts."""
    pipeline = _get_pipeline()
    return pipeline.store.list_concepts(order_by=order_by, limit=limit)


@router.get("/wisdoms")
def list_wisdoms(status: str | None = None) -> list[dict]:
    """List generated wisdoms."""
    pipeline = _get_pipeline()
    return pipeline.store.list_wisdoms(status=status)


@router.post("/feedback")
def submit_feedback(wisdom_id: str, content: str) -> dict:
    """Submit feedback on a wisdom."""
    pipeline = _get_pipeline()
    feedback_id = pipeline.store.add_feedback(wisdom_id, content)
    return {"feedback_id": feedback_id, "wisdom_id": wisdom_id}


@router.get("/stats")
def pipeline_stats() -> dict:
    """Full stats: provider + concepts + wisdoms."""
    pipeline = _get_pipeline()
    return pipeline.get_stats()
