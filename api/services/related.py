"""Related documents service."""

from __future__ import annotations

from typing import List

from api.services.db import get_async_db
from api.services.retrieval import RetrievalService


class RelatedService:
    """Service for finding related documents."""

    async def get_related_documents(self, doc_id: str, k: int = 5) -> List[dict]:
        """Find related documents via shared tags and document type."""
        async for db in [get_async_db()]:
            async with db:
                retrieval = RetrievalService(db)
                return await retrieval.get_related_documents(doc_id, k)
        return []
