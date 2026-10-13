# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Timeline service for chronological document views."""

from __future__ import annotations

from typing import List, Optional

from api.services.db import session_scope
from api.services.retrieval import RetrievalService


class TimelineService:
    """Service for timeline operations.

    ``corpus_id`` is required rather than optional. `RetrievalService` treats a
    missing corpus as "every corpus", which is right for a benchmark sandbox and
    wrong for a request: an unscoped timeline would list documents from every
    tenant in the database, with their titles and dates.
    """

    async def get_timeline(
        self,
        corpus_id: str,
        document_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Get chronological document view."""
        async with session_scope() as db:
            retrieval = RetrievalService(db)
            return await retrieval.get_timeline(
                document_type, start_date, end_date, limit, corpus_id=corpus_id
            )
