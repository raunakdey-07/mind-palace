# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""API router for ingestion endpoints.

Both routes name their corpus explicitly. They used to write into the implicit
`default` corpus, which made the shared default namespace a write target for
every unauthenticated caller; ingestion now goes through the same
authorization choke point as every other tenant-owned write.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.schemas import IngestFileRequest, IngestRepoRequest, IngestResponse
from api.services.db import get_async_db
from api.services.embedder import SEMANTIC_DEPENDENCY_ERRORS
from api.services.ingest_paths import IngestPathRefused, require_permitted
from api.services.ingestion import IngestionService

router = APIRouter()
ingestion_service = IngestionService()

DbSession = Annotated[AsyncSession, Depends(get_async_db)]


@asynccontextmanager
async def _write_scope(db: AsyncSession | None):
    """A session for the authorization lookup, whether or not one was injected."""
    if db is not None:
        yield db
        return
    from api.services.db import session_scope

    async with session_scope() as opened:
        yield opened


async def _writable_corpus(db: AsyncSession | None, name: str) -> str:
    """Resolve a corpus this request is allowed to write into."""
    from api.services.corpora import get_corpus_by_name

    async with _write_scope(db) as session:
        corpus = await get_corpus_by_name(session, name, write=True)
    if corpus is None:
        raise HTTPException(status_code=404, detail=f"corpus '{name}' not found")
    return corpus["id"]


@router.post("/file", response_model=IngestResponse)
async def ingest_file(request: IngestFileRequest, db: DbSession = None) -> IngestResponse:
    """Ingest a single Markdown file into a named corpus."""
    corpus_id = await _writable_corpus(db, request.corpus)
    try:
        result = await ingestion_service.ingest_file(
            request.content, request.path, corpus_id=corpus_id
        )
    except SEMANTIC_DEPENDENCY_ERRORS as exc:
        raise HTTPException(status_code=503, detail="Semantic model unavailable") from exc
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("message", "Unknown error"))
    return IngestResponse(**result)


@router.post("/repo", response_model=IngestResponse)
async def ingest_repo(request: IngestRepoRequest, db: DbSession = None) -> IngestResponse:
    """Batch ingest all Markdown files from a repository into a named corpus."""
    corpus_id = await _writable_corpus(db, request.corpus)
    # Refused before a single file is read, and before the directory is walked.
    try:
        repo_path = require_permitted(request.repo_path)
    except IngestPathRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        result = await ingestion_service.ingest_repo(repo_path, corpus_id=corpus_id)
    except SEMANTIC_DEPENDENCY_ERRORS as exc:
        raise HTTPException(status_code=503, detail="Semantic model unavailable") from exc
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("message", "Unknown error"))
    return IngestResponse(**result)
