"""Integration tests for Mind Palace API endpoints."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

import api.routers.context as context_router
import api.routers.ingest as ingest_router
import api.routers.query as query_router
import api.routers.search as search_router
from api.main import app
from api.models.schemas import AskRequest


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_endpoint_no_results():
    transport = ASGITransport(app=app)

    with (
        patch(
            "api.routers.search.resolve_corpus_scope",
            new_callable=AsyncMock,
            return_value=("c1", "docs"),
        ),
        patch(
            "api.routers.search.RetrievalService.search",
            new_callable=AsyncMock,
        ) as mock_search,
    ):
        mock_search.return_value = []

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/search",
                params={"q": "unlikely-query"},
            )

    assert response.status_code == 200
    mock_search.assert_awaited_once()
    assert mock_search.await_args.kwargs["corpus_id"] == "c1"

    data = response.json()

    assert data["results"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_search_backend_unavailable_is_503_not_empty():
    """Regression: DB outages must return 503, not an empty 200 response.

    Clients must be able to distinguish 'no results' from 'backend down'.
    """
    transport = ASGITransport(app=app)

    with (
        patch(
            "api.routers.search.resolve_corpus_scope",
            new_callable=AsyncMock,
            return_value=("c1", "docs"),
        ),
        patch(
            "api.routers.search.RetrievalService.search",
            new_callable=AsyncMock,
        ) as mock_search,
    ):
        mock_search.side_effect = OperationalError("SELECT 1", {}, Exception("connection refused"))

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/api/search", params={"q": "anything"})

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["search", "context", "ask"])
async def test_semantic_dependency_failure_is_sanitized_503(operation):
    if operation == "search":
        module = search_router

        async def target():
            return await module.search(
                q="q",
                corpus="c",
                k=5,
                document_type=None,
                tags=None,
                hybrid=False,
                rrf=True,
                rerank=False,
                db=object(),
            )

    elif operation == "context":
        module = context_router

        async def target():
            return await module.get_context(
                q="q", corpus="c", k=5, budget_tokens=512, strategy="vector", db=object()
            )

    else:
        module = query_router

        async def target():
            return await module.ask(AskRequest(question="q", corpus="c"), Response(), debug=False)

    with (
        patch.object(module, "resolve_corpus_scope", AsyncMock(return_value=("c", "docs"))),
        patch.object(
            module,
            "embedder",
            Mock(embed_single=Mock(side_effect=OSError("missing local model"))),
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            await target()

    assert caught.value.status_code == 503
    assert "missing local model" not in str(caught.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["file", "repo"])
async def test_ingest_model_failure_is_sanitized_503(route, monkeypatch):
    service = Mock()
    service.ingest_file = AsyncMock(side_effect=OSError("missing local model"))
    service.ingest_repo = AsyncMock(side_effect=OSError("missing local model"))
    monkeypatch.setattr(ingest_router, "ingestion_service", service)

    if route == "file":
        request = __import__(
            "api.models.schemas", fromlist=["IngestFileRequest"]
        ).IngestFileRequest(content="x", path="x.md")
        with pytest.raises(HTTPException) as caught:
            await ingest_router.ingest_file(request)
    else:
        request = __import__(
            "api.models.schemas", fromlist=["IngestRepoRequest"]
        ).IngestRepoRequest(repo_path=".")
        with pytest.raises(HTTPException) as caught:
            await ingest_router.ingest_repo(request)

    assert caught.value.status_code == 503
    assert "missing local model" not in str(caught.value.detail)


@pytest.mark.asyncio
async def test_query_endpoint_empty():
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post("/api/query", json={})

    assert response.status_code in (400, 422)
