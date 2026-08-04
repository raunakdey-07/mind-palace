"""Search endpoint: semantic vector similarity search."""

from __future__ import annotations

from typing import Optional

from api.models.schemas import SearchResponse, SearchResult
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.repository import search_chunks
from fastapi import APIRouter, Query

router = APIRouter()
embedder = Embedder()


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query"),
    k: int = Query(5, ge=1, le=50, description="Number of results"),
) -> SearchResponse:
    """Semantic search over ingested Markdown content."""
    query_vector = embedder.embed_single(q)

    async for db in [get_async_db()]:
        async with db:
            results = await search_chunks(db, query_vector, k=k)

    return SearchResponse(
        query=q,
        results=[
            SearchResult(
                text=r["text"],
                source_title=r["source_title"],
                source_id=r.get("source_path"),
                score=r["score"],
            )
            for r in results
        ],
        total=len(results),
    )
