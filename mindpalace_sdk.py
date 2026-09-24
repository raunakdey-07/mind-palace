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

Named local clients retain the original sync/search/context behavior. Memory
operations use the central memory service locally, or HTTP when ``base_url`` is
provided (without loading local embedding or database services)::

    mp = MindPalace("my-corpus", base_url="http://127.0.0.1:8000")
    print(mp.memory.current().canonical_json())

An unnamed local client can use ``mp.memory.current(corpus="my-corpus")`` without
initializing legacy retrieval. The synchronous local SDK cannot be called from
an active event loop; async applications should await memory_public.execute.
Remote failures raise MemoryClientError with code, message, and status_code;
local service failures propagate the central MemoryError unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from api.models.memory import FeedResponse, MemoryResponse
    from api.services.context_packer import ContextPack


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


class MemoryClientError(Exception):
    """Remote memory failure; server error codes/messages are preserved.

    Adapter-generated codes are timeout (504), transport_error (503),
    invalid_response (502), and http_error (the HTTP response status).
    """

    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _require_sync() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "The synchronous local SDK cannot run inside an active event loop; "
        "await api.services.memory_public.execute instead."
    )


def _remote_error(response, default_message: str) -> MemoryClientError:
    """Preserve the server's stable error code without exposing its body."""
    code = "http_error"
    message = default_message
    try:
        detail = response.json()
        if isinstance(detail, dict):
            detail = detail.get("detail", detail.get("error", detail))
            if isinstance(detail, dict):
                if isinstance(detail.get("code"), str):
                    code = detail["code"]
                if isinstance(detail.get("message"), str):
                    message = detail["message"]
    except ValueError:
        pass
    return MemoryClientError(code, message, response.status_code)


class MemoryClient:
    """Thin adapter over MemoryRequest and the central memory operations.

    Timestamps accept aware datetimes or ISO-8601 strings; validation belongs to
    MemoryRequest. Corpus defaults to the parent client's name.
    """

    def __init__(self, client: MindPalace):
        self._client = client

    def _corpus_name(self, corpus: str | None) -> str | None:
        return self._client.name if corpus is None else corpus

    def _execute(self, operation: str, corpus: str | None, query: str, **fields) -> MemoryResponse:
        from api.models.memory import MemoryRequest, MemoryResponse

        corpus_name = self._corpus_name(corpus)
        request = MemoryRequest.model_validate({"corpus": corpus_name, "query": query, **fields})
        if self._client.base_url is None:
            _require_sync()
            from api.services.memory_public import execute

            return asyncio.run(execute(operation, request))

        import httpx

        try:
            response = httpx.post(
                f"{self._client.base_url}/api/memory/{operation}",
                json=request.model_dump(mode="json"),
                headers=self._client.headers,
                timeout=self._client.timeout,
            )
        except httpx.TimeoutException as exc:
            raise MemoryClientError("timeout", "Memory request timed out", 504) from exc
        except httpx.RequestError as exc:
            raise MemoryClientError(
                "transport_error", "Memory service is unavailable", 503
            ) from exc

        if not response.is_success:
            raise _remote_error(response, f"Memory request failed (HTTP {response.status_code})")
        try:
            return MemoryResponse.model_validate(response.json())
        except ValueError as exc:
            raise MemoryClientError(
                "invalid_response", "Memory service returned an invalid response", 502
            ) from exc

    def query(
        self,
        query: str,
        corpus: str | None = None,
        *,
        intent: Literal[
            "auto", "current", "historical", "temporal", "change", "conflict", "provenance"
        ] = "auto",
        budget: int = 8000,
        as_of: datetime | str | None = None,
        valid_at: datetime | str | None = None,
        path: str | None = None,
        claim_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> MemoryResponse:
        """Query memory with intent-aware evidence bounded in Unicode characters."""
        return self._execute(
            "query",
            corpus,
            query,
            intent=intent,
            budget=budget,
            as_of=as_of,
            valid_at=valid_at,
            path=path,
            claim_id=claim_id,
            snapshot_id=snapshot_id,
        )

    def current(
        self,
        corpus: str | None = None,
        query: str = "",
        *,
        valid_at: datetime | str | None = None,
        path: str | None = None,
    ) -> MemoryResponse:
        return self._execute("current", corpus, query, valid_at=valid_at, path=path)

    def history(
        self,
        corpus: str | None = None,
        query: str = "",
        *,
        as_of: datetime | str | None = None,
        valid_at: datetime | str | None = None,
        path: str | None = None,
    ) -> MemoryResponse:
        return self._execute("history", corpus, query, as_of=as_of, valid_at=valid_at, path=path)

    def changes(
        self,
        corpus: str | None = None,
        query: str = "",
        *,
        as_of: datetime | str | None = None,
        valid_at: datetime | str | None = None,
        path: str | None = None,
    ) -> MemoryResponse:
        return self._execute("changes", corpus, query, as_of=as_of, valid_at=valid_at, path=path)

    def feed(
        self,
        corpus: str | None = None,
        *,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> FeedResponse:
        """Consume the durable corpus-scoped operational change feed."""
        corpus_name = self._corpus_name(corpus)
        if corpus_name is None:
            raise ValueError("a corpus name is required")
        if self._client.base_url is None:
            _require_sync()
            from api.services.db import session_scope
            from api.services.memory_feed import feed

            async def run() -> FeedResponse:
                async with session_scope() as db:
                    return await feed(db, corpus_name, cursor, page_size)

            return asyncio.run(run())
        import httpx

        params = {"corpus": corpus_name, "page_size": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        try:
            response = httpx.get(
                f"{self._client.base_url}/api/memory/feed",
                params=params,
                headers=self._client.headers,
                timeout=self._client.timeout,
            )
        except httpx.TimeoutException as exc:
            raise MemoryClientError("timeout", "Memory feed request timed out", 504) from exc
        except httpx.RequestError as exc:
            raise MemoryClientError(
                "transport_error", "Memory service is unavailable", 503
            ) from exc
        if not response.is_success:
            raise _remote_error(response, f"Memory feed failed (HTTP {response.status_code})")
        from api.models.memory import FeedResponse

        try:
            return FeedResponse.model_validate(response.json())
        except (TypeError, ValueError) as exc:
            raise MemoryClientError(
                "invalid_response", "Memory feed returned an invalid response", 502
            ) from exc

    def evidence(
        self,
        claim_id: str,
        corpus: str | None = None,
        query: str = "",
        *,
        as_of: datetime | str | None = None,
        valid_at: datetime | str | None = None,
        path: str | None = None,
    ) -> MemoryResponse:
        return self._execute(
            "evidence", corpus, query, claim_id=claim_id, as_of=as_of, valid_at=valid_at, path=path
        )

    def as_of(
        self,
        timestamp: datetime | str,
        corpus: str | None = None,
        query: str = "",
        *,
        valid_at: datetime | str | None = None,
        path: str | None = None,
    ) -> MemoryResponse:
        return self._execute("as-of", corpus, query, as_of=timestamp, valid_at=valid_at, path=path)

    def snapshot(
        self,
        as_of: datetime | str | None = None,
        corpus: str | None = None,
        query: str = "",
        *,
        valid_at: datetime | str | None = None,
        path: str | None = None,
    ) -> MemoryResponse:
        return self._execute("snapshot", corpus, query, as_of=as_of, valid_at=valid_at, path=path)

    def replay_snapshot(
        self,
        snapshot_id: str,
        corpus: str | None = None,
        query: str = "",
        *,
        path: str | None = None,
    ) -> MemoryResponse:
        return self._execute("replay", corpus, query, snapshot_id=snapshot_id, path=path)

    def pack(
        self,
        budget: int = 8000,
        corpus: str | None = None,
        query: str = "",
        *,
        as_of: datetime | str | None = None,
        valid_at: datetime | str | None = None,
        path: str | None = None,
        snapshot_id: str | None = None,
    ) -> MemoryResponse:
        return self._execute(
            "pack",
            corpus,
            query,
            budget=budget,
            as_of=as_of,
            valid_at=valid_at,
            path=path,
            snapshot_id=snapshot_id,
        )


class MindPalace:
    """Local corpus client or remote memory client (when base_url is supplied)."""

    def __init__(
        self,
        name: str | None = None,
        *,
        create_if_missing: bool = True,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/") if base_url is not None else None
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.memory = MemoryClient(self)
        self._create_if_missing = create_if_missing
        if base_url is None and name is not None:
            from api.services.embedder import Embedder
            from api.services.ingestion import IngestionService

            self._embedder = Embedder()
            self._ingestion = IngestionService()
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
        try:
            _require_sync()
        except RuntimeError:
            coro.close()
            raise

        import logging

        from api.services.db import async_engine

        logging.getLogger("sqlalchemy.pool.impl.AsyncAdaptedQueuePool").setLevel(logging.CRITICAL)
        try:
            return asyncio.run(coro)
        finally:
            try:
                asyncio.run(async_engine.dispose())
            except (RuntimeError, OSError):
                pass

    async def _ensure_corpus(self):
        from api.services.corpora import get_or_create_corpus
        from api.services.db import session_scope

        if self.name is None:
            raise ValueError("a corpus name is required")
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
        self._require_legacy()
        from api.services.context_packer import pack_context
        from api.services.db import session_scope
        from api.services.retrieval import RetrievalService

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
        self._require_legacy()
        from api.services.db import session_scope
        from api.services.retrieval import RetrievalService

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

    def _require_legacy(self) -> None:
        if self.base_url is not None or self.name is None:
            raise ValueError("sync/search/context require a named local MindPalace client")

    def _corpus_id(self, must_exist: bool = False) -> str:
        self._require_legacy()
        from api.services.corpora import get_corpus_by_name
        from api.services.db import session_scope

        if self.name is None:
            raise ValueError("a corpus name is required")
        name = self.name

        async def _get():
            async with session_scope() as db:
                return await get_corpus_by_name(db, name)

        corpus = self._run(_get())
        if not corpus:
            raise CorpusNotFoundError(f"corpus '{self.name}' not found")
        return corpus["id"]
