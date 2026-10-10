"""The tenant scope is a session setting, so it must not be authority.

Every isolation guarantee in this repository rests on one session variable,
`mindpalace.tenant_scope`, that the application sets per transaction. A session
setting is not a secret: **any role that can open a connection can set it**, and
PostgreSQL provides no way to stop one.

That makes it a load-bearing assumption, and an audit of the unreleased tenant
work found it was load-bearing in the wrong direction. The `{*}` sentinel --
"this principal may reach every corpus" -- was taken at face value, so the
least-privileged runtime role could:

    SET mindpalace.tenant_scope = '{*}';
    SELECT mindpalace_erase_corpus(sha256('corpus:' || 'other-tenant'));

and erase a corpus it held no grant on. Demonstrated, not theorised: the corpus,
its document, claim, evidence row and version were all removed. Reads were equally
forgeable -- the same session setting returned every tenant's rows.

Migration `010` closes the sentinel half. These tests are the proof, and each one
fails against `009`. The residual forge -- a *scoped* value naming a corpus the
role has no grant for -- is not closable without moving authorization into the
database; it is measured here and documented, not hidden.
"""

from __future__ import annotations

import contextlib
import hashlib

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from tenant_harness import apply_as_runtime, requires_tenant_db, runtime_url

pytestmark = [pytest.mark.asyncio, requires_tenant_db]

#: The sentinel meaning "every corpus", as the application writes it.
UNRESTRICTED = "{*}"


def corpus_id_for_name(name: str) -> str:
    """The id the application itself derives, and so can anyone with the name.

    Deliberately computed here rather than looked up: an attacker does not query
    the database for a corpus id, they hash the name, which is why these tests
    pass the id in directly.
    """
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


@pytest.fixture
async def two_tenants():
    """Alpha and Beta, each holding real archive rows, plus their ids.

    Seeded through the maintenance role because that is who can create a corpus --
    the runtime role cannot, and a fixture that could would be testing something
    an application cannot do.
    """
    from api.services import authz
    from api.services.corpora import create_corpus
    from api.services.db import admin_session_scope, principal_scope
    from api.services.remember import remember

    names = ("forge-alpha", "forge-beta")
    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for name in names:
                await create_corpus(db, name, "scope-forgery fixture")
    async with admin_session_scope() as db:
        with principal_scope(authz.LOCAL_PRINCIPAL):
            for name in names:
                await remember(
                    f"FORGE_CANARY_{name.upper()} recorded fact.",
                    corpus=name,
                    key="k",
                )

    yield {name: corpus_id_for_name(name) for name in names}

    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for name in names:
                await db.execute(
                    text("SELECT mindpalace_erase_corpus(:c)"),
                    {"c": corpus_id_for_name(name)},
                )
        await db.commit()


async def test_the_unrestricted_sentinel_is_not_self_assertable(two_tenants):
    """The defect: `{*}` was a value, not a fact about the caller.

    A role that cannot read a single row of another tenant without a credential
    could declare itself unrestricted and get every row.
    """
    count = await _runtime_scalar("SELECT count(*) FROM memory_claims", scope=UNRESTRICTED)
    assert count == 0, (
        "a role that set its own tenant scope to '{*}' read "
        f"{count} rows it holds no grant for; the sentinel is self-assertable"
    )


async def test_erasure_cannot_be_reached_by_forging_the_scope(two_tenants):
    """The destructive half, which is what the audit actually demonstrated.

    Called with a corpus id the attacker derived from a name -- no lookup, no
    privileged query, nothing the role is entitled to.
    """
    beta = two_tenants["forge-beta"]

    with pytest.raises(Exception) as caught:
        await _runtime_call("SELECT mindpalace_erase_corpus(:c)", {"c": beta}, scope=UNRESTRICTED)
    assert "corpus not found" in str(caught.value)

    assert await _survives(beta), "the corpus was erased despite the forged scope"


async def test_a_scoped_value_is_honoured_for_its_own_corpus(two_tenants):
    """The fix must not break the case the scope exists for.

    A genuine scope still reads that corpus and nothing else, and the erasure
    function still works for the scope it was given.
    """
    alpha = two_tenants["forge-alpha"]
    beta = two_tenants["forge-beta"]

    own = await _runtime_scalar("SELECT count(*) FROM memory_claims", scope=alpha)
    other = await _runtime_scalar(
        "SELECT count(*) FROM memory_claims WHERE corpus_id = :c", {"c": beta}, scope=alpha
    )

    assert own >= 1, "a scoped principal must be able to read its own corpus"
    assert other == 0, "a scoped principal read another tenant's rows"


async def test_the_maintenance_role_can_still_use_the_sentinel(two_tenants):
    """Binding the sentinel must remove no capability a trusted role had.

    The local single-user workflow runs on exactly this role; if `{*}` stopped
    working for it, the documented five-minute path would break.
    """
    count = await _maintenance_scalar("SELECT count(*) FROM corpora", scope=UNRESTRICTED)
    assert count >= 1, "the maintenance role must still see every corpus"


async def test_public_holds_no_execute_on_the_security_definer_functions():
    """PostgreSQL grants `EXECUTE` to `PUBLIC` on every new function.

    The provisioning script revoked from the runtime role, which removed nothing:
    any *other* role that could connect could still call the erasure function.
    Revoked from `PUBLIC` as well, and granted to the runtime role by name.
    """
    rows = await _maintenance_rows(
        "SELECT p.proname::text AS name, "
        "       has_function_privilege('public', p.oid, 'EXECUTE') AS allowed "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'mindpalace%'"
    )
    rows = {row[0]: row[1] for row in rows}

    sensitive = {
        "mindpalace_erase_corpus",
        "mindpalace_resolve_credential",
        "mindpalace_locate_credential",
    }
    for name in sensitive:
        assert rows.get(name) is False, f"PUBLIC still holds EXECUTE on {name}"
    # The predicate is the exception, and deliberately so: a row-level policy is
    # evaluated as the querying user, so revoking it would break isolation for
    # every role not granted it by name.
    assert rows.get("mindpalace_allowed_corpus") is True


async def test_the_runtime_role_keeps_execute_on_what_it_needs():
    """The other half: a reduction that breaks authentication is not a reduction."""
    rows = await _runtime_rows(
        "SELECT p.proname::text AS name, "
        "       has_function_privilege(current_user, p.oid, 'EXECUTE') AS allowed "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'mindpalace%'"
    )
    rows = {row[0]: row[1] for row in rows}

    for name in (
        "mindpalace_allowed_corpus",
        "mindpalace_resolve_credential",
        "mindpalace_locate_credential",
        "mindpalace_erase_corpus",
    ):
        assert rows.get(name) is True, f"the runtime role lost EXECUTE on {name}"


async def test_the_residual_forge_is_measured_not_hidden(two_tenants):
    """State the limit precisely, so nobody believes it was fixed when it wasn't.

    A *scoped* session value naming another corpus is still accepted by the
    row-level predicate, because a role may set its own session settings and
    PostgreSQL offers no way to stop it. This is not a new class of access --
    the runtime role already holds `SELECT` on the tenant tables -- but it does
    widen the blast radius of an already-compromised application process, and it
    is the reason the trust model says the application process is inside the
    boundary.

    Pinned rather than merely documented: if this ever changes in either
    direction, this test says so.
    """
    beta = two_tenants["forge-beta"]
    leaked = await _runtime_scalar("SELECT count(*) FROM memory_claims", scope=beta)
    assert leaked >= 1, (
        "a forged *scoped* value is no longer accepted by the predicate; if this "
        "was fixed, update docs/security.md and the trust model rather than "
        "leaving the claim that it is not contained"
    )


async def _runtime_scalar(statement: str, params: dict | None = None, *, scope: str):
    return await _scalar(statement, params, scope=scope, role="runtime")


async def _runtime_rows(statement: str):
    async with _session("runtime") as conn:
        result = await conn.execute(sa.text(statement))
        return result.fetchall()


async def _maintenance_rows(statement: str):
    async with _session("maintenance") as db:
        result = await db.execute(sa.text(statement))
        return result.fetchall()


async def _maintenance_scalar(statement: str, params: dict | None = None, *, scope: str):
    return await _scalar(statement, params, scope=scope, role="maintenance")


async def _survives(corpus_id: str) -> bool:
    return bool(
        await _maintenance_scalar(
            "SELECT EXISTS (SELECT 1 FROM corpora WHERE id = :c)",
            {"c": corpus_id},
            scope=UNRESTRICTED,
        )
    )


async def _runtime_call(statement: str, params: dict | None = None, *, scope: str):
    return await _call(statement, params, scope=scope, role="runtime")


async def _scalar(statement: str, params: dict | None, *, scope: str, role: str):
    async with _session(role) as conn:
        await conn.execute(  # noqa: E501
            sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
            {"s": scope},
        )
        result = await conn.execute(sa.text(statement), params or {})
        return result.scalar()


async def _call(statement: str, params: dict | None, *, scope: str, role: str):
    async with _session(role) as conn:
        await conn.execute(
            sa.text("SELECT set_config('mindpalace.tenant_scope', :s, false)"),
            {"s": scope},
        )
        return await conn.execute(sa.text(statement), params or {})


async def _scalar_rows(statement: str, params: dict | None, *, scope: str, role: str):
    async with _session(role) as conn:
        await conn.execute(
            sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
            {"s": scope},
        )
        result = await conn.execute(sa.text(statement), params or {})
        return result.fetchall()


@contextlib.asynccontextmanager
async def _session(role: str):
    """One connection on the named role, with a real pool.

    The runtime role is the one whose isolation properties are being asserted, so
    it is the role these queries run as -- not a substitute that happens to be
    easier to set up.
    """
    if role == "runtime":
        engine = apply_as_runtime(runtime_url())
    else:
        from api.services.db import admin_session_scope

        async with admin_session_scope() as db:
            yield db
            return
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()
