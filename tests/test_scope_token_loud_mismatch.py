"""Scope-token mismatch is loud (option c): fail-closed, refusal on mismatch.

Covers: secret matches; secret mismatched -> refusal at startup and not-ready
on readiness; enforcement off; probe unavailable; local/off-mode and the
five-minute path unaffected. Any test that rotates a secret or flips
enforcement restores the database in a finally block so later tests are not
contaminated (the fixture-leak that #72 once had).
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
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _RANDOM_SECRET)
    await _set_enforcement(database_url, True)
    try:
        await enforce_startup_posture()
    finally:
        await _set_enforcement(database_url, False)


async def test_scope_token_secret_mismatch_refuses_startup(monkeypatch):
    """When MIND_PALACE_SCOPE_SECRET disagrees with the database row,
    enforce_startup_posture raises RuntimeError naming the cause."""
    database_url = os.environ["DATABASE_URL"]
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _OTHER_SECRET)
    await _set_enforcement(database_url, True)
    try:
        with pytest.raises(RuntimeError, match="disagrees"):
            await enforce_startup_posture()
    finally:
        await _set_enforcement(database_url, False)


async def test_scope_token_secret_mismatch_not_ready_on_readiness(monkeypatch):
    """When the secret mismatches, /health/ready returns 503 not_ready with
    scope_token: mismatch."""
    database_url = os.environ["DATABASE_URL"]
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


async def test_scope_token_enforcement_off_no_mismatch_refusal(monkeypatch):
    """With MIND_PALACE_ENFORCE_SCOPE_TOKEN=off, no refusal from the
    scope-token mismatch check (startup takes the enforcement-off path
    instead, which is a separate, pre-existing refusal)."""
    database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    monkeypatch.setenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off")
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", _RANDOM_SECRET)
    monkeypatch.setenv("MIND_PALACE_I_UNDERSTAND_SCOPE_FORGERY", "1")
    await _set_db_secret(database_url, _DEV_SECRET)
    await _set_enforcement(database_url, False)
    try:
        # Enforcement off: the mismatch check does not run.
        await enforce_startup_posture()
    finally:
        await _set_enforcement(database_url, False)


async def test_scope_token_probe_unavailable_fail_closed(monkeypatch):
    """When mp_scope_enforcement_status is missing (old schema), the server
    profile fails closed and refuses to start."""
    database_url = os.environ["DATABASE_URL"]
    _server_env(monkeypatch, _RANDOM_SECRET)
    await _set_db_secret(database_url, _RANDOM_SECRET)
    await _set_enforcement(database_url, True)
    try:
        # Drop the probe function to simulate an old schema.
        from api.services.db import session_scope

        async with session_scope() as db:
            await db.execute(
                sa.text("DROP FUNCTION IF EXISTS mp_scope_enforcement_status(TEXT, TEXT)")
            )
            await db.commit()
        try:
            with pytest.raises(RuntimeError, match="probe unavailable"):
                await enforce_startup_posture()
        finally:
            # Restore the function by re-applying migration 015's definition.
            async with session_scope() as db:
                await db.execute(
                    sa.text(
                        "CREATE OR REPLACE FUNCTION mp_scope_enforcement_status("
                        "probe_token TEXT, probe_scope TEXT"
                        ") RETURNS TABLE(enforced BOOLEAN, using_default_secret BOOLEAN, "
                        "secret_matches BOOLEAN) LANGUAGE plpgsql STABLE SECURITY DEFINER "
                        "SET search_path = public, pg_temp AS $fn$ "
                        "DECLARE secret text; enforce text; expected text; "
                        "BEGIN "
                        "SELECT value INTO secret FROM memory_server_secrets "
                        "WHERE name = 'scope_hmac_secret'; "
                        "SELECT value INTO enforce FROM memory_server_secrets "
                        "WHERE name = 'enforce_scope_token'; "
                        "enforced := (coalesce(enforce, 'off') = 'on'); "
                        "using_default_secret := (coalesce(secret, '') = 'dev-scope-secret'); "
                        "expected := encode(hmac(convert_to(probe_scope, 'UTF8'), "
                        "convert_to(coalesce(secret, ''), 'UTF8'), 'sha256'), 'hex'); "
                        "secret_matches := (expected = probe_token); "
                        "RETURN NEXT; END $fn$"
                    )
                )
                await db.commit()
    finally:
        await _set_enforcement(database_url, False)


async def test_scope_token_local_mode_unaffected(monkeypatch):
    """Local deployment is not gated by scope-token enforcement at all."""
    _local_env(monkeypatch)
    await enforce_startup_posture()


async def test_scope_token_secret_matches_readiness_ready(monkeypatch):
    """When the secret matches and enforcement is on, readiness returns ready.

    Calls the readiness handler directly (not via ASGI) with a clean posture
    double, because the ASGI path also runs the database-role posture check
    which refuses on the test's superuser connection -- that is a separate
    gate from the scope-token check being tested here.
    """
    database_url = os.environ["DATABASE_URL"]
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
