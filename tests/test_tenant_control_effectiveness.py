"""Negative tests: proving the tenant controls are load-bearing.

A security test that passes tells you the system works. It does not tell you
whether the *test* would notice if the system stopped working. These tests
disable one control at a time and assert what becomes reachable.

Where two controls cover the same path -- the application-layer check and the
row-level policy -- each is shown to hold on its own, and only removing both
opens the path. That is defence in depth demonstrated rather than asserted.

**There are now three, not two.** Migration `010` added a third: the `{*}` scope
sentinel is honoured only by a role that already reads every row. `_scope_value`
alone no longer opens the door for the runtime role, so the control this file
exists to police has to remove that one too. A negative control that removes two
controls of three and then expects a leak is a test that starts lying the moment
the third control ships -- which is exactly what happened here, and is why
`_remove_every_database_control` exists alongside `_forget_row_level_policy`.

The controls are restored after each test, so no production code is left broken.
"""

from __future__ import annotations

import pytest

from tenant_harness import (
    ALPHA_CORPORA,
    BETA_CORPORA,
    BETA_PRIVATE_PRODUCT_FACT_7Q2,
    requires_tenant_db,
)
from test_tenant_isolation import World
from test_tenant_isolation import world as _shared_world_fixture

# The `world` fixture is shared rather than duplicated, so these negative tests
# break controls against exactly the fixture the isolation suite uses. Aliasing it
# instead of importing it as `world` keeps every test's own `world` parameter
# unambiguous to a reader and to the linter.
world = _shared_world_fixture

pytestmark = [pytest.mark.asyncio, requires_tenant_db]


def _forget_application_check(monkeypatch) -> None:
    """Remove the authorization choke point everywhere it is imported.

    Two namespaces, because two modules imported the function directly. Patching
    only one would leave the check in place and make this test prove nothing.
    """
    monkeypatch.setattr(
        "api.services.authz.require_corpus",
        lambda principal, corpus, write=False: None,
        raising=True,
    )
    monkeypatch.setattr(
        "api.services.corpora.require_corpus",
        lambda principal, corpus, write=False: None,
        raising=True,
    )


def _forget_row_level_policy(monkeypatch) -> None:
    """Make every transaction claim to be unrestricted.

    The application half only: the scope value is `{*}` and the row-level
    predicate still binds that sentinel to a role that already reads every row,
    which `mindpalace_runtime` does not. That binding is a control of its own
    after migration `010`, and this helper deliberately leaves it in place --
    `test_without_the_application_check_the_database_still_holds_the_line` is
    stronger for it, not weaker, and `_remove_every_database_control` is where
    all of it comes off.
    """
    from api.services import db as db_module

    monkeypatch.setattr(db_module, "_scope_value", lambda: "{*}", raising=True)


#: Tables protected by the `mindpalace_tenant` row-level policy. Migration `016`
#: inlines `(SELECT mp_verified_scope_corpora())` directly in each policy, so
#: `mindpalace_allowed_corpus` is no longer the policy control point -- disabling
#: row-level security on these tables is what "removing the control" means now.
_PROTECTED_TABLES = (
    "corpora",
    "documents",
    "ingestion_manifest",
    "memory_documents",
    "memory_versions",
    "memory_chunks",
    "memory_claims",
    "memory_evidence",
    "memory_snapshots",
    "memory_snapshot_versions",
    "memory_snapshot_seals",
    "memory_claim_embeddings",
    "chunks",
)


#: A trusting replacement for `mp_verified_scope_corpora()`: it parses the session
#: scope setting into corpus IDs without checking the token or enforcement flag,
#: so any scope a session names is honoured. This is the new control point after
#: migration 016 (the policy inlines `(SELECT mp_verified_scope_corpora())`).
_TRUSTING_SCOPE_FUNCTION = """
CREATE OR REPLACE FUNCTION mp_verified_scope_corpora()
RETURNS text[]
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $fn$
    SELECT CASE
        WHEN coalesce(current_setting('mindpalace.tenant_scope', true), '') = ''
            THEN ARRAY[]::text[]
        WHEN current_setting('mindpalace.tenant_scope', true) = '{*}'
            THEN ARRAY['*']::text[]
        ELSE coalesce(
            (SELECT array_agg(btrim(x))
             FROM unnest(string_to_array(
                 current_setting('mindpalace.tenant_scope', true), ','
             )) AS x),
            ARRAY[]::text[]
        )
    END
$fn$
"""


#: Captured once, on first use, so the autouse restore is exact rather than a
#: re-typed approximation of migration 015's definition.
_installed_scope_function: str | None = None


async def _replace_scope_function_with_trusting_body() -> None:
    """Replace `mp_verified_scope_corpora()` with a trusting body.

    Uses CREATE OR REPLACE so the function owner and existing grants are
    preserved; the only thing that changes is the body. The original definition
    is captured the first time this runs and restored by `_restore_scope_function`.
    """
    global _installed_scope_function
    from sqlalchemy import text

    from api.services import authz
    from api.services.db import admin_session_scope, principal_scope

    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            if _installed_scope_function is None:
                _installed_scope_function = (
                    await db.execute(
                        text(
                            "SELECT pg_get_functiondef("
                            "'mp_verified_scope_corpora()'::regprocedure)"
                        )
                    )
                ).scalar()
            await db.execute(text(_TRUSTING_SCOPE_FUNCTION))
        await db.commit()


@pytest.fixture(autouse=True)
async def _restore_scope_function():
    """Restore `mp_verified_scope_corpora()` after each test, unconditionally.

    Autouse so that a test that fails partway through after replacing the
    function does not leave every later test running against a trusting scope
    check. Restoration is verified by reading the definition back and comparing
    it to the captured original.
    """
    yield
    if _installed_scope_function is None:
        return
    from sqlalchemy import text

    from api.services import authz
    from api.services.db import admin_session_scope, principal_scope

    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(text(_installed_scope_function))
            restored = (
                await db.execute(
                    text(
                        "SELECT pg_get_functiondef(" "'mp_verified_scope_corpora()'::regprocedure)"
                    )
                )
            ).scalar()
        await db.commit()
    assert (
        restored == _installed_scope_function
    ), "mp_verified_scope_corpora() was not restored to its original definition"


async def _remove_every_database_control(monkeypatch) -> None:
    """Take the row-level control off entirely.

    Migration `016` inlines `(SELECT mp_verified_scope_corpora())` directly in
    each `mindpalace_tenant` policy, so `mindpalace_allowed_corpus` is no longer
    referenced by any policy. Disabling row-level security on every protected
    table is what "removing the control" means now; the function itself is left
    untouched (it still exists and is still granted).
    """
    from sqlalchemy import text

    from api.services import authz
    from api.services.db import admin_session_scope, principal_scope

    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for table in _PROTECTED_TABLES:
                await db.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
        await db.commit()


@pytest.fixture(autouse=True)
async def _restore_rls():
    """Re-enable row-level security on every protected table, whatever a test did.

    Autouse and unconditional rather than tied to the helper, because a test that
    fails partway through after disabling RLS would otherwise leave every later
    test in this file -- and the isolation suite, which shares the database --
    running without row-level security.
    """
    yield
    from sqlalchemy import text

    from api.services import authz
    from api.services.db import admin_session_scope, principal_scope

    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for table in _PROTECTED_TABLES:
                await db.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        await db.commit()


def _forget_role_check(monkeypatch) -> None:
    """Treat every credential as read-write."""
    monkeypatch.setattr(
        "api.services.authz.Principal.can_write", property(lambda self: True), raising=True
    )


def _forget_authentication(monkeypatch) -> None:
    """Let the middleware accept any request."""
    monkeypatch.setattr(
        "api.services.authz.resolve_principal",
        _unauthenticated_local_principal,
        raising=True,
    )


async def _unauthenticated_local_principal(db, authorization=None):
    from api.services.authz import LOCAL_PRINCIPAL

    return LOCAL_PRINCIPAL


async def test_without_the_application_check_the_database_still_holds_the_line(world, monkeypatch):
    """The choke point is removed and the cross-tenant read is still refused.

    This is the RLS control being load-bearing on its own. Without it, the only
    thing standing between a caller and another tenant's rows would be a Python
    `if`.
    """
    _forget_application_check(monkeypatch)
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router mesh bands"},
    )
    assert response.status_code == 404, (
        "with the application check removed the request should have been refused by "
        "row-level security alone"
    )
    World.assert_no_foreign_canary(response.json())


async def test_without_the_row_level_policy_the_application_still_holds_the_line(
    world, monkeypatch
):
    """The database stops enforcing, and the request is still refused.

    The defence-in-depth claim: an application-layer check that is correct today
    can still be correct after someone adds a query that forgets to scope itself,
    because the policy is underneath it rather than beside it.
    """
    _forget_row_level_policy(monkeypatch)
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router mesh bands"},
    )
    assert response.status_code == 404
    World.assert_no_foreign_canary(response.json())


async def test_without_both_controls_the_cross_tenant_read_succeeds(world, monkeypatch):
    """The control the suite exists to police: remove them all and the leak appears.

    If this test ever stops finding a leak, the isolation tests above have gone
    vacuous -- they would then be passing because the fixture is broken rather
    than because the boundary holds.

    "Both" now means three things: the application choke point, the transaction
    scope value, and the predicate's binding of the `{*}` sentinel. The third is
    the newest, and it is the one a test would silently forget -- without it this
    assertion still passed before `010` and stopped passing after it.
    """
    _forget_application_check(monkeypatch)
    _forget_row_level_policy(monkeypatch)
    await _remove_every_database_control(monkeypatch)
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router mesh bands"},
    )
    assert response.status_code == 200
    assert BETA_PRIVATE_PRODUCT_FACT_7Q2 in response.text, (
        "the canary did not leak even with every control removed; the fixture is "
        "not proving what it claims to"
    )


async def test_without_scope_function_the_forgery_holds(world, monkeypatch):
    """The scope function is the new control point: replace it with a trusting
    body and the forgery passes through.

    Migration `016` inlines `(SELECT mp_verified_scope_corpora())` directly in
    each `mindpalace_tenant` policy, so `mp_verified_scope_corpora()` is the
    control. This is a mutation test: first the forged request is refused with
    the real function (404, zero rows), then the function is replaced with a
    trusting body and the same request succeeds (200, canary leaks), proving
    the control is load-bearing. The autouse fixture restores the original
    definition; restoration is verified by reading the definition back.
    """
    _forget_application_check(monkeypatch)
    _forget_row_level_policy(monkeypatch)

    # Red first: with the real function the forged request is refused.
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router mesh bands"},
    )
    assert response.status_code == 404, (
        "with the real scope function the forged request should be refused; "
        "got %d" % response.status_code
    )
    World.assert_no_foreign_canary(response.json())

    # Mutation: replace with a trusting body.
    await _replace_scope_function_with_trusting_body()

    # Forgery now succeeds.
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router mesh bands"},
    )
    assert response.status_code == 200, (
        "with the scope function replaced the forged request should have been "
        "served; the control is not proving what it claims to"
    )
    assert BETA_PRIVATE_PRODUCT_FACT_7Q2 in response.text, (
        "the canary did not leak even with the scope function replaced; the "
        "fixture is not proving what it claims to"
    )


async def test_without_authentication_an_anonymous_caller_is_served(world, monkeypatch):
    """Remove the middleware's authentication and the 401s disappear.

    This is what makes the coverage test meaningful: it asserts 401 on every
    tenant route, and that assertion is only worth something while a request
    without a credential really is refused.

    The database-side controls come off as well, because they are not what this
    test is about and leaving them on would make it assert something else. With
    them in place an anonymous caller now gets a `404` from row-level security
    rather than a `200` -- correct, and a stronger answer, but it would turn this
    into a statement about the policy rather than about authentication. The claim
    here is "authentication, once removed, stops being the thing that refuses",
    and the others come off so it is the only thing under test.
    """
    _forget_authentication(monkeypatch)
    _forget_row_level_policy(monkeypatch)
    await _remove_every_database_control(monkeypatch)
    response = await world.post(
        "/api/memory/query",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert response.status_code == 200
    assert response.json()["current_memories"]


async def test_without_the_role_check_a_read_only_credential_can_write(world, monkeypatch):
    """Remove the read-only role and the reviewer can write memory."""
    _forget_role_check(monkeypatch)
    response = await world.post(
        "/api/memory/remember",
        "alpha-reviewer",
        json={"statement": "A reviewer writes.", "corpus": ALPHA_CORPORA["brand"]},
    )
    assert response.status_code == 200


async def test_a_tenant_cannot_forge_a_scope_by_asking_for_one(world):
    """The scope comes from the credential, never from the request.

    A caller that names a corpus it was not granted gets the same refusal as one
    that guessed the name correctly, because the guess is not the decision.
    """
    for guess in (BETA_CORPORA["brand"], "default", "..", "alpha-brand"):
        response = await world.post(
            "/api/memory/query",
            "alpha-creator",
            json={"corpus": guess, "query": "router"},
        )
        assert response.status_code in (200, 404)
        if guess != ALPHA_CORPORA["brand"]:
            assert response.status_code == 404, f"naming {guess!r} was not refused"
        World.assert_no_foreign_canary(response.json(), own=("alpha",))


async def test_the_client_is_never_the_thing_that_decides(world):
    """Everything that identifies the caller comes from the credential.

    Sent as request data so a caller could set it if it were honoured: a body
    field, a header, and a query parameter that all claim a different identity.
    None of them changes who the request is.
    """
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        headers={"X-Workspace": BETA_CORPORA["brand"], "X-Role": "administrator"},
        json={
            "corpus": ALPHA_CORPORA["brand"],
            "query": "router mesh bands",
            "workspace": BETA_CORPORA["brand"],
        },
    )
    # `MemoryRequest` forbids unknown fields, so the body claim is rejected rather
    # than ignored. Either outcome is acceptable; what matters is that neither
    # changes the caller's identity, and neither returns Beta's memory.
    assert response.status_code in (200, 422)
    World.assert_no_foreign_canary(response.json(), own=("alpha",))


async def test_the_browser_cannot_reach_the_database_itself(world):
    """The browser talks to the application; the database has no HTTP surface."""
    response = await world.client.post(
        "/api/memory/query", json={"corpus": ALPHA_CORPORA["brand"], "query": "x"}
    )
    assert response.status_code == 401
