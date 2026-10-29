"""Scope-token semantics: the DB secret / service-env mismatch (original db.py).

Migration 015 makes the row-level policy verify an HMAC scope token against
`memory_server_secrets`. The application computes that token in
`api/services/db.py::_apply_tenant_scope` from `MIND_PALACE_SCOPE_SECRET`
(env, default "dev-scope-secret") on *every* transaction.

If an operator rotates a new secret into the database and enables enforcement
but does not also update `MIND_PALACE_SCOPE_SECRET` in the service
environment, every query returns zero rows: a silent outage. This is the
failure f505f5e tried to address and made worse: with the env flag off it
sends an empty token (still zero rows when enforcement is ON), and a wrong
env secret is no longer detected (when enforcement is OFF).

This test demonstrates the failure with the original `db.py` (always sends an
HMAC token). It proposes no fix; the owner chooses between:

  (a) the env-flag approach (`MIND_PALACE_ENFORCE_SCOPE_TOKEN`): gating the
      token on an app env flag -- see the problems above;
  (b) the database approach: a new migration makes the policy ignore tokens
      when enforcement is off and require a valid one when ON, leaving the
      application unchanged (always sends a token). If (b) is chosen it
      requires a NEW migration, and the parked governance migration 016 would
      need renumbering -- report that, do not renumber anything.

xfail(strict): no fix is implemented on this branch. Remove the marker only
in the PR that implements the chosen approach; the assertion must then pass.
"""

from __future__ import annotations

import hashlib
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tenant_harness import requires_tenant_db, runtime_url

pytestmark = [pytest.mark.asyncio, requires_tenant_db]

_RANDOM_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _corpus_id(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MIND_PALACE_SCOPE_TOKEN_XFAIL: deliberate xfail(strict) until the "
        "owner chooses between env-flag and database approaches for the "
        "scope-token mismatch silent outage; no fix implemented on this "
        "branch."
    ),
)
async def test_scope_token_mismatch_returns_rows_after_rotation():
    """Rotated DB secret + enforcement ON + stale env secret must still read.

    Baseline: after seeding a corpus, a runtime query sees it. After rotating
    a random secret into `memory_server_secrets` and enabling enforcement
    while `MIND_PALACE_SCOPE_SECRET` stays stale, the query must still see it
    -- a correct implementation keeps the app and DB in step. With the
    original `db.py`, the app sends an HMAC computed from the stale env
    secret, `mp_verify_scope_token` rejects it, and the query returns zero
    rows (silent outage).

    The database state (secret + enforcement flag) is restored in the finally
    block so later tests are not contaminated.
    """
    corpus_name = "scope-token-semantics-corpus"
    cid = _corpus_id(corpus_name)
    session_db_url = os.environ["DATABASE_URL"]
    async_url = session_db_url.replace("postgresql://", "postgresql+asyncpg://")
    admin_engine = create_async_engine(async_url, poolclass=None)
    runtime_engine = create_async_engine(runtime_url(), poolclass=None)
    original_secret = "dev-scope-secret"
    try:
        # --- seed a corpus as admin --------------------------------------
        async with admin_engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO corpora (id, name) VALUES (:c, :n) " "ON CONFLICT (id) DO NOTHING"
                ),
                {"c": cid, "n": corpus_name},
            )

        # --- rotate a random secret, enable enforcement ------------------
        async with admin_engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO memory_server_secrets (name, value) VALUES "
                    "('scope_hmac_secret', :v) "
                    "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"v": _RANDOM_SECRET},
            )
            await conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(true)"))

        # --- stale env secret: app still computes HMAC from "dev-scope-secret"
        os.environ["MIND_PALACE_SCOPE_SECRET"] = original_secret

        async with AsyncSession(runtime_engine) as session:
            result = await session.execute(
                sa.text("SELECT count(*) FROM corpora WHERE id = :c"), {"c": cid}
            )
        after = result.scalar()
        assert after >= 1, (
            f"silent outage: expected the seeded corpus to still be readable "
            f"after secret rotation with enforcement ON, got {after} rows"
        )
    finally:
        # --- restore DB state so later tests are not contaminated ---------
        async with admin_engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO memory_server_secrets (name, value) VALUES "
                    "('scope_hmac_secret', :v) "
                    "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"v": original_secret},
            )
            await conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(false)"))
        os.environ.pop("MIND_PALACE_SCOPE_SECRET", None)
        await admin_engine.dispose()
        await runtime_engine.dispose()
