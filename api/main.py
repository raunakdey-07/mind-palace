# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Mind Palace FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from api.routers import ingest, query, search


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: verify DB connectivity on startup."""
    from api.services.db import init_db

    await init_db()
    yield


app = FastAPI(
    title="Mind Palace API",
    description="RAG and agent APIs for the Mind Palace AI-Research OS",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS — allow the Next.js frontend in dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


# Health check
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Register routers
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(query.router, prefix="/api/query", tags=["query"])
