"""Search endpoint: semantic vector similarity search with metadata filtering."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from api.models.schemas import SearchResponse, SearchResult
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.retrieval import RetrievalService

router = APIRouter()
embedder = Embedder()


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query"),
    k: int = Query(5, ge=1, le=50, description="Number of results"),
    document_type: Optional[str] = Query(
        None, description="Filter by document type (kaggle, project, note, paper)"
    ),
    tags: Optional[str] = Query(None, description="Comma-separated tags to filter by"),
    hybrid: bool = Query(False, description="Enable hybrid search (vector + keyword)"),
) -> SearchResponse:
    """Semantic search over ingested Markdown content with optional metadata filters."""
    query_vector = embedder.embed_single(q)

    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    async for db in [get_async_db()]:
        async with db:
            retrieval = RetrievalService(db)
            results = await retrieval.search(
                query_vector,
                k=k,
                document_type=document_type,
                tags=tag_list,
                hybrid=hybrid,
                query_text=q if hybrid else None,
            )

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
