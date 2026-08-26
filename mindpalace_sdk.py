# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Mind Palace Python SDK: corpus memory for AI applications.

Minimal, typed client over the Mind Palace core. The intended experience:

    from mindpalace import MindPalace

    mp = MindPalace("my-corpus")
    mp.sync("./docs")
    pack = mp.context("How does authentication work?", budget_tokens=4000)
    print(pack.context)       # model-ready text
    print(pack.sources)       # attribution

The SDK talks to the database through the same service layer as the API;
no HTTP server is required.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.services.context_packer import ContextPack, pack_context
from api.services.corpora import get_or_create_corpus
from api.services.db import session_scope
from api.services.embedder import Embedder
from api.services.ingestion import IngestionService
from api.services.retrieval import RetrievalService


@dataclass
class SyncSummary:
    """Machine-readable result of a corpus synchronization."""

    success: bool
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    chunk_count: int = 0
    duration_ms: int = 0


class CorpusNotFoundError(Exception):
    """Raised when an operation references a corpus that does not exist."""


class MindPalace:
    """Corpus memory client for one named corpus."""

    def __init__(self, name: str, *, create_if_missing: bool = True):
        self.name = name
        self._embedder = Embedder()
        self._ingestion = IngestionService()
        self._create_if_missing = create_if_missing
        if create_if_missing:
            self._run(self._ensure_corpus())

    @staticmethod
    def _run(coro):
        """Run a coroutine on a fresh event loop with a disposed pool after.

        asyncpg connections bind to their creating loop, so each SDK call
        gets its own loop and the shared engine is disposed afterwards.
        The engine uses the default queue pool; disposal warnings from
        cross-loop close are suppressed as they are benign here.
        """
        import asyncio
        import logging

        from api.services.db import async_engine

        logging.getLogger("sqlalchemy.pool.impl.AsyncAdaptedQueuePool").setLevel(logging.CRITICAL)
        try:
            return asyncio.run(coro)
        finally:
            try:
                asyncio.run(async_engine.dispose())
            except Exception:
                pass

    async def _ensure_corpus(self):
        async with session_scope() as db:
            await get_or_create_corpus(db, self.name)

    def sync(self, path: str, *, delete_removed: bool = True) -> SyncSummary:
        """Synchronize the corpus with a source directory.

        Added files are ingested; changed files are reprocessed; files removed
        from the source are dropped from the index (when ``delete_removed``).
        """
        corpus_id = self._corpus_id()
        result = self._run(
            self._ingestion.sync_repo(path, corpus_id, delete_removed=delete_removed)
        )
        return SyncSummary(
            success=result["success"],
            added=result["added"],
            changed=result["changed"],
            unchanged=result["unchanged"],
            deleted=result["deleted"],
            failed=result["failed"],
            chunk_count=result["chunk_count"],
            duration_ms=result["duration_ms"],
        )

    def context(
        self,
        query: str,
        *,
        budget_tokens: int = 4096,
        k: int = 8,
        strategy: str = "hybrid_rrf",
    ) -> ContextPack:
        """Retrieve evidence for ``query`` and pack model-ready context."""
        corpus_id = self._corpus_id(must_exist=True)
        vector = self._embedder.embed_single(query)

        async def _search():
            async with session_scope() as db:
                svc = RetrievalService(db)
                return await svc.search(
                    vector,
                    k=k,
                    hybrid=(strategy != "vector"),
                    rrf=(strategy == "hybrid_rrf"),
                    query_text=query if strategy != "vector" else None,
                    corpus_id=corpus_id,
                )

        results = self._run(_search())
        return pack_context(query, results, budget_tokens=budget_tokens, strategy=strategy)

    def search(self, query: str, *, k: int = 5, strategy: str = "hybrid_rrf"):
        """Raw retrieval results (what is relevant), without packing."""
        corpus_id = self._corpus_id(must_exist=True)
        vector = self._embedder.embed_single(query)

        async def _search():
            async with session_scope() as db:
                svc = RetrievalService(db)
                return await svc.search(
                    vector,
                    k=k,
                    hybrid=(strategy != "vector"),
                    rrf=(strategy == "hybrid_rrf"),
                    query_text=query if strategy != "vector" else None,
                    corpus_id=corpus_id,
                )

        return self._run(_search())

    # -- internals ---------------------------------------------------------

    def _corpus_id(self, must_exist: bool = False) -> str:
        from api.services.corpora import get_corpus_by_name

        async def _get():
            async with session_scope() as db:
                return await get_corpus_by_name(db, self.name)

        corpus = self._run(_get())
        if not corpus:
            raise CorpusNotFoundError(f"corpus '{self.name}' not found")
        return corpus["id"]
