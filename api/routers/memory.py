# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Public memory HTTP adapter. Authentication is not currently provided."""

from fastapi import APIRouter, HTTPException

from api.models.memory import MemoryRequest, MemoryResponse

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
