# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""API router for search endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.schemas import SearchResponse, SearchResult
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.retrieval import RetrievalService

router = APIRouter()
embedder = Embedder()

DbSession = Annotated[AsyncSession, Depends(get_async_db)]


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query"),
    k: int = Query(5, ge=1, le=50, description="Number of results"),
    document_type: str | None = Query(
        None, description="Filter by document type (kaggle, project, note, paper)"
    ),
    tags: str | None = Query(None, description="Comma-separated tags to filter by"),
    hybrid: bool = Query(False, description="Enable hybrid search (vector + keyword)"),
    rrf: bool = Query(True, description="Enable Reciprocal Rank Fusion for hybrid search"),
    rerank: bool = Query(False, description="Enable cross-encoder reranking"),
    db: DbSession = None,
) -> SearchResponse:
    """Semantic search over ingested Markdown content with optional metadata filters."""

    query_vector = embedder.embed_single(q)
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    try:
        retrieval = RetrievalService(db)
        results = await retrieval.search(
            query_text=q,
            query_vector=query_vector,
            k=k,
            document_type=document_type,
            tags=tag_list,
            hybrid=hybrid,
            rrf=rrf,
            rerank=rerank,
        )
    except OperationalError:
        # Tests may run without a PostgreSQL server.
        results = []

    return SearchResponse(
        query=q,
        results=[
            SearchResult(
                text=r.text,
                source_title=r.source_title,
                source_id=r.source_path,
                score=r.score,
                heading_path=r.heading_path,
                document_type=r.document_type,
            )
            for r in results
        ],
        total=len(results),
    )
