"""Timeline service for chronological document views."""

from __future__ import annotations

from api.services.db import get_async_db
from api.services.retrieval import RetrievalService


class TimelineService:
    """Service for timeline operations."""

    async def get_timeline(
        self,
        document_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get chronological document view."""
        async for db in [get_async_db()]:
            async with db:
                retrieval = RetrievalService(db)
                return await retrieval.get_timeline(
                    document_type, start_date, end_date, limit
                )
        return []
