"""Integration tests for Mind Palace API endpoints."""

import importlib.metadata
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
async def test_openapi_describes_the_product_not_a_rag_wrapper():
    """The API description is part of the public contract.

    It was corrected from an outdated RAG framing once and should not silently
    drift back. The reported version is the installed package version, so the
    OpenAPI document cannot disagree with the distribution.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    info = resp.json()["info"]
    assert info["description"] == "The Durable AI Memory Substrate"
    assert info["title"] == "Mind Palace API"
    assert info["version"] == importlib.metadata.version("mindpalace-os")


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200


def _working_embedder():
    """A vector producer that works, so a test can reach the search call.

    `/api/search` embeds the query *before* it searches, and that embed needs the
    embedding model. Two tests below patch `RetrievalService.search` and expect to
    observe its behaviour -- so they must also satisfy the step in front of it.

    They used to depend on the developer machine happening to have the model
    cached. On a runner with no model cache and `HF_HUB_OFFLINE=1`, the embed
    raised, the endpoint answered 503, and one test failed while the other passed
    *for the wrong reason*: its 503 came from the model, not from the database
    outage it was written to check. Both failures were the same missing
    precondition, so both now state it explicitly.

    A model that genuinely cannot be loaded is a separate, already-tested contract:
    see `test_semantic_dependency_failure_is_sanitized_503`.
    """
    return Mock(embed_single=Mock(return_value=[0.0] * 8))


@pytest.mark.asyncio
async def test_search_endpoint_no_results():
    """A search that ran and matched nothing is 200 with zero results.

    Not a 503. 'No results' and 'backend down' are different answers and a client
    must be able to tell them apart.
    """
    transport = ASGITransport(app=app)

    with (
        patch(
            "api.routers.search.resolve_corpus_scope",
            new_callable=AsyncMock,
            return_value=("c1", "docs"),
        ),
        patch.object(search_router, "embedder", _working_embedder()),
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
    # Asserted, not assumed: if the embed had failed the handler would have raised
    # before searching, and this count is what proves the search actually ran.
    mock_search.assert_awaited_once()
    assert mock_search.await_args.kwargs["corpus_id"] == "c1"
    assert mock_search.await_args.kwargs["query_vector"] == [0.0] * 8
    assert mock_search.await_args.kwargs["query_text"] == "unlikely-query"

    data = response.json()

    assert data["results"] == []
    assert data["total"] == 0
    assert data["query"] == "unlikely-query"


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
        patch.object(search_router, "embedder", _working_embedder()),
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
    # The exact detail, not merely a word. 'unavailable' also appears in
    # 'Semantic model unavailable', so a loose assertion here passes even when the
    # database outage never happened -- which is exactly what it did.
    assert response.json()["detail"] == "Search backend unavailable"
    # And the search really was reached, so the 503 came from where it claims.
    mock_search.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["search", "ask"])
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
async def test_context_degrades_instead_of_503_when_model_is_missing():
    """A missing model must cost the pack its raw material, not the whole answer.

    The endpoint stays 200, reports why, and leaks no dependency detail.
    """
    import api.services.context_service as cserv
    import api.services.embedder as embedder_mod

    transport = ASGITransport(app=app)
    with (
        patch.object(
            context_router,
            "resolve_corpus_scope",
            AsyncMock(return_value=("c1", "docs")),
        ),
        patch.object(
            cserv, "get_corpus_by_name", AsyncMock(return_value={"id": "c1", "name": "docs"})
        ),
        patch.object(cserv, "archive_present", AsyncMock(return_value=False)),
        patch.object(
            embedder_mod,
            "Embedder",
            Mock(side_effect=OSError("missing local model")),
        ),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/context", params={"q": "q", "corpus": "docs"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "empty_corpus"
    assert body["context"] == ""
    assert "missing local model" not in response.text


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
