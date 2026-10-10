from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from api.main import app


async def _clean_posture(_db):
    """A least-privileged runtime role, as `inspect` would report it."""
    from api.services.posture import DatabasePosture

    return DatabasePosture(
        role="mindpalace_runtime",
        is_superuser=False,
        bypasses_rls=False,
        owns_protected_tables=[],
        unprotected_tables=[],
        settable_privileged_roles=[],
        excess_privileges=[],
        can_enumerate_credentials=False,
        archive_delete_granted=[],
    )


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

    # The posture probes are stubbed out rather than answered by the same stub.
    # This test is about whether a healthy schema reports ready; whether a role
    # actually enforces the policies is a separate question with a separate set
    # of tests against a real role, in `test_credential_management.py`.
    monkeypatch.setattr("api.services.db.session_scope", lambda: ReadySession())
    monkeypatch.setattr("api.main.inspect_posture", _clean_posture)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    # `auth` is part of readiness on purpose: an operator who cannot tell `off`
    # from `required` cannot tell a tenant-safe deployment from one that will
    # answer anyone who asks.
    #
    # The posture verdict is here for the same reason: `required: true` says the
    # application checks credentials, not that the database enforces anything
    # underneath. Readiness reports the verdict and stops -- the role, the
    # BYPASSRLS flag and the table inventory are `mindpalace posture`'s job, for
    # an operator who already holds database credentials, not for anything that
    # can reach the port. See `test_credential_management.py` for the real role.
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "row_level_security": "enforced"}
    assert body["auth"] == {"mode": "off", "required": False}
    assert "database_posture" not in body


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
