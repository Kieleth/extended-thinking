"""FastAPI application for extended-thinking."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from extended_thinking.api.routes import graph_v2, pipeline_v2
from extended_thinking.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan.

    Stores are lazily initialized per resolved data path (process-wide
    singleton — see graph_v2._get_graph_store). On shutdown we close
    every cached GraphStore so the Kuzu file handles release
    deterministically before the process exits (R11).
    """
    try:
        yield
    finally:
        graph_v2.close_graph_stores()


app = FastAPI(
    title="extended-thinking",
    description="A pluggable thinking layer for AI-augmented work.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(graph_v2.router)
app.include_router(pipeline_v2.router)


@app.get("/api/health")
def health() -> dict:
    """Health check."""
    return {"status": "ok"}
