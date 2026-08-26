"""Contract tests for the public corpus/context API (Milestone 1 + 5).

These pin the product surface:
- POST/GET/DELETE /api/corpora
- POST /api/corpora/{name}/sync
- GET /api/context — model-ready context with attribution and budgets
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from api.main import app


@pytest.mark.asyncio
async def test_create_corpus_201():
    transport = ASGITransport(app=app)
    with patch(
        "api.services.corpora.create_corpus",
        new_callable=AsyncMock,
        return_value={"id": "c1", "name": "docs", "description": ""},
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/corpora", json={"name": "docs", "description": ""})
    assert resp.status_code == 201
    assert resp.json()["name"] == "docs"


@pytest.mark.asyncio
async def test_create_duplicate_corpus_409():
    transport = ASGITransport(app=app)
    with patch(
        "api.services.corpora.create_corpus",
        new_callable=AsyncMock,
        side_effect=ValueError("corpus 'docs' already exists"),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/corpora", json={"name": "docs"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_invalid_corpus_name_422():
    transport = ASGITransport(app=app)
    with patch(
        "api.services.corpora.create_corpus",
        new_callable=AsyncMock,
        side_effect=ValueError("corpus name may contain only letters, digits, '-', '_', '.'"),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/corpora", json={"name": "bad name!"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_missing_corpus_404():
    transport = ASGITransport(app=app)
    with patch(
        "api.services.corpora.corpus_stats",
        new_callable=AsyncMock,
        return_value=None,
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/corpora/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_corpus_404():
    transport = ASGITransport(app=app)
    with patch(
        "api.services.corpora.delete_corpus",
        new_callable=AsyncMock,
        return_value=False,
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/corpora/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_corpora_db_unavailable_503():
    transport = ASGITransport(app=app)
    with patch(
        "api.services.corpora.list_corpora",
        new_callable=AsyncMock,
        side_effect=OperationalError("SELECT 1", {}, Exception("down")),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/corpora")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_sync_returns_machine_readable_summary():
    transport = ASGITransport(app=app)
    summary = {
        "success": True,
        "corpus_id": "c1",
        "added": 2,
        "changed": 1,
        "unchanged": 3,
        "deleted": 1,
        "failed": 0,
        "chunk_count": 10,
        "duration_ms": 250,
        "message": "sync: +2 ~1 =3 -1 !0, 10 chunks",
    }
    with (
        patch(
            "api.services.corpora.get_corpus_by_name",
            new_callable=AsyncMock,
            return_value={"id": "c1", "name": "docs", "description": ""},
        ),
        patch(
            "api.services.ingestion.IngestionService.sync_repo",
            new_callable=AsyncMock,
            return_value=summary,
        ),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/corpora/docs/sync", json={"path": "/some/dir"})
    assert resp.status_code == 200
    data = resp.json()
    for key in ("added", "changed", "unchanged", "deleted", "failed", "duration_ms"):
        assert key in data
    assert data["added"] == 2


@pytest.mark.asyncio
async def test_context_endpoint_contract():
    """The context endpoint returns bounded, attributable context."""
    from api.services.retrieval import RetrievalResult

    def make_result(text, title, doc_id):
        return RetrievalResult(
            text=text,
            heading_path="H",
            document_type="note",
            source_url=None,
            tags=[],
            source_title=title,
            source_path=f"p/{title}",
            source_document_type="note",
            score=0.9,
            doc_id=doc_id,
        )

    results = [
        make_result("alpha evidence text", "DocA", "id-a"),
        make_result("beta evidence text", "DocB", "id-b"),
    ]

    transport = ASGITransport(app=app)
    import api.routers.context as cmod

    with patch.object(
        cmod.RetrievalService,
        "search",
        new_callable=AsyncMock,
        return_value=results,
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/context",
                params={"q": "test question", "budget_tokens": 1024},
            )

    assert resp.status_code == 200
    data = resp.json()

    # Contract fields from the milestone spec
    for key in ("query", "context", "sources", "chunks", "token_estimate", "strategy", "truncated"):
        assert key in data, f"missing contract field: {key}"

    assert data["query"] == "test question"
    assert len(data["sources"]) >= 1
    assert data["token_estimate"] > 0
    assert data["strategy"] == "hybrid_rrf"
    assert isinstance(data["truncated"], bool)
    # attribution must reference retrieved docs
    titles = {s["title"] for s in data["sources"]}
    assert {"DocA", "DocB"} <= titles


@pytest.mark.asyncio
async def test_context_invalid_strategy_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/context", params={"q": "x", "strategy": "bogus"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_context_unknown_corpus_404():
    transport = ASGITransport(app=app)
    with patch(
        "api.routers.context.get_corpus_by_name",
        new_callable=AsyncMock,
        return_value=None,
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/context", params={"q": "x", "corpus": "ghost"})
    assert resp.status_code == 404
