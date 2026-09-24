# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Public memory HTTP adapter. Authentication is not currently provided."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from api.models.memory import FeedResponse, MemoryRequest, MemoryResponse

logger = logging.getLogger("mindpalace.ops")

router = APIRouter()


async def execute_memory(operation: str, request: MemoryRequest) -> MemoryResponse:
    from api.services.memory_public import MemoryError, execute

    try:
        return await execute(operation, request)
    except MemoryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.post("/query", response_model=MemoryResponse)
async def query(request: MemoryRequest) -> MemoryResponse:
    """Query corpus memory with intent-aware, bounded evidence."""
    return await execute_memory("query", request)


@router.post("/current", response_model=MemoryResponse)
async def current(request: MemoryRequest) -> MemoryResponse:
    """Read current memories within a corpus."""
    return await execute_memory("current", request)


@router.post("/history", response_model=MemoryResponse)
async def history(request: MemoryRequest) -> MemoryResponse:
    """Read memory history with provenance."""
    return await execute_memory("history", request)


@router.get("/feed", response_model=FeedResponse)
async def feed(
    corpus: str = Query(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    ),
    page_size: int = Query(50, ge=1, le=500),
    cursor: str | None = Query(None, min_length=1, max_length=4096),
) -> FeedResponse:
    """Consume the durable corpus-scoped operational change feed."""
    from api.services.db import session_scope
    from api.services.memory_feed import feed as get_feed
    from api.services.memory_public import MemoryError, translate_database_error

    started = time.perf_counter()
    continuation = cursor is not None

    def emit(result: FeedResponse | None = None, error: str | None = None) -> None:
        logger.info(
            "op=memory_feed corpus=%s page_size=%s returned=%s has_more=%s "
            "cursor_continuation=%s latency_ms=%.2f error=%s",
            corpus,
            page_size,
            len(result.items) if result is not None else 0,
            result.has_more if result is not None else False,
            continuation,
            (time.perf_counter() - started) * 1000,
            error or "-",
        )

    try:
        async with session_scope() as db:
            result = await get_feed(db, corpus, cursor, page_size)
    except MemoryError as exc:
        emit(error=exc.code)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        translated = translate_database_error(exc)
        emit(error=translated.code)
        raise HTTPException(
            status_code=translated.status_code,
            detail={"code": translated.code, "message": translated.message},
        ) from exc
    emit(result=result)
    return result


@router.post("/changes", response_model=MemoryResponse)
async def changes(request: MemoryRequest) -> MemoryResponse:
    """Read memory changes."""
    return await execute_memory("changes", request)


@router.post("/evidence", response_model=MemoryResponse)
async def evidence(request: MemoryRequest) -> MemoryResponse:
    """Read evidence supporting memories."""
    return await execute_memory("evidence", request)


@router.post("/as-of", response_model=MemoryResponse)
async def as_of(request: MemoryRequest) -> MemoryResponse:
    """Read memory at a historical observation time."""
    return await execute_memory("as-of", request)


@router.post("/snapshot", response_model=MemoryResponse)
async def snapshot(request: MemoryRequest) -> MemoryResponse:
    """Create an immutable snapshot, not an arbitrary memory write."""
    return await execute_memory("snapshot", request)


@router.post("/replay", response_model=MemoryResponse)
async def replay(request: MemoryRequest) -> MemoryResponse:
    """Replay an immutable snapshot."""
    return await execute_memory("replay", request)


@router.post("/pack", response_model=MemoryResponse)
async def pack(request: MemoryRequest) -> MemoryResponse:
    """Build canonical evidence context bounded in Unicode characters."""
    return await execute_memory("pack", request)
