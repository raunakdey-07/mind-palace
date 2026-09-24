from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from api.main import app


def test_liveness_does_not_require_database(monkeypatch):
    async def unavailable():
        raise OSError("connection refused")

    monkeypatch.setattr("api.services.db.init_db", unavailable)
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_reports_healthy_schema(monkeypatch):
    class ReadySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, _query):
            return type("Result", (), {"one": lambda self: (True, True, True)})()

    monkeypatch.setattr("api.services.db.session_scope", lambda: ReadySession())
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok"},
    }


def test_readiness_reports_missing_schema(monkeypatch):
    class MissingSchemaSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, _query):
            return type("Result", (), {"one": lambda self: (True, False, True)})()

    monkeypatch.setattr("api.services.db.session_scope", lambda: MissingSchemaSession())
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_readiness_reports_timeout_without_details(monkeypatch):
    class SlowSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, _query):
            await asyncio.sleep(0.05)
            return type("Result", (), {"one": lambda self: (True, True, True)})()

    monkeypatch.setenv("MIND_PALACE_READINESS_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setattr("api.services.db.session_scope", lambda: SlowSession())
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable"},
    }
    assert "traceback" not in response.text.lower()
    assert "slow" not in response.text.lower()


def test_readiness_reports_database_failure(monkeypatch):
    class BrokenSession:
        async def __aenter__(self):
            raise OSError("connection refused")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr("api.services.db.session_scope", lambda: BrokenSession())
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "connection refused" not in response.text
