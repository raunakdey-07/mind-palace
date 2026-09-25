"""Corpus-scope contracts for live retrieval routes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.exc import OperationalError

import api.routers.context as context_router
import api.routers.query as query_router
import api.routers.search as search_router
from api.models.schemas import (
    AskRequest,
    ContextPackResponse,
    InterviewRequest,
    RelatedRequest,
    SearchResponse,
    SummarizeRequest,
)
from api.services.corpora import (
    CorpusScopeNotFound,
    CorpusScopeRequired,
    resolve_corpus_scope,
)
from api.services.retrieval import RetrievalService


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _db(result: _Result) -> Mock:
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_explicit_scope_resolves_by_name():
    db = _db(_Result([("c1", "docs", "description")]))

    assert await resolve_corpus_scope(db, "docs") == ("c1", "docs")
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_omitted_scope_selects_only_one_corpus():
    assert await resolve_corpus_scope(_db(_Result([("c1", "docs")])), None) == ("c1", "docs")


@pytest.mark.asyncio
async def test_omitted_scope_rejects_multiple_corpora():
    with pytest.raises(CorpusScopeRequired, match="corpus is required"):
        await resolve_corpus_scope(_db(_Result([("c1", "a"), ("c2", "b")])), None)


@pytest.mark.asyncio
async def test_empty_database_has_no_live_scope():
    assert await resolve_corpus_scope(_db(_Result()), None) == (None, None)


@pytest.mark.asyncio
async def test_unknown_explicit_scope_is_not_silently_unscoped():
    with pytest.raises(CorpusScopeNotFound, match="missing"):
        await resolve_corpus_scope(_db(_Result()), "missing")


@pytest.mark.asyncio
async def test_search_with_no_corpus_does_not_embed_or_retrieve():
    embed = Mock(side_effect=AssertionError("embedding must not run"))
    search = AsyncMock(side_effect=AssertionError("retrieval must not run"))

    with (
        patch.object(search_router, "resolve_corpus_scope", AsyncMock(return_value=(None, None))),
        patch.object(search_router.embedder, "embed_single", embed),
        patch.object(search_router.RetrievalService, "search", search),
    ):
        result = await search_router.search(
            q="question",
            corpus=None,
            k=5,
            document_type=None,
            tags=None,
            hybrid=False,
            rrf=True,
            rerank=False,
            db=object(),
        )

    assert result == SearchResponse(query="question", results=[], total=0)
    embed.assert_not_called()
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_rejects_ambiguous_scope_before_embedding():
    embed = Mock(side_effect=AssertionError("embedding must not run"))

    with (
        patch.object(
            search_router,
            "resolve_corpus_scope",
            AsyncMock(side_effect=CorpusScopeRequired("corpus is required")),
        ),
        patch.object(search_router.embedder, "embed_single", embed),
    ):
        with pytest.raises(HTTPException) as caught:
            await search_router.search(
                q="question",
                corpus=None,
                k=5,
                document_type=None,
                tags=None,
                hybrid=False,
                rrf=True,
                rerank=False,
                db=object(),
            )

    assert caught.value.status_code == 422
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_context_rejects_ambiguous_scope_before_embedding():
    embed = Mock(side_effect=AssertionError("embedding must not run"))

    with (
        patch.object(
            context_router,
            "resolve_corpus_scope",
            AsyncMock(side_effect=CorpusScopeRequired("corpus is required")),
        ),
        patch.object(context_router.embedder, "embed_single", embed),
    ):
        with pytest.raises(HTTPException) as caught:
            await context_router.get_context(
                q="question",
                corpus=None,
                k=8,
                budget_tokens=4096,
                strategy="hybrid_rrf",
                db=object(),
            )

    assert caught.value.status_code == 422
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_ask_rejects_ambiguous_scope_before_embedding():
    @asynccontextmanager
    async def session_scope():
        yield object()

    embed = Mock(side_effect=AssertionError("embedding must not run"))

    with (
        patch.object(query_router, "session_scope", session_scope),
        patch.object(
            query_router,
            "resolve_corpus_scope",
            AsyncMock(side_effect=CorpusScopeRequired("corpus is required")),
        ),
        patch.object(query_router.embedder, "embed_single", embed),
    ):
        with pytest.raises(HTTPException) as caught:
            await query_router.ask(AskRequest(question="question"), Response(), debug=False)

    assert caught.value.status_code == 422
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_context_with_no_corpus_does_not_embed_or_retrieve():
    embed = Mock(side_effect=AssertionError("embedding must not run"))
    search = AsyncMock(side_effect=AssertionError("retrieval must not run"))

    with (
        patch.object(
            context_router,
            "resolve_corpus_scope",
            AsyncMock(return_value=(None, None)),
        ),
        patch.object(context_router.embedder, "embed_single", embed),
        patch.object(context_router.RetrievalService, "search", search),
    ):
        result = await context_router.get_context(
            q="question",
            corpus=None,
            k=8,
            budget_tokens=4096,
            strategy="hybrid_rrf",
            db=object(),
        )

    assert isinstance(result, ContextPackResponse)
    assert result.context == ""
    assert result.token_estimate == 0
    embed.assert_not_called()
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_ask_search_backend_failure_is_503():
    @asynccontextmanager
    async def session_scope():
        yield object()

    embed = Mock(return_value=[0.1])
    search = AsyncMock(side_effect=OperationalError("SELECT 1", {}, Exception("down")))

    with (
        patch.object(query_router, "session_scope", session_scope),
        patch.object(query_router, "resolve_corpus_scope", AsyncMock(return_value=("c1", "docs"))),
        patch.object(query_router.embedder, "embed_single", embed),
        patch.object(query_router.RetrievalService, "search", search),
    ):
        with pytest.raises(HTTPException) as caught:
            await query_router.ask(AskRequest(question="question"), Response(), debug=False)

    assert caught.value.status_code == 503
    embed.assert_called_once_with("question")
    search.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_with_no_corpus_does_not_embed_or_retrieve():
    @asynccontextmanager
    async def session_scope():
        yield object()

    embed = Mock(side_effect=AssertionError("embedding must not run"))
    search = AsyncMock(side_effect=AssertionError("retrieval must not run"))

    with (
        patch.object(query_router, "session_scope", session_scope),
        patch.object(query_router, "resolve_corpus_scope", AsyncMock(return_value=(None, None))),
        patch.object(query_router.embedder, "embed_single", embed),
        patch.object(query_router.RetrievalService, "search", search),
    ):
        result = await query_router.ask(
            AskRequest(question="question"),
            Response(),
            debug=False,
        )

    assert result.answer == "No relevant content found in the knowledge base."
    embed.assert_not_called()
    search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "request_model", "method", "expected_args", "expected_kwargs"),
    [
        (
            query_router.summarize,
            SummarizeRequest(document_id="doc"),
            "get_document_chunks",
            ("doc",),
            {"corpus_id": "c1"},
        ),
        (
            query_router.interview,
            InterviewRequest(document_id="doc"),
            "get_document_chunks",
            ("doc",),
            {"corpus_id": "c1"},
        ),
        (
            query_router.related,
            RelatedRequest(document_id="doc"),
            "get_related_documents",
            ("doc",),
            {"k": 5, "corpus_id": "c1"},
        ),
    ],
)
async def test_legacy_document_routes_pass_corpus_scope(
    route, request_model, method, expected_args, expected_kwargs
):
    @asynccontextmanager
    async def session_scope():
        yield object()

    retrieval = Mock()
    method_mock = AsyncMock(return_value=[])
    setattr(retrieval, method, method_mock)

    with (
        patch.object(query_router, "session_scope", session_scope),
        patch.object(query_router, "resolve_corpus_scope", AsyncMock(return_value=("c1", "docs"))),
        patch.object(query_router, "RetrievalService", return_value=retrieval),
    ):
        await route(request_model, Response())

    method_mock.assert_awaited_once_with(*expected_args, **expected_kwargs)


@pytest.mark.asyncio
async def test_timeline_route_passes_corpus_scope():
    @asynccontextmanager
    async def session_scope():
        yield object()

    retrieval = Mock()
    retrieval.get_timeline = AsyncMock(return_value=[])

    with (
        patch.object(query_router, "session_scope", session_scope),
        patch.object(query_router, "resolve_corpus_scope", AsyncMock(return_value=("c1", "docs"))),
        patch.object(query_router, "RetrievalService", return_value=retrieval),
    ):
        await query_router.timeline(
            document_type=None,
            start_date=None,
            end_date=None,
            limit=5,
            corpus=None,
            response=Response(),
        )

    retrieval.get_timeline.assert_awaited_once_with(None, None, None, 5, corpus_id="c1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args", "sql_fragment"),
    [
        (
            "get_document_chunks",
            {"doc_id": "doc", "corpus_id": "c1"},
            "d.corpus_id = :corpus_id",
        ),
        (
            "get_related_documents",
            {"doc_id": "doc", "k": 5, "corpus_id": "c1"},
            "d.corpus_id = :corpus_id",
        ),
        (
            "get_timeline",
            {"limit": 5, "corpus_id": "c1"},
            "corpus_id = :corpus_id",
        ),
    ],
)
async def test_retrieval_document_queries_bind_corpus(method, args, sql_fragment):
    db = _db(_Result())
    service = RetrievalService(db)

    await getattr(service, method)(**args)

    statement, params = db.execute.await_args.args
    assert sql_fragment in str(statement)
    assert params["corpus_id"] == "c1"
