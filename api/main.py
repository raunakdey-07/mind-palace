# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Mind Palace FastAPI application entry point."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.exc import SQLAlchemyError

from api.routers import context, corpora, ingest, memory, query, search


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Keep process startup independent of PostgreSQL connectivity."""
    yield


app = FastAPI(
    title="Mind Palace API",
    description="RAG and agent APIs for the Mind Palace AI-Research OS",
    version="0.6.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    memory_ask = (
        request.url.path in {"/api/query/ask", "/api/query"}
        and isinstance(exc.body, dict)
        and exc.body.get("mode", "rag") != "rag"
    )
    if request.url.path.startswith("/api/memory/") or memory_ask:
        # Do not echo untrusted inputs or non-JSON-serializable validator contexts.
        message = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "invalid_request", "message": message}},
        )
    return await request_validation_exception_handler(request, exc)


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


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    from sqlalchemy import text

    from api.services.db import session_scope

    timeout_seconds = float(os.getenv("MIND_PALACE_READINESS_TIMEOUT_SECONDS", "2"))
    try:
        async with asyncio.timeout(timeout_seconds):
            async with session_scope() as db:
                result = await db.execute(
                    text(
                        "SELECT to_regclass('corpora') IS NOT NULL, "
                        "to_regclass('memory_documents') IS NOT NULL, "
                        "to_regclass('memory_versions') IS NOT NULL"
                    )
                )
                if not all(result.one()):
                    return JSONResponse(
                        status_code=503,
                        content={"status": "not_ready", "checks": {"database": "unavailable"}},
                    )
    except (SQLAlchemyError, OSError, TimeoutError, ValueError):
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": {"database": "unavailable"}},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "checks": {"database": "ok"}},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Register routers
app.include_router(corpora.router, prefix="/api/corpora", tags=["corpora"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(query.router, prefix="/api/query", tags=["query"])
app.include_router(context.router, prefix="/api/context", tags=["context"])
app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
