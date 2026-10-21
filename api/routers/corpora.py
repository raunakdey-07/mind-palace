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
    CorpusDeletionResponse,
    CorpusStatsResponse,
    SyncRequest,
    SyncResponse,
)
from api.services import corpora
from api.services.db import get_async_db
from api.services.ingest_paths import IngestPathRefused, require_permitted
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


@router.delete("/{name}", response_model=CorpusDeletionResponse)
async def delete_corpus(name: str, db: DbSession = None) -> CorpusDeletionResponse:
    """Erase a corpus and every row of memory the server holds for it.

    Returns a report rather than an empty 204, because the caller needs to know
    whether anything was actually removed and what is *not* covered: exported
    receipts and database backups are outside this operation, and a `204` would
    imply otherwise.

    Requires `manage_corpus`. A corpus the caller cannot see is `404`, the same
    as one that does not exist, so this cannot be used to discover tenants.
    """
    try:
        report = await corpora.delete_corpus(db, name)
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e
    if not report["deleted"]:
        # `get_corpus_by_name` already refused a corpus outside the caller's
        # scope, so reaching here means it was visible and then absent.
        raise HTTPException(status_code=404, detail=f"corpus '{name}' not found")
    return CorpusDeletionResponse(**report)


@router.post("/{name}/sync", response_model=SyncResponse)
async def sync_corpus(
    name: str,
    request: SyncRequest,
    delete_removed: bool = Query(
        True, description="Remove indexed docs whose source files were deleted"
    ),
    embed_chunks: bool = Query(
        False,
        description=(
            "Also embed every chunk, for the chunk-level search. Off by default: a "
            "document that produced claims is answered from the claim archive."
        ),
    ),
    db: DbSession = None,
) -> SyncResponse:
    """Synchronize a corpus with a source directory.

    Reconciles added/changed/unchanged/deleted documents. A failed document
    never leaves a false success state.
    """
    corpus = await _require_corpus(db, name, write=True)
    # Refused before the directory is walked. `sync` names a path on the server's
    # filesystem, which is an operator capability; on a shared service that has
    # to be bounded by configuration rather than trusted to the caller.
    try:
        source = require_permitted(request.path)
    except IngestPathRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    svc = IngestionService()
    result = await svc.sync_repo(
        source, corpus["id"], delete_removed=delete_removed, embed_chunks=embed_chunks
    )
    return SyncResponse(**result)


async def _require_corpus(db: AsyncSession, name: str, *, write: bool = False) -> dict:
    """Resolve a corpus this request may use.

    `write=True` for the operations that mutate tenant-owned rows. `sync` was
    the one that did not: it writes documents, chunks, the manifest and the whole
    memory archive, so a `read_only` credential could perform a complete write
    into its own corpus. The role check is the whole point of the flag.
    """
    try:
        corpus = await corpora.get_corpus_by_name(db, name, write=write)
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e
    if not corpus:
        raise HTTPException(status_code=404, detail=f"corpus '{name}' not found")
    return corpus
