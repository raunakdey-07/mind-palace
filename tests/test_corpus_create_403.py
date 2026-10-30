"""POST /api/corpora returns 500 instead of 403 when permission is missing.

The route calls create_corpus which raises AuthorizationError (403), but the
result was a 500 Internal Server Error instead.

Root cause (reproduced): in off auth mode LOCAL_PRINCIPAL is unrestricted, so
both permission checks in create_corpus are bypassed and the INSERT into
`corpora` runs as the least-privileged runtime role. Row-level security
rejects the row (`new row violates row-level security policy for table
"corpora"`, a ProgrammingError/InsufficientPrivilege), which escaped as an
unhandled 500 instead of being translated to a 403.

Fix: api/routers/corpora.py catches the row-level-security ProgrammingError at
the create-corpus path and returns 403 with code `forbidden`, without leaking
database internals in the response or the log. Only SQLSTATE 42501 (RLS
violations) is translated; other ProgrammingErrors still propagate as 500.

This is a pre-existing bug, not a regression from migration 015. Fix in this
branch (m018/corpus-403-followups).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import ProgrammingError

from api.services import authz
from test_tenant_isolation import world as _shared_world_fixture

world = _shared_world_fixture


@pytest.mark.asyncio
async def test_corpus_create_returns_403_not_500(world, monkeypatch):
    """POST /api/corpora by LOCAL_PRINCIPAL must return 403, not 500.

    LOCAL_PRINCIPAL is unrestricted, so create_corpus bypasses its permission
    checks and attempts the INSERT as the runtime role. Row-level security
    rejects the row, which used to escape as a 500 ProgrammingError. The
    create-corpus path now translates it to 403.
    """
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", "dev-scope-secret")
    monkeypatch.setenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off")

    response = await world.client.post(
        "/api/corpora",
        json={"name": "should-fail", "description": "test"},
    )
    # Should be 403 (forbidden), not 500 (internal error).
    assert response.status_code == 403, f"expected 403, got {response.status_code}: {response.text}"
    body = response.json()
    assert body.get("detail", {}).get("code") == "forbidden", body


@pytest.mark.asyncio
async def test_admin_cannot_create_new_corpus_over_http(world):
    """A principal with manage_corpus cannot create a brand-new corpus over
    HTTP today (returns 403). Documented known gap; corpora are created by the
    operator with `mindpalace corpus create`. The world fixture runs in
    required auth mode, so the issued admin credential authenticates.

    world.db_module.admin_session_scope() and principal_scope(ADMIN_PRINCIPAL)
    are used to issue the credential, mirroring how other administrative tests
    operate against the runtime-role app. The INSERT runs as the runtime role
    and hits the RLS WITH CHECK on `corpora`, which we translate to 403.
    """
    async with world.db_module.admin_session_scope() as db:
        with world.db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            cred = await authz.issue_credential(
                db, "http-admin-creator", ["alpha-brand"], "principal_admin"
            )

    headers = {"Authorization": f"Bearer {cred['secret']}"}
    response = await world.client.post(
        "/api/corpora",
        json={"name": "brand-new-http-corpus", "description": "known-gap"},
        headers=headers,
    )
    # Currently 403: get_corpus_by_name returns None for an unrestricted
    # principal on a new corpus (RLS filters the SELECT), so the INSERT runs
    # and hits the RLS WITH CHECK on `corpora`, which we translate to 403.
    # Corpora are created by the operator with `mindpalace corpus create`.
    assert response.status_code == 403, f"expected 403, got {response.status_code}: {response.text}"
    assert response.json()["detail"]["code"] == "forbidden", response.json()


@pytest.mark.asyncio
async def test_corpus_create_programming_error_propagates_500(world, monkeypatch):
    """A ProgrammingError that is NOT a row-level-security violation must
    propagate as a 500, not be converted to a 403.

    Only SQLSTATE 42501 (insufficient_privilege, raised by RLS violations)
    is translated to 403. Other ProgrammingErrors (e.g. 42P01 undefined_table)
    must still surface as 500 so operators can see real database errors.
    """
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", "dev-scope-secret")
    monkeypatch.setenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off")

    mock_orig = MagicMock()
    mock_orig.sqlstate = "42P01"  # undefined_table, not an RLS violation
    error = ProgrammingError("SELECT * FROM nonexistent_table", None, mock_orig)

    async def mock_create_corpus(*args, **kwargs):
        raise error

    with patch("api.services.corpora.create_corpus", side_effect=mock_create_corpus):
        response = await world.client.post(
            "/api/corpora",
            json={"name": "should-still-fail", "description": "test"},
        )

    assert response.status_code == 500, f"expected 500, got {response.status_code}: {response.text}"
