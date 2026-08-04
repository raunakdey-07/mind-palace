"""Integration tests for Mind Palace API endpoints."""

import pytest
from api.main import app
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_search_endpoint_no_results():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/search?q=nonexistent+xyz+123&k=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "nonexistent xyz 123"
    assert data["total"] >= 0  # May be 0 if DB is empty


@pytest.mark.asyncio
async def test_query_endpoint_empty():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/api/query", json={"question": "test", "k": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert isinstance(data["sources"], list)
    assert isinstance(data["snippets"], list)
