# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Mind Palace Python SDK: memory that remembers what changed, and why.

The intended experience is five operations:

    from mindpalace_sdk import MindPalace

    client = MindPalace()                      # no configuration required

    client.remember("Production uses PostgreSQL.")
    result = client.recall("What database does production use?")
    print(result.current_memories[0].claim)    # the answer

    result = client.explain("What database does production use?")
    print(result.receipt["memory_pack_digest"]) # why it was returned, provably

    print(client.history("datastore").changes) # what it said before

A statement becomes one claim with exact-substring evidence, through the same
archive that synced documents use. `recall` never invents an answer: a question
the archive does not cover abstains with a `constraint` rather than guessing. No
embedding model is needed to write, recall or verify.

The corpus defaults to ``default``. Pass one to scope memory explicitly.

Everything above runs against a local PostgreSQL. Supply `base_url` to go over
HTTP instead, which is how an agent reaches memory held by someone else:

    client = MindPalace(base_url="http://127.0.0.1:8000")

The lower-level `client.memory.*` operations -- `current`, `changes`, `evidence`,
`as_of`, `snapshot`, `replay`, `pack`, `feed`, and `query` with its intent and
selector options -- remain available and unchanged.

The synchronous local SDK cannot be called from an active event loop; async
applications should await `api.services.memory_public.execute`. Remote failures
raise MemoryClientError with code, message and status_code; local service failures
propagate the central MemoryError unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from api.models.memory import FeedResponse, MemoryResponse
    from api.services.context_packer import ContextPack

#: The corpus a first memory lands in, matching the CLI. Passing a name scopes
#: memory explicitly and is never required.
DEFAULT_CORPUS = "default"


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
    errors: list[str] = field(default_factory=list)


class CorpusNotFoundError(Exception):
    """Raised when an operation references a corpus that does not exist."""


@dataclass(frozen=True)
class MemoryResult:
    """What one ``remember`` wrote.

    ``unchanged=True`` is the idempotency result, not a failure: the same
    statement submitted twice resolves to the same identity and the same
    authoritative memory, so there is nothing to write and nothing duplicated.
    """

    corpus: str
    version_id: str | None
    event: str
    path: str
    statement: str | None = None
    key: str | None = None

    @property
    def unchanged(self) -> bool:
        """True when this write was already recorded, so nothing was added."""
        return self.event == "UNCHANGED"

    @property
    def created(self) -> bool:
        """True when this write recorded a new version."""
        return not self.unchanged

    @classmethod
    def from_write(cls, written: dict, corpus: str, statement) -> "MemoryResult":
        """Build the result from the ingestion service's write result.

        The identity comes from the write itself. Recomputing it here would let the
        SDK name a key or a path the archive does not hold -- which it did, for a
        whitespace-padded statement -- and a caller retrying with that key would
        write a second memory instead of recognising the first.
        """
        return cls(
            corpus=corpus,
            version_id=written.get("version_id"),
            event=written.get("event", "NEW"),
            path=written.get("path", ""),
            statement=statement,
            key=written.get("key"),
        )


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


def _as_client_error(exc: Exception) -> Exception:
    """Present a service failure as the SDK's own exception type.

    A caller should not have to care whether a request was answered in this process,
    through a local runtime, or over HTTP -- all three are the same product, and one
    condition must not arrive as three exception classes. The code, message and
    status are carried across unchanged; only the class changes, so `except
    MemoryClientError` is the one thing a caller needs to write.

    Anything that is not a service error is returned untouched. A genuine defect must
    not be dressed up as an API failure, which would send a user looking at their
    request instead of at the bug.
    """
    from api.services.memory_public import MemoryError as ServiceMemoryError

    if isinstance(exc, MemoryClientError):
        return exc
    if isinstance(exc, ServiceMemoryError):
        return MemoryClientError(exc.code, exc.message, exc.status_code)
    return exc


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
        # `corpus=None` stays invalid: a caller who passes no corpus explicitly
        # should be told so, not silently routed somewhere. The convenience default
        # lives on MindPalace's short methods, which name it for you.
        return self._client.name if corpus is None else corpus

    def _through_runtime(self, operation: str, request) -> "MemoryResponse | None":
        """Ask a running local runtime, if the caller opted in and one is up.

        Returns None when there is no runtime, so the caller falls back to its own
        process. Deliberately never raises: the runtime is an optimisation, and an
        optimisation that can fail a query is not one.

        Only useful for a caller that would otherwise pay a fresh model import per
        call. A long-lived application has already paid it, which is why the SDK
        defaults this off -- see `MindPalace(runtime=...)`.

        Lexical mode never uses the runtime. A runtime exists to hold the model, and
        a developer who asked for model-free reads does not want that process in the
        path. Bypassing it needs no protocol change and no second implementation: the
        same `execute` runs here, in this process, and answers identically.
        """
        if not getattr(self._client, "use_runtime", False):
            return None
        from api.services.retrieval_mode import lexical_requested

        if lexical_requested():
            return None
        from api import runtime as runtime_module

        try:
            reply = runtime_module.call(
                {"op": operation, "request": request.model_dump(mode="json")},
                timeout=self._client.timeout,
            )
        except Exception:  # noqa: BLE001 - unreachable runtime means "do it myself"
            return None
        if not reply.get("ok"):
            # A runtime that answered is authoritative about failure: re-raise its
            # error rather than silently producing a different answer in-process.
            error = reply.get("error") or {}
            raise MemoryClientError(
                error.get("code", "error"),
                error.get("message", "memory runtime failed"),
                int(error.get("status", 1) or 1),
            )
        from api.models.memory import MemoryResponse

        return MemoryResponse.model_validate(reply["result"])

    def _execute(self, operation: str, corpus: str | None, query: str, **fields) -> MemoryResponse:
        from api.models.memory import MemoryRequest, MemoryResponse

        corpus_name = self._corpus_name(corpus)
        request = MemoryRequest.model_validate({"corpus": corpus_name, "query": query, **fields})
        if self._client.base_url is None:
            _require_sync()
            remote = self._through_runtime(operation, request)
            if remote is not None:
                return remote
            from api.services.memory_public import execute

            try:
                return asyncio.run(execute(operation, request))
            except MemoryClientError:
                raise
            except Exception as exc:  # noqa: BLE001 - re-raised, or translated to ours
                raise _as_client_error(exc) from exc

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

    def remember(
        self,
        statement: str | None = None,
        *,
        corpus: str | None = None,
        key: str | None = None,
        file: str | None = None,
    ) -> MemoryResult:
        """Record a fact as authoritative memory, and return what was written.

        A statement becomes one claim with exact-substring evidence, through the
        same archive a synced document uses::

            client.memory.remember("Production uses PostgreSQL.", key="datastore")

        Remembering the same statement twice returns ``unchanged=True`` and the
        same ``version_id``: submitting identical memory is not duplication.
        Remembering the same ``key`` with new text supersedes the previous value.

        Works locally against PostgreSQL, or over HTTP when the client has a
        ``base_url``. Requires no embedding model either way. The same operation is
        available as :meth:`MindPalace.remember`.
        """
        name = self._corpus_name(corpus)
        if self._client.base_url is None:
            _require_sync()
            if file is None:
                # A file is a local read, so it stays in this process. A statement is
                # not, and going through a runtime keeps the corpus's connection pool
                # warm for the recall that usually follows.
                remote = self._remember_via_runtime(name, statement, key)
                if remote is not None:
                    return remote
            from api.services.remember import remember as write

            async def run() -> MemoryResult:
                written = await write(statement, corpus=name, key=key, file=file)
                return MemoryResult.from_write(written, name, statement)

            try:
                return MindPalace._run(run())
            except MemoryClientError:
                raise
            except Exception as exc:  # noqa: BLE001 - one exception type, whichever path ran
                raise _as_client_error(exc) from exc

        import httpx

        if file is not None:
            # The HTTP contract is statement-only, on purpose: an unauthenticated
            # endpoint must not read a path on the server. Say so here rather than
            # sending a request that can only 422.
            raise MemoryClientError(
                "invalid_request",
                "file= reads a path on this machine, which a remote service cannot do. "
                "Read the file and send its content as the statement.",
                422,
            )
        payload = {
            k: v
            for k, v in {"corpus": name, "statement": statement, "key": key}.items()
            if v is not None
        }
        try:
            response = httpx.post(
                f"{self._client.base_url}/api/memory/remember",
                json=payload,
                headers=self._client.headers,
                timeout=self._client.timeout,
            )
        except httpx.TimeoutException as exc:
            raise MemoryClientError("timeout", "Memory write timed out", 504) from exc
        except httpx.RequestError as exc:
            raise MemoryClientError(
                "transport_error", "Memory service is unavailable", 503
            ) from exc
        if not response.is_success:
            raise _remote_error(response, f"Memory write failed (HTTP {response.status_code})")

        from api.models.remember import RememberResult as WireResult

        try:
            written = WireResult.model_validate(response.json())
        except ValueError as exc:
            raise MemoryClientError(
                "invalid_response", "Memory service returned an invalid response", 502
            ) from exc
        return MemoryResult(
            corpus=written.corpus,
            version_id=written.version_id,
            event=written.event,
            path=written.path,
            statement=statement,
            key=written.key,
        )

    def _remember_via_runtime(
        self, name: str, statement: str | None, key: str | None
    ) -> "MemoryResult | None":
        """One write through a running runtime, or None if there is not one."""
        if not getattr(self._client, "use_runtime", False):
            return None
        from api.services.retrieval_mode import lexical_requested

        if lexical_requested():
            return None
        from api import runtime as runtime_module

        try:
            reply = runtime_module.call(
                {"op": "remember", "corpus": name, "statement": statement, "key": key},
                timeout=self._client.timeout,
            )
        except Exception:  # noqa: BLE001 - unreachable runtime means "do it myself"
            return None
        if not reply.get("ok"):
            error = reply.get("error") or {}
            raise MemoryClientError(
                error.get("code", "error"),
                error.get("message", "memory write failed"),
                int(error.get("status", 1) or 1),
            )
        result = reply["result"]
        return MemoryResult(
            corpus=result.get("corpus", name),
            version_id=result.get("version_id"),
            event=result.get("event", "NEW"),
            path=result.get("path", ""),
            statement=statement,
            key=result.get("key"),
        )

    def recall(
        self,
        question: str,
        corpus: str | None = None,
        *,
        as_of: datetime | str | None = None,
    ) -> MemoryResponse:
        """Answer a question from memory, with its source and the time it was true.

        The shortest path to a useful result. It never invents an answer, so a
        question whose vocabulary the archive does not contain abstains with a
        ``constraint`` rather than guessing::

            result = client.recall("What datastore does production use?")
            for memory in result.current_memories:
                print(memory.claim, memory.path, memory.status)

        The corpus defaults to ``default``. Pass one to scope memory explicitly.
        """
        return self._execute("query", corpus, question, as_of=as_of)

    def explain(
        self, question: str, corpus: str | None = None, *, as_of: datetime | str | None = None
    ) -> MemoryResponse:
        """Answer a question and attach why it was returned.

        The same answer as :meth:`recall`, with the Memory Receipt attached, so
        ``result.receipt`` names the claim identity, its source version and path,
        its evidence with offsets, its validity window and its supersession
        lineage.

        This explains recorded provenance and temporal state. It is not a reasoning
        trace, and it does not establish that the original source was factually
        correct.
        """
        return self._execute("query", corpus, question, as_of=as_of, include_receipt=True)

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
        include_receipt: bool = False,
    ) -> MemoryResponse:
        """Query memory with intent-aware evidence bounded in Unicode characters.

        This is the full operation behind :meth:`recall` and :meth:`explain`: it
        adds intent selection, an explicit budget, and selectors such as
        ``claim_id`` and ``snapshot_id``. Reach for it when you need those;
        otherwise prefer the two shorter methods.

        ``include_receipt=True`` attaches the canonical Memory Receipt to
        ``result.receipt``: what was returned, from which version, with what
        evidence, and how to verify it later. The receipt is built by the same
        service REST and MCP use, so it cannot differ between surfaces.
        """
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
            include_receipt=include_receipt,
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
        runtime: bool = False,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/") if base_url is not None else None
        self.headers = dict(headers or {})
        self.timeout = timeout
        # Off by default, on purpose. A long-lived application pays the model import
        # once and then has it resident, so a runtime would only add a socket hop and
        # a second copy of the model. It exists for the caller who pays per call --
        # a script, or the CLI -- so it is opt-in here rather than assumed.
        self.use_runtime = bool(runtime)
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
            errors=result.get("errors", []),
        )

    def remember(
        self,
        statement: str | None = None,
        *,
        corpus: str | None = None,
        key: str | None = None,
        file: str | None = None,
    ) -> MemoryResult:
        """Record a fact as authoritative memory, and return what was written.

        A statement becomes one claim with exact-substring evidence, through the
        same ingestion and archive path a synced document uses::

            client.remember("Production uses PostgreSQL.", key="datastore")

        Remembering the same statement twice returns ``unchanged=True`` and the
        same ``version_id``: submitting identical memory is not duplication.
        Remembering the same ``key`` with new text supersedes the previous value,
        which is what :meth:`history` then shows.

        ``key`` is how you tell Mind Palace that two statements are about the same
        fact. Omit it and each distinct statement is its own memory.

        Requires no embedding model. Semantic ranking fills in later; the
        authoritative claim and its evidence are recorded either way.
        """
        return self.memory.remember(
            statement, corpus=corpus or self.name or DEFAULT_CORPUS, key=key, file=file
        )

    def recall(
        self, question: str, *, corpus: str | None = None, as_of: datetime | str | None = None
    ) -> MemoryResponse:
        """Answer a question from memory, with its source and the time it was true.

        The shortest path to a useful result. It never invents an answer, so a
        question the archive does not cover abstains with a ``constraint`` rather
        than guessing.

        ``as_of`` reconstructs the answer from an earlier instant, so "what did we
        decide back then?" is answerable after the decision changes.
        """
        return self.memory.recall(question, corpus or self.name or DEFAULT_CORPUS, as_of=as_of)

    def explain(self, question: str, *, corpus: str | None = None) -> MemoryResponse:
        """Answer a question and attach why it was returned.

        The same answer as :meth:`recall`, with the Memory Receipt attached: the
        claim identity, its source version and path, its evidence with offsets,
        its validity window and its supersession lineage.

        This explains recorded provenance and temporal state. It is not a reasoning
        trace, and it does not establish that the original source was factually
        correct.
        """
        return self.memory.explain(question, corpus or self.name or DEFAULT_CORPUS)

    def history(self, question: str = "", *, corpus: str | None = None) -> MemoryResponse:
        """Read what a memory said before, and what replaced it.

        Reads the real supersession chain from the archive. Nothing here is
        reconstructed: these are the versions and claims the archive actually
        holds. Pass a key or a question to narrow it; pass nothing for the whole
        corpus.
        """
        return self.memory.history(
            corpus=corpus or self.name or DEFAULT_CORPUS, query=question or ""
        )

    def context(
        self,
        query: str,
        *,
        budget_tokens: int = 4096,
        k: int = 8,
        strategy: str = "hybrid_rrf",
        as_of: str | None = None,
        intent: str | None = None,
    ) -> ContextPack:
        """Assemble bounded, evidence-backed context for ``query``.

        The archive decides what is true, what changed, what conflicts and what
        is absent. Retrieval only adds raw source material, and is skipped when
        no embedding model is available, so this still answers without one.
        """
        self._require_legacy()
        from api.services.context_service import ContextError, build_context
        from api.services.db import session_scope

        corpus_name = self.name
        if corpus_name is None:
            raise ValueError("a corpus name is required")
        cutoff = datetime.fromisoformat(as_of) if as_of else None

        async def _build():
            async with session_scope() as db:
                return await build_context(
                    db,
                    corpus_name,
                    query,
                    budget_tokens=budget_tokens,
                    k=k,
                    strategy=strategy,
                    as_of=cutoff,
                    intent=intent,
                )

        try:
            pack, _ = self._run(_build())
        except ContextError as exc:
            raise MemoryClientError(exc.code, exc.message, exc.status_code) from exc
        return pack

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


# ---------------------------------------------------------------------------
# Receipt verification, re-exported
# ---------------------------------------------------------------------------
# A caller who received `result.receipt` should not have to import server or
# database code to check it. These are the same stdlib-only functions the CLI
# uses, re-exported so the SDK surface is one import.

from memory_receipt import (  # noqa: E402
    ReceiptError,
    verify_response_receipt,
    verify_receipt,
    verify_trust,
)

__all__ = [
    "DEFAULT_CORPUS",
    "CorpusNotFoundError",
    "MemoryClient",
    "MemoryClientError",
    "MemoryResult",
    "MindPalace",
    "ReceiptError",
    "SyncSummary",
    "verify_receipt",
    "verify_response_receipt",
    "verify_trust",
]
