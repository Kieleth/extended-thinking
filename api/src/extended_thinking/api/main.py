"""FastAPI application for extended-thinking."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from extended_thinking.api.routes import graph_v2, pipeline_v2
from extended_thinking.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """No-op lifespan. Kuzu / Chroma stores are lazily initialized per request."""
    yield


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
