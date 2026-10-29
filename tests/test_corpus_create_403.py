"""Failing test: POST /api/corpora returns 500 instead of 403 when permission
is missing.

The route calls create_corpus which raises AuthorizationError (403), but the
result is a 500 Internal Server Error instead.

This is a pre-existing bug, not a regression from migration 015. Fix in this
branch (m018/corpus-creation-403).
"""

from __future__ import annotations

import pytest

from test_tenant_isolation import world as _shared_world_fixture

world = _shared_world_fixture


@pytest.mark.asyncio
async def test_corpus_create_returns_403_not_500(world, monkeypatch):
    """POST /api/corpora by LOCAL_PRINCIPAL must return 403, not 500.

    In off mode, LOCAL_PRINCIPAL is unrestricted but lacks manage_corpus.
    POST /api/corpora should return 403 Forbidden, not 500 Internal Server
    Error.
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
