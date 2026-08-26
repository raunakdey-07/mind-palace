# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""API router for corpus management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.schemas import (
    CorpusCreate,
    CorpusResponse,
    CorpusStatsResponse,
    SyncRequest,
    SyncResponse,
)
from api.services import corpora
from api.services.db import get_async_db
from api.services.ingestion import IngestionService

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_async_db)]


@router.post("", response_model=CorpusResponse, status_code=201)
async def create_corpus(request: CorpusCreate, db: DbSession = None) -> CorpusResponse:
    """Create a corpus namespace."""
    try:
        corpus = await corpora.create_corpus(db, request.name, request.description)
    except ValueError as e:
        raise HTTPException(status_code=409 if "exists" in str(e) else 422, detail=str(e))
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e
    return CorpusResponse(**corpus, document_count=0)


@router.get("", response_model=list[CorpusResponse])
async def list_corpora(db: DbSession = None) -> list[CorpusResponse]:
    """List all corpora with document counts."""
    try:
        items = await corpora.list_corpora(db)
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e
    return [CorpusResponse(**i) for i in items]


@router.get("/{name}", response_model=CorpusStatsResponse)
async def get_corpus(name: str, db: DbSession = None) -> CorpusStatsResponse:
    """Inspect one corpus: counts and last ingestion time."""
    try:
        stats = await corpora.corpus_stats(db, name)
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e
    if not stats:
        raise HTTPException(status_code=404, detail=f"corpus '{name}' not found")
    return CorpusStatsResponse(**stats)


@router.delete("/{name}", status_code=204)
async def delete_corpus(name: str, db: DbSession = None) -> None:
    """Delete a corpus and all its documents/chunks."""
    try:
        deleted = await corpora.delete_corpus(db, name)
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e
    if not deleted:
        raise HTTPException(status_code=404, detail=f"corpus '{name}' not found")


@router.post("/{name}/sync", response_model=SyncResponse)
async def sync_corpus(
    name: str,
    request: SyncRequest,
    delete_removed: bool = Query(
        True, description="Remove indexed docs whose source files were deleted"
    ),
    db: DbSession = None,
) -> SyncResponse:
    """Synchronize a corpus with a source directory.

    Reconciles added/changed/unchanged/deleted documents. A failed document
    never leaves a false success state.
    """
    corpus = await _require_corpus(db, name)
    svc = IngestionService()
    result = await svc.sync_repo(request.path, corpus["id"], delete_removed=delete_removed)
    return SyncResponse(**result)


async def _require_corpus(db: AsyncSession, name: str) -> dict:
    try:
        corpus = await corpora.get_corpus_by_name(db, name)
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e
    if not corpus:
        raise HTTPException(status_code=404, detail=f"corpus '{name}' not found")
    return corpus
