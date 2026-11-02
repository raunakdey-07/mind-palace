"""Scope-token mismatch is loud (option c): fail-closed, refusal on mismatch.

Covers: secret matches; secret mismatched -> refusal at startup and not-ready
on readiness; enforcement off; probe unavailable; local/off-mode and the
five-minute path unaffected.

Every test that mutates `memory_server_secrets` or `enforce_scope_token`
restores both in its own `finally` block, so no test leaks database state
into later tests in the session (the fixture-leak that #72 once had). The
canary test at the end of the module verifies the database is clean after
all tests run.
"""

from __future__ import annotations

import json as _json
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from httpx import ASGITransport, AsyncClient

from api.main import app, enforce_startup_posture, readiness

_DEV_SECRET = "dev-scope-secret"
_RANDOM_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_OTHER_SECRET = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"

#: Initial state of memory_server_secrets, captured synchronously at module
#: import time. Used by the canary test to verify no state leaked into the
#: session database.
try:
    _INIT_ENGINE = sa.create_engine(
        os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
    )
    with _INIT_ENGINE.connect() as _init_conn:
        _INITIAL_SECRETS = _init_conn.execute(
            sa.text("SELECT name, value FROM memory_server_secrets ORDER BY name")
        ).fetchall()
    _INIT_ENGINE.dispose()
except Exception:
    _INITIAL_SECRETS = []


class _PassingPosture:
    """Posture with no server problems, so server-mode tests are gated only
    on the scope-token check, not on the connected role's shape."""

    def problems(self):
        return []

    def server_problems(self):
        return []


async def _fake_inspect(db):
    return _PassingPosture()


@pytest.fixture(autouse=True)
def _clean_posture(monkeypatch):
    """Make posture.clean so tests are gated only on scope-token, not role."""
    import api.services.posture as _posture_mod
    import api.main as _main_mod

    monkeypatch.setattr(_posture_mod, "inspect", _fake_inspect, raising=True)
    monkeypatch.setattr(_main_mod, "inspect_posture", _fake_inspect, raising=True)


async def _snapshot_secrets(database_url: str):
    """Return (rows, enforcement_value) from memory_server_secrets."""
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as conn:
            rows = await conn.execute(
                sa.text("SELECT name, value FROM memory_server_secrets ORDER BY name")
            )
            all_rows = rows.fetchall()
            enforcement = await conn.execute(
                sa.text(
                    "SELECT value FROM memory_server_secrets WHERE name = 'enforce_scope_token'"
                )
            )
            enforcement_value = enforcement.scalar()
            return all_rows, enforcement_value
    finally:
        await engine.dispose()


async def _restore_secrets(database_url: str, original_rows, original_enforcement):
    """Restore memory_server_secrets to the given rows and enforcement value."""
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as conn:
            current = await conn.execute(
                sa.text("SELECT name, value FROM memory_server_secrets ORDER BY name")
            )
            current_map = {name: value for name, value in current.fetchall()}
            original_map = {name: value for name, value in original_rows}
            for name, value in original_map.items():
                if name not in current_map or current_map[name] != value:
                    await conn.execute(
                        sa.text(
                            "INSERT INTO memory_server_secrets (name, value) VALUES "
                            "(:n, :v) ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
                        ),
                        {"n": name, "v": value},
                    )
            for name in current_map:
                if name not in original_map:
                    await conn.execute(
                        sa.text("DELETE FROM memory_server_secrets WHERE name = :n"),
                        {"n": name},
                    )
            if original_enforcement is not None:
                await conn.execute(
                    sa.text("SELECT mp_set_scope_token_enforcement(:e)"),
                    {"e": original_enforcement == "on"},
                )
    finally:
        await engine.dispose()


async def _set_db_secret(database_url: str, secret: str) -> None:
    """Set scope_hmac_secret in memory_server_secrets."""
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO memory_server_secrets (name, value) VALUES "
                    "('scope_hmac_secret', :v) "
                    "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"v": secret},
            )
    finally:
        await engine.dispose()


async def _set_enforcement(database_url: str, enabled: bool) -> None:
    """Toggle enforce_scope_token in the database."""
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(:e)"), {"e": enabled})
    finally:
        await engine.dispose()


def _server_env(monkeypatch, scope_secret: str) -> None:
    """Configure the process as a server with enforcement on and a given secret."""
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    monkeypatch.setenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "on")
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", scope_secret)
    monkeypatch.delenv("MIND_PALACE_I_UNDERSTAND_SCOPE_FORGERY", raising=False)


def _local_env(monkeypatch) -> None:
    """Configure the process as local (the five-minute path)."""
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "local")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    monkeypatch.delenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", raising=False)
    monkeypatch.delenv("MIND_PALACE_SCOPE_SECRET", raising=False)


async def test_scope_token_secret_matches_startup_ok(monkeypatch):
    """When MIND_PALACE_SCOPE_SECRET matches the database secret and
    enforcement is on, server startup succeeds."""
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _RANDOM_SECRET)
    await _set_enforcement(database_url, True)
    try:
        await enforce_startup_posture()
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_secret_mismatch_refuses_startup(monkeypatch):
    """When MIND_PALACE_SCOPE_SECRET disagrees with the database row,
    enforce_startup_posture raises RuntimeError naming the cause."""
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _OTHER_SECRET)
    await _set_enforcement(database_url, True)
    try:
        with pytest.raises(RuntimeError, match="disagrees"):
            await enforce_startup_posture()
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_secret_mismatch_not_ready_on_readiness(monkeypatch):
    """When the secret mismatches, /health/ready returns 503 not_ready with
    scope_token: mismatch."""
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _OTHER_SECRET)
    await _set_enforcement(database_url, True)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["scope_token"] == "mismatch"
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_mismatch_env_off_db_off(monkeypatch):
    """Mismatched secret, env enforcement off, DB enforcement off.

    The process always sends a token (_apply_tenant_scope computes HMAC on
    every transaction). mp_verified_scope_corpora() compares the supplied
    token to the stored secret regardless of the enforcement flag, so a
    non-empty token that does not verify returns zero rows even with
    enforcement off. In server mode this is a not-ready condition.
    """
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    monkeypatch.setenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off")
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", _RANDOM_SECRET)
    monkeypatch.setenv("MIND_PALACE_I_UNDERSTAND_SCOPE_FORGERY", "1")
    await _set_db_secret(database_url, _OTHER_SECRET)
    await _set_enforcement(database_url, False)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["scope_token"] == "mismatch"
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_mismatch_env_on_db_off(monkeypatch):
    """Mismatched secret, env enforcement on, DB enforcement off.

    Same rule: the process sends a token that won't verify, so readiness
    is not-ready even though the database has enforcement disabled.
    """
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _OTHER_SECRET)
    await _set_enforcement(database_url, False)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["scope_token"] == "mismatch"
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_mismatch_env_off_db_on(monkeypatch):
    """Mismatched secret, env enforcement off, DB enforcement on.

    Startup takes the enforcement-off path (no refusal). Readiness always
    probes in server mode and reports not-ready because the token doesn't
    verify.
    """
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    monkeypatch.setenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off")
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", _RANDOM_SECRET)
    monkeypatch.setenv("MIND_PALACE_I_UNDERSTAND_SCOPE_FORGERY", "1")
    await _set_db_secret(database_url, _OTHER_SECRET)
    await _set_enforcement(database_url, True)
    try:
        # Startup: enforcement off, so no refusal.
        await enforce_startup_posture()
        # Readiness: always probes, finds mismatch -> not-ready.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["scope_token"] == "mismatch"
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_secret_matches_readiness_ready(monkeypatch):
    """When the secret matches and enforcement is on, readiness returns ready.

    Calls the readiness handler directly (not via ASGI) with a clean posture
    double, because the ASGI path also runs the database-role posture check
    which refuses on the test's superuser connection -- that is a separate
    gate from the scope-token check being tested here.
    """
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _RANDOM_SECRET)
    await _set_enforcement(database_url, True)
    try:
        import api.main as _main_mod

        monkeypatch.setattr(_main_mod, "inspect_posture", _fake_inspect, raising=True)
        resp = await readiness()
        assert resp.status_code == 200
        data = _json.loads(resp.body)
        assert data["status"] == "ready"
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_local_mode_unaffected(monkeypatch):
    """Local deployment is not gated by scope-token enforcement at all."""
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    _local_env(monkeypatch)
    try:
        await enforce_startup_posture()
    finally:
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_probe_returns_none_when_function_dropped(monkeypatch):
    """_probe_scope_token returns None when mp_scope_enforcement_status is
    missing. The function is dropped inside a transaction that is rolled back,
    so no persistent DDL changes the session database (DDL is transactional
    in PostgreSQL)."""
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    from api.services.db import session_scope
    from api.main import _probe_scope_token

    async with session_scope() as db:
        await db.execute(sa.text("DROP FUNCTION IF EXISTS mp_scope_enforcement_status(TEXT, TEXT)"))
        try:
            probed = await _probe_scope_token(db)
            assert probed is None, f"expected None, got {probed}"
        finally:
            await db.rollback()
    await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_probe_unavailable_fail_closed(monkeypatch):
    """When the probe is unavailable, the server profile fails closed and
    refuses to start, and readiness reports not-ready.

    Monkeypatch _probe_scope_token to return None (simulating a missing
    function on an old schema) rather than dropping it with persistent DDL.
    """
    database_url = os.environ["DATABASE_URL"]
    snapshot = await _snapshot_secrets(database_url)
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _RANDOM_SECRET)
    await _set_enforcement(database_url, True)
    try:
        import api.main as _main_mod

        async def _probe_none(db):  # noqa: ARG001
            return None

        monkeypatch.setattr(_main_mod, "_probe_scope_token", _probe_none, raising=False)
        with pytest.raises(RuntimeError, match="probe unavailable"):
            await enforce_startup_posture()
        # Readiness also reports not-ready via the probe path.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["scope_token"] == "mismatch"
    finally:
        await _set_enforcement(database_url, False)
        await _restore_secrets(database_url, snapshot[0], snapshot[1])


async def test_scope_token_secrets_restored_canary():
    """Canary: memory_server_secrets and enforce_scope_token must be at
    their initial values after this module runs. Fails if any test leaked
    state into the session database."""
    if not _INITIAL_SECRETS:
        pytest.skip("could not capture initial database state")

    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as conn:
            rows = await conn.execute(
                sa.text("SELECT name, value FROM memory_server_secrets ORDER BY name")
            )
            actual = rows.fetchall()
            enforcement = await conn.execute(
                sa.text(
                    "SELECT value FROM memory_server_secrets WHERE name = 'enforce_scope_token'"
                )
            )
            enforcement_value = enforcement.scalar()
    finally:
        await engine.dispose()

    expected = [tuple(r) for r in _INITIAL_SECRETS]
    assert actual == expected, f"secrets changed: expected {expected}, got {actual}"
    expected_enforcement = None
    for name, value in _INITIAL_SECRETS:
        if name == "enforce_scope_token":
            expected_enforcement = value
    assert (
        enforcement_value == expected_enforcement
    ), f"enforcement changed: expected {expected_enforcement}, got {enforcement_value}"
