"""Related documents service."""

from __future__ import annotations

from api.services.db import session_scope
from api.services.retrieval import RetrievalService


class RelatedService:
    """Service for finding related documents.

    ``corpus_id`` is required rather than optional. `RetrievalService` treats a
    missing corpus as "every corpus", which is the right default for a benchmark
    sandbox and the wrong one for a request: a related-documents lookup scoped to
    nobody would answer with documents from every tenant in the database.
    """

    async def get_related_documents(self, doc_id: str, corpus_id: str, k: int = 5) -> list[dict]:
        """Find related documents via shared tags and document type."""
        async with session_scope() as db:
            retrieval = RetrievalService(db)
            return await retrieval.get_related_documents(doc_id, k, corpus_id=corpus_id)
