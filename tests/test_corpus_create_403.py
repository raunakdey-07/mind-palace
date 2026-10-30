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
database internals in the response or the log.

This is a pre-existing bug, not a regression from migration 015. Fix in this
branch (m018/corpus-creation-403).
"""

from __future__ import annotations

import pytest

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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MIND_PALACE_HTTP_CORPUS_CREATE_XFAIL: POST /api/corpora with a "
        "manage_corpus credential cannot create a brand-new corpus in server "
        "mode: get_corpus_by_name requires the caller to already hold scope "
        "over the target corpus, impossible for a corpus that does not exist "
        "yet, so it returns 404. Corpora are created by the operator with "
        "`mindpalace corpus create`. Revisit this test if the HTTP creation "
        "path is redesigned."
    ),
)
@pytest.mark.asyncio
async def test_admin_cannot_create_new_corpus_over_http(world):
    """A principal with manage_corpus cannot create a brand-new corpus over
    HTTP today (returns 404). Documented known gap; corpora are created by the
    operator with `mindpalace corpus create`. The world fixture runs in
    required auth mode, so the issued admin credential authenticates.

    world.db_module.admin_session_scope() and principal_scope(ADMIN_PRINCIPAL)
    are used to issue the credential, mirroring how other administrative tests
    operate against the runtime-role app.
    """
    async with world.db_module.admin_session_scope() as db:
        with world.db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            cred = await authz.issue_credential(
                db, "http-admin-creator", ["alpha-brand"], "principal_admin"
            )

    headers = {"Authorization": f"Bearer {cred['secret']}"}
    response = await world.client.post(
        "/api/corpora",
        json={"name": "brand-new-http-corpus-xfail", "description": "xfail"},
        headers=headers,
    )
    # Currently 404 (documented gap). When the HTTP creation path is redesigned
    # to allow operator creation of a new corpus, this becomes 201 and the
    # xfail can be removed.
    assert response.status_code == 201, response.text
