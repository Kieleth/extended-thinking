"""ADR 013 C3 — synchronous typed write path.

Programmatic consumers (autoresearch-ET and kin) POST a typed node or edge,
the call returns only after Kuzu has committed, with an honest
`vectors_pending: true` marker for async ChromaDB indexing.

This is the HTTP surface of `GraphStore.insert`. The contract mirrors
autoresearch-ET's `ETClient.add_node` / `add_edge` Protocol so their
RealET implementation is a thin HTTP client.

Endpoints:
    POST /api/v2/graph/node  -> {id, vectors_pending: true}
    POST /api/v2/graph/edge  -> {id}

Shape (request body):
    {
        "type": "Hypothesis",          # must be a registered class
        "properties": {...},           # pydantic-model fields
        "namespace": "default",        # ADR 013 C2
        "source": "autoresearch-et"   # provenance of the write
    }

Failure modes:
    400 — unknown type, invalid pydantic payload (fails at cls(**properties))
    409 — Kuzu binder rejected the insert (wrong FROM/TO for an edge, etc.)
    422 — payload shape invalid (missing required fields)
    500 — unexpected storage error
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/graph", tags=["graph-v2 (ADR 013)"])


# ── Request / response shapes ─────────────────────────────────────────

class _TypedWriteIn(BaseModel):
    """Common write envelope for nodes and edges."""
    type: str = Field(..., description="Registered class name (e.g. 'Concept', 'RelatesTo')")
    properties: dict[str, Any] = Field(default_factory=dict)
    namespace: str = Field(default="default", description="ADR 013 C2 namespace")
    source: str = Field(default="", description="Consumer identifier (et_source)")


class NodeIn(_TypedWriteIn):
    pass


class NodeOut(BaseModel):
    id: str
    vectors_pending: bool = True  # Kuzu commit is sync; vector index is async


class EdgeIn(_TypedWriteIn):
    """Edge `properties` must include `source_id` and `target_id` (malleus
    Relation slots). Endpoint types are resolved by id lookup — Kuzu's
    binder enforces the ontology's FROM/TO at write time."""


class EdgeOut(BaseModel):
    id: str


# ── Resolve the typed class + dispatch ───────────────────────────────

def _resolve_class(type_name: str, expected: str):
    """Look up a registered class by string name.

    `expected` is "node" or "edge"; raised if the resolved class is the
    wrong kind (prevents POST /node with an edge type and vice versa).
    """
    from extended_thinking._schema.kuzu_types import EDGE_TYPES, KUZU_TABLE, NODE_TYPES

    for cls in KUZU_TABLE:
        if cls.__name__ == type_name:
            if expected == "node" and cls not in NODE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"{type_name!r} is an edge type; use POST /api/v2/graph/edge",
                )
            if expected == "edge" and cls not in EDGE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"{type_name!r} is a node type; use POST /api/v2/graph/node",
                )
            return cls

    available = sorted(c.__name__ for c in KUZU_TABLE
                       if (expected == "node" and c in NODE_TYPES)
                       or (expected == "edge" and c in EDGE_TYPES))
    raise HTTPException(
        status_code=400,
        detail=(
            f"unknown {expected} type {type_name!r}. "
            f"Registered {expected} types: {available}"
        ),
    )


# Process-wide GraphStore cache (R11). Two HTTP requests on the same
# process must NOT each construct their own GraphStore on the same path
# — that would produce two live Kuzu Database handles, divergent page
# allocations, and corruption on write. Serve all requests off a single
# lazily-initialised instance per resolved data path.
#
# Threading note: FastAPI runs route handlers in a threadpool by default;
# the lock guards the lazy init. Kuzu's Connection itself is thread-safe
# for reads + serial writes, so one shared Connection per path is fine.

import threading as _threading
from pathlib import Path as _Path

_STORE_CACHE: dict[str, object] = {}
_STORE_CACHE_LOCK = _threading.Lock()


def _get_graph_store():
    """Process-wide singleton GraphStore on settings.data.root.

    Reuses the same path the memory pipeline uses so both audiences
    share the Kuzu database. Cached per resolved path; concurrent
    requests on the same path see the same instance. R11 contract:
    never construct two GraphStores on the same file in one process.
    """
    from extended_thinking.config import migrate_data_dir, settings
    from extended_thinking.storage.graph_store import GraphStore

    data_dir = migrate_data_dir(settings)
    key = str(_Path(data_dir / "knowledge").resolve())
    with _STORE_CACHE_LOCK:
        cached = _STORE_CACHE.get(key)
        if cached is not None:
            return cached
        store = GraphStore(data_dir / "knowledge")
        _STORE_CACHE[key] = store
        return store


def close_graph_stores() -> None:
    """Close every cached GraphStore. Wire to FastAPI shutdown so the
    Kuzu file handles release deterministically when the process ends.
    """
    with _STORE_CACHE_LOCK:
        for store in list(_STORE_CACHE.values()):
            try:
                store.close()
            except Exception:  # noqa: BLE001
                logger.exception("failed to close cached GraphStore")
        _STORE_CACHE.clear()


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/node", response_model=NodeOut, status_code=201)
def create_node(payload: NodeIn) -> NodeOut:
    """Synchronous typed node write (ADR 013 C3).

    Returns after Kuzu commit. `vectors_pending=true` signals that the
    async ChromaDB indexer hasn't embedded this node yet; `et_find_similar`
    (when it ships in C6) can filter on `vectors_pending=false` to avoid
    pre-embed rows.
    """
    cls = _resolve_class(payload.type, "node")
    try:
        instance = cls(**payload.properties)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e
    except TypeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    kg = _get_graph_store()
    try:
        nid = kg.insert(instance, namespace=payload.namespace, source=payload.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("node insert failed")
        raise HTTPException(status_code=500, detail=f"storage error: {e}") from e

    return NodeOut(id=nid, vectors_pending=True)


@router.post("/edge", response_model=EdgeOut, status_code=201)
def create_edge(payload: EdgeIn) -> EdgeOut:
    """Synchronous typed edge write (ADR 013 C3).

    `properties.source_id` and `properties.target_id` must resolve to
    existing nodes; Kuzu's binder enforces the edge type's FROM/TO
    against its malleus `slot_usage` pinning.
    """
    cls = _resolve_class(payload.type, "edge")
    try:
        instance = cls(**payload.properties)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e
    except TypeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    kg = _get_graph_store()
    try:
        eid = kg.insert(instance, namespace=payload.namespace, source=payload.source)
    except ValueError as e:
        # Unregistered edge, missing endpoint, wrong FROM/TO at the Python layer
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        # Kuzu binder rejection — wrong type pair — surface as 409 conflict
        if "Expected labels" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"edge rejected by ontology constraint: {e}",
            ) from e
        logger.exception("edge insert failed")
        raise HTTPException(status_code=500, detail=f"storage error: {e}") from e
    except Exception as e:
        logger.exception("edge insert failed")
        raise HTTPException(status_code=500, detail=f"storage error: {e}") from e

    return EdgeOut(id=eid)
