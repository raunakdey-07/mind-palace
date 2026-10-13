"""The surfaces a shared deployment can bypass the boundary through.

The REST surface is covered by `test_tenant_isolation.py`. This file covers the
others, because each is a separate process or a separate code path and a
boundary that holds on one of them proves nothing about the others:

* the **local runtime socket**, which is the default transport for the CLI and
  for `MindPalace(runtime=True)`;
* **SDK clients constructed without a corpus name**, which is exactly the shape
  the CLI builds;
* **MCP**, which constructs its own clients;
* **read-only credentials**, on the write operations they were not meant to reach;
* **deployment configuration**, where an unauthenticated service is the default.

Every bypass here was a real defect found by audit rather than a hypothetical, and
each test states the defect it pins.
"""

from __future__ import annotations

from pathlib import Path

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tenant_harness import (
    ALPHA_CORPORA,
    BETA_CORPORA,
    BETA_PRIVATE_PRODUCT_FACT_7Q2,
    RUNTIME_ROLE,
    apply_as_runtime,
    requires_tenant_db,
    runtime_url,
    sync_url,
)
from test_tenant_isolation import World, corpus_id_for
from test_tenant_isolation import world as _shared_world_fixture

# The `world` fixture is shared rather than duplicated, so these tests run against
# exactly the two organisations the isolation suite uses. Aliasing it instead of
# importing it as `world` keeps every test's own `world` parameter unambiguous to a
# reader and to the linter.
world = _shared_world_fixture

pytestmark = [pytest.mark.asyncio, requires_tenant_db]


# ---------------------------------------------------------------------------
# The runtime socket was a complete tenant bypass
# ---------------------------------------------------------------------------


async def test_the_runtime_socket_has_no_authorization_concept(world, monkeypatch):
    """The defect: `api/runtime.py` never mentioned a principal or a corpus check.

    Its only check was the socket token, which proves the caller is a local peer
    of this user and decides nothing about which corpora they may reach. Asserted
    on the source because the absence is the defect, and a behavioural test for
    "this code has no branch" is not really possible.
    """
    from pathlib import Path

    import api.runtime as runtime_module

    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    # The socket resolves a principal and scopes every dispatch with it.
    assert "principal_scope" in source, (
        "api/runtime.py no longer scopes its dispatches; if it was removed on "
        "purpose, this test should be replaced by one for the replacement"
    )
    assert "_runtime_principal" in source
    # ... and the corpus name still arrives from the wire, which is exactly why
    # the principal has to be checked against it.
    assert 'request.get("corpus")' in source
    assert "MIND_PALACE_RUNTIME_CREDENTIAL" in source


async def test_the_runtime_refuses_every_corpus_without_a_credential(world, monkeypatch):
    """In `required` mode a runtime with no credential serves nothing.

    The runtime resolves its own principal per dispatch, so a revoked credential
    stops working without a restart and a missing one cannot select a corpus by
    name. Asserted against the resolver the socket calls, because driving a real
    socket needs a started runtime and the defect is in the dispatch path.
    """
    from api.services import authz
    from api.runtime import _runtime_principal

    monkeypatch.delenv("MIND_PALACE_RUNTIME_CREDENTIAL", raising=False)
    with pytest.raises(authz.AuthorizationError):
        await _runtime_principal()

    monkeypatch.setenv(
        "MIND_PALACE_RUNTIME_CREDENTIAL", world.credentials["alpha-creator"]["secret"]
    )
    principal = await _runtime_principal()
    assert principal.unrestricted is False
    assert not (principal.corpora & set(BETA_CORPORA.values()))


async def test_a_runtime_credential_cannot_read_another_organisation(world, monkeypatch):
    """The socket honours the credential's scope, not the corpus name on the wire.

    This is the check the socket did not have. `_dispatch` reads a corpus from
    the request; the principal must still refuse it, and the database must
    refuse it again underneath.
    """
    from api.runtime import _dispatch, _Runtime

    monkeypatch.setenv(
        "MIND_PALACE_RUNTIME_CREDENTIAL", world.credentials["alpha-creator"]["secret"]
    )
    monkeypatch.setattr("api.services.db.AsyncSessionLocal", world.db_module.AsyncSessionLocal)

    from api.services import authz

    with pytest.raises(authz.AuthorizationError):
        await _dispatch(
            "query",
            {"request": {"corpus": BETA_CORPORA["brand"], "query": "router"}},
            _Runtime(),
        )


# ---------------------------------------------------------------------------
# An SDK client with no corpus name authenticated in no mode
# ---------------------------------------------------------------------------


def run_sdk(action):
    """Run a synchronous SDK call the way an application runs it.

    Two constraints force this rather than a plain call. The SDK refuses to run
    inside a running event loop, by design, so an async test cannot call it
    directly. And the tenant fixture has already swapped `AsyncSessionLocal` for
    an engine bound to *this* test's loop, so reusing it from the loop the SDK
    creates would fail on the connection pool rather than on anything under test.

    So the SDK is given what it would have in a real application: a thread with no
    loop of its own, and the process's own session factory rather than the
    fixture's. The `principal_scope` context manager is a `ContextVar`, entered and
    left inside that thread, which is exactly how the SDK uses it in production --
    per operation, never inherited across a thread boundary.
    """
    import asyncio
    import concurrent.futures

    from api.services import db as db_module
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tenant_harness import apply_as_runtime, runtime_url

    fixture_factory = db_module.AsyncSessionLocal
    fixture_engine = db_module.async_engine

    def in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # A fresh engine for this thread only. Built on the least-privileged role,
        # so the SDK reaches the database the same way a deployment would, and
        # never from a pool whose connections belong to the test's own loop.
        #
        # Both names are redirected, not just the session factory: `MindPalace._run`
        # disposes `async_engine` after every call, because asyncpg binds a
        # connection to the loop that opened it. Leaving it pointed at the
        # fixture's engine would dispose the wrong pool and leave this one holding
        # a connection from a loop that no longer exists.
        engine = apply_as_runtime(runtime_url())
        db_module.async_engine = engine
        db_module.AsyncSessionLocal = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            return action()
        finally:
            db_module.AsyncSessionLocal = fixture_factory
            db_module.async_engine = fixture_engine
            loop.run_until_complete(engine.dispose())
            asyncio.set_event_loop(None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(in_thread).result()


async def test_an_unnamed_local_client_cannot_be_constructed_in_required_mode(world, monkeypatch):
    """The defect: `_local_principal` ran only when a corpus name was supplied.

    `MindPalace()` without a name is the shape the CLI builds, so it skipped
    authentication entirely: its operations ran as the default local principal,
    unrestricted, with a `{*}` database scope, even under
    `MIND_PALACE_AUTH_MODE=required`.
    """
    from mindpalace_sdk import MemoryClientError, MindPalace

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    with pytest.raises(MemoryClientError) as caught:
        MindPalace()
    assert caught.value.status_code == 401


async def test_a_named_local_client_needs_a_credential_in_required_mode(world, monkeypatch):
    """The scope is installed for a named client before any operation runs.

    `create_if_missing=False` keeps the client from touching the database, so
    this asserts the constructor and not the corpus bootstrap.
    """
    from mindpalace_sdk import MemoryClientError, MindPalace

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")

    def build(api_key=None):
        return MindPalace(ALPHA_CORPORA["brand"], api_key=api_key, create_if_missing=False)

    with pytest.raises(MemoryClientError):
        run_sdk(build)
    client = run_sdk(lambda: build(world.credentials["alpha-creator"]["secret"]))
    assert client._principal is not None
    assert client._principal.unrestricted is False


async def test_a_local_client_cannot_read_a_corpus_it_was_not_granted(world, monkeypatch):
    """End to end through the SDK, which is how an integrator reaches memory."""
    from mindpalace_sdk import MemoryClientError, MindPalace

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    client = run_sdk(
        lambda: MindPalace(
            ALPHA_CORPORA["brand"],
            api_key=world.credentials["alpha-creator"]["secret"],
            create_if_missing=False,
        )
    )
    with pytest.raises(MemoryClientError) as caught:
        run_sdk(lambda: client.recall("mesh bands", corpus=BETA_CORPORA["brand"]))
    assert caught.value.status_code == 404


async def test_the_runtime_shortcut_runs_inside_the_client_scope(world, monkeypatch):
    """The defect: `_through_runtime` was called before `_as_caller()`.

    The socket is a process boundary, so a `ContextVar` cannot cross it. Taking
    the shortcut outside the scope would mean the in-process path was authorized
    and the socket path was not -- the one distinction that must not exist.
    """
    import inspect

    from mindpalace_sdk import MemoryClient

    source = inspect.getsource(MemoryClient._execute)
    runtime_call = source.index("_through_runtime")
    scope_enter = source.index("_as_caller")
    assert (
        scope_enter < runtime_call
    ), "the runtime shortcut must be taken inside the caller's scope, not before it"


# ---------------------------------------------------------------------------
# MCP: the bare-secret bug, and clients that never got a credential
# ---------------------------------------------------------------------------


async def test_mcp_resolves_its_credential_as_a_bearer_header(world, monkeypatch):
    """The defect: the bare secret was passed where a header value was required.

    `resolve_principal` takes the value an `Authorization` header carries, so
    passing the secret alone failed the scheme check and every MCP tool answered
    401 -- including with a correctly configured credential.
    """
    import mcp_server

    monkeypatch.setenv(
        "MIND_PALACE_RUNTIME_CREDENTIAL", world.credentials["alpha-creator"]["secret"]
    )
    monkeypatch.setenv("MIND_PALACE_MCP_CREDENTIAL", world.credentials["alpha-creator"]["secret"])
    monkeypatch.setattr("api.services.db.AsyncSessionLocal", world.db_module.AsyncSessionLocal)

    principal = await mcp_server._resolved_principal()
    assert principal.unrestricted is False
    assert BETA_CORPORA["brand"] not in principal.corpora


async def test_mcp_refuses_every_tool_without_a_credential_in_required_mode(world, monkeypatch):
    import mcp_server
    from api.services import authz

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.delenv("MIND_PALACE_MCP_CREDENTIAL", raising=False)
    monkeypatch.setattr("api.services.db.AsyncSessionLocal", world.db_module.AsyncSessionLocal)
    with pytest.raises(authz.AuthorizationError):
        await mcp_server._resolved_principal()


async def test_mcp_hands_its_credential_to_the_clients_it_builds(world, monkeypatch):
    """The defect: `_client()` built `MindPalace(corpus)` with no credential.

    `context`, `search` and `sync` go through that client. It authenticated in
    no mode, so those three tools bypassed the very scope the tool had just
    established -- while the eleven `memory_*` tools, which do not build a client,
    were correctly scoped. Asserted on the constructed client, which is where the
    credential has to arrive.
    """
    import mcp_server

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.setenv("MIND_PALACE_MCP_CREDENTIAL", world.credentials["alpha-creator"]["secret"])
    mcp_server._clients.clear()
    try:
        client = run_sdk(lambda: mcp_server._client(ALPHA_CORPORA["brand"]))
        assert (
            client._principal is not None
        ), "the MCP client was built without the configured credential"
        assert client._principal.unrestricted is False
    finally:
        mcp_server._clients.clear()


# ---------------------------------------------------------------------------
# Read-only credentials on write operations
# ---------------------------------------------------------------------------


async def test_a_read_only_credential_cannot_sync_its_own_corpus(world, tmp_path):
    """The defect: `sync` resolved its corpus with `write=False`.

    A read-only credential could therefore perform a complete document and
    archive write into a corpus it *was* granted -- the cross-tenant test passed
    because the corpus check fired first, and nothing covered the role.
    """
    (tmp_path / "doc.md").write_text("# Title\n\nContent for the sync test.\n")
    response = await world.post(
        f"/api/corpora/{ALPHA_CORPORA['brand']}/sync",
        "alpha-reviewer",
        json={"path": str(tmp_path)},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


async def test_a_read_only_credential_cannot_reindex(world, tmp_path):
    """`mindpalace reindex` wrote documents and archive rows with no scope at all.

    It reached the authorization choke point, but against the *default* local
    principal, which is unrestricted in every mode -- so in `required` mode an
    operator could rewrite any corpus named on the command line. What the CLI
    does is rehydrate unconditionally; what it must do first is resolve the
    corpus with the role the reindex needs.
    """
    from api.services import authz
    from api.services.authz import Principal
    from api.services.corpora import get_corpus_by_name
    from api.services.rehydrate import rehydrate_corpus

    async with world.db_module.admin_session_scope() as db:
        # What the CLI does today: an administrative write with no role check.
        with world.db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            row = await get_corpus_by_name(db, ALPHA_CORPORA["brand"])
            await rehydrate_corpus(db, row["id"])

        # What it must do: resolve with the write role first, under the reader.
        reader = Principal(
            id="test",
            name="test",
            role="read_only",
            corpora=frozenset({ALPHA_CORPORA["brand"]}),
            corpus_ids=frozenset({corpus_id_for(ALPHA_CORPORA["brand"])}),
        )
        with world.db_module.principal_scope(reader):
            assert await get_corpus_by_name(db, ALPHA_CORPORA["brand"]) is not None
            with pytest.raises(authz.AuthorizationError):
                await get_corpus_by_name(db, ALPHA_CORPORA["brand"], write=True)


# ---------------------------------------------------------------------------
# Deployment configuration
# ---------------------------------------------------------------------------


def test_a_server_deployment_refuses_to_start_unauthenticated(monkeypatch):
    """The defect: nothing anywhere refused this.

    `MIND_PALACE_DEPLOYMENT=server` with `MIND_PALACE_AUTH_MODE=off` is an
    unauthenticated service on a network, and it is what a bare `uvicorn
    api.main:app` or `docker compose up` produces. There was no guard at all, so
    the safe-looking default was the unsafe one.

    `MIND_PALACE_INGEST_ROOTS` is set so this isolates the authentication
    question; the directory guard is tested on its own below.
    """
    from api.services import authz

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")

    problems = authz.server_mode_errors()
    assert problems, "a server deployment with authentication off must be refused"
    assert any("AUTH_MODE" in problem for problem in problems), problems
    with pytest.raises(RuntimeError, match="refusing to start"):
        authz.assert_server_configuration()

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    assert authz.server_mode_errors() == []
    authz.assert_server_configuration()


def test_a_local_deployment_is_never_refused(monkeypatch):
    """The point of the guard is the server case; local use must not regress."""
    from api.services import authz

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "local")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.delenv("MIND_PALACE_INGEST_ROOTS", raising=False)
    assert authz.server_mode_errors() == []
    authz.assert_server_configuration()


def test_the_app_refuses_to_build_as_an_unauthenticated_server(monkeypatch):
    """End of the chain: the refusal reaches the application factory."""
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    import api.main as main_module

    with pytest.raises(RuntimeError, match="refusing to start"):
        main_module.create_app()


# ---------------------------------------------------------------------------
# Readiness reports whether the database is actually enforcing the policies
# ---------------------------------------------------------------------------


async def test_readiness_reports_that_a_superuser_bypasses_the_policies(monkeypatch):
    """The defect: readiness said `auth.required: true` whatever the role was.

    A deployment could report itself ready and tenant-scoped while connecting as
    a superuser, with migration 008 completely inert. That is precisely the
    configuration the docs warn about and the one an operator checks readiness
    for.

    Deliberately does not take the `world` fixture: that fixture switches the
    application onto the restricted role, which is the *other* case and is
    covered below. Without it the module's own session is the migration
    superuser this suite provisions with.

    `server` is set explicitly because the verdict is profile-dependent. In
    `local` there is no second tenant to isolate from, and answering
    `not_ready` to a developer running the documented single-user setup would
    break the convenient path for a problem that cannot occur there.
    """
    from api.main import app

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    body = response.json()

    # A verdict, not an inventory. The endpoint is read by probes and anything
    # else that can reach the port, so it says "not enforcing" and stops; the
    # role name, the `BYPASSRLS` flag and the table list are what
    # `mindpalace posture` prints for an operator who already holds credentials.
    assert (
        response.status_code == 503
    ), "readiness must not report ready on a role that bypasses the policies"
    assert body["status"] == "not_ready"
    assert body["checks"]["row_level_security"] == "not_enforced"
    assert "database_posture" not in body
    # ... and the inventory is genuinely gone, not renamed.
    #
    # Both names are checked, not one. `mindpalace_runtime` is the shipped role
    # and `RUNTIME_ROLE` is the per-session role this session actually connected
    # as, so a leak of either would be a real leak. Asserting only the shipped
    # literal would have passed while the live role name went out in the clear.
    for role_name in ("mindpalace_runtime", RUNTIME_ROLE):
        assert role_name not in response.text, f"{role_name} leaked in the body"
    assert "bypassrls" not in response.text.lower()


async def test_readiness_still_answers_in_local_mode(world, monkeypatch):
    """The profile-dependence, asserted so it cannot be tightened by accident.

    Removing the posture verdict from readiness entirely would make the test
    above pass for the wrong reason, and would take the convenient local path
    with it.
    """
    from api.main import app

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "local")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    body = response.json()

    assert response.status_code == 200, (
        "a local single-user deployment must keep answering ready; there is no "
        "second tenant for the posture to protect"
    )
    assert body["checks"]["row_level_security"] == "enforced"


async def test_readiness_reports_a_restricted_role_as_effective(world):
    """The same endpoint, on the role a tenant-scoped deployment actually uses.

    The `world` fixture puts the application on `mindpalace_runtime`, so this
    asserts what an operator in the intended configuration sees: the policies are
    in force. Two roles, one endpoint, two different honest answers.
    """
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get("/health/ready")).json()

    assert body["status"] == "ready"
    assert body["auth"]["required"] is True
    assert body["checks"]["row_level_security"] == "enforced"
    # The detail is the CLI's job, not an anonymous response's.
    assert "database_posture" not in body
    for role_name in ("mindpalace_runtime", RUNTIME_ROLE):
        assert role_name not in json.dumps(body), f"{role_name} leaked in the body"


async def test_the_role_check_agrees_with_the_role_provision_script():
    """The inspector and the provisioning script must describe the same role.

    If they drift, an operator gets a clean `posture` report for a role that does
    not exist in the shape the script creates, or the reverse: a healthy role
    reported as broken.
    """
    from api.services.posture import inspect

    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            posture = await inspect(db)
    finally:
        await engine.dispose()

    # The role the harness provisioned, not the shipped name. The test is about
    # what this role can do, so the name it asserts has to be the name it used.
    assert posture.role == RUNTIME_ROLE
    assert posture.is_superuser is False
    assert posture.bypasses_rls is False
    assert posture.rls_effective is True
    assert posture.unprotected_tables == []
    assert posture.problems() == []

    # The script must create that role as NOLOGIN-with-BYPASSRLS owner plus a
    # LOGIN NOSUPERUSER NOBYPASSRLS runtime role; a role the script would not
    # create is not the role the check describes.
    script = Path("scripts/db/runtime_roles.sql").read_text(encoding="utf-8")
    assert "CREATE ROLE mindpalace_runtime LOGIN NOSUPERUSER NOBYPASSRLS" in script
    assert "CREATE ROLE mindpalace_owner NOLOGIN BYPASSRLS" in script


async def test_posture_reports_a_policy_that_is_not_forced(world):
    """A table with RLS enabled but not forced is a real deployment error.

    Without `FORCE`, the table owner silently bypasses its own policy, which is
    the shape a migration that forgets the second flag produces.
    """
    import sqlalchemy as sa

    from api.services.posture import inspect

    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE memory_claims NO FORCE ROW LEVEL SECURITY"))
    finally:
        engine.dispose()
    try:
        from api.services.db import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            posture = await inspect(db)
        assert "memory_claims" in posture.unprotected_tables
        assert any("memory_claims" in problem for problem in posture.problems())
    finally:
        engine = sa.create_engine(sync_url())
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("ALTER TABLE memory_claims FORCE ROW LEVEL SECURITY"))
        finally:
            engine.dispose()


async def test_readiness_says_the_schema_is_missing_without_leaking(world, monkeypatch):
    """The existing behaviour this must not regress into.

    The posture check runs after the schema check, so a session that reports a
    missing table must still produce a readiness answer rather than a traceback.
    """
    from api.main import app

    class MissingSchemaSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, _query):
            return type("Result", (), {"one": lambda self: (False, False, False)})()

    monkeypatch.setattr("api.services.db.session_scope", lambda: MissingSchemaSession())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "traceback" not in response.text.lower()


# ---------------------------------------------------------------------------
# No cross-tenant read through the CLI-shaped surface
# ---------------------------------------------------------------------------


async def test_a_local_client_in_required_mode_cannot_read_another_tenant(world, monkeypatch):
    """The end-to-end version of the bypass, and the reason it mattered.

    Without the fix, `MindPalace()` -- the CLI's own construction -- reached every
    corpus in the database under `MIND_PALACE_AUTH_MODE=required`.
    """
    from mindpalace_sdk import MemoryClientError, MindPalace

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    with pytest.raises(MemoryClientError) as caught:
        run_sdk(lambda: MindPalace().recall("mesh bands", corpus=BETA_CORPORA["brand"]))
    assert caught.value.status_code in (401, 404)


async def test_credential_management_never_returns_a_hash_or_secret(world):
    """An operator listing credentials sees what to revoke, never the material.

    On the maintenance role, because listing is an enumeration and the runtime
    role is deliberately denied it -- see the posture check.
    """
    from api.services import authz

    async with world.db_module.admin_session_scope() as db:
        with world.db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            rows = await authz.list_credentials(db)
    assert rows
    for row in rows:
        assert "secret" not in row
        assert "key_hash" not in row
        assert set(row) == {
            "id",
            "name",
            "role",
            "disabled",
            "revoked_at",
            "created_at",
            "corpora",
        }


async def test_revoking_by_prefix_revokes_exactly_one(world):
    """`list_credentials` prints a prefix, so revoke has to accept one."""
    from api.services import authz

    async with world.db_module.admin_session_scope() as db:
        with world.db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            issued = await authz.issue_credential(
                db, "prefix-probe", [ALPHA_CORPORA["brand"]], "read_only"
            )
            full, short = issued["id"], issued["id"][:12]
            assert await authz.revoke_credential(db, short) is True
            # Revoking the same credential twice reports it, rather than
            # silently succeeding a second time.
            assert await authz.revoke_credential(db, full) is False
            assert await authz.revoke_credential(db, "no-such-prefix") is False
            assert await authz.revoke_credential(db, "") is False


async def test_a_revoked_credential_stops_the_sdk_client(world, monkeypatch):
    """Revocation has to reach an already-constructed local client."""
    from mindpalace_sdk import MemoryClientError, MindPalace

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    secret = world.credentials["alpha-creator"]["secret"]
    build = lambda: MindPalace(  # noqa: E731 - a one-line factory used twice
        ALPHA_CORPORA["brand"], api_key=secret, create_if_missing=False
    )
    client = run_sdk(build)
    assert run_sdk(lambda: client.recall("battery")).current_memories

    async with world.db_module.admin_session_scope() as db:
        await authz_revoke(db, world.credentials["alpha-creator"]["id"])

    # A new client must be refused...
    with pytest.raises(MemoryClientError):
        run_sdk(build)
    # ... and so must the HTTP path, immediately, with nothing cached.
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(
            "/api/memory/query",
            headers={"Authorization": f"Bearer {secret}"},
            json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
        )
    assert response.status_code == 401


def test_no_environment_variable_is_logged_or_echoed():
    """A configuration secret must not reach a log or an error body.

    Checked by inspecting the module that reads configuration rather than by
    capturing a log, because the failure mode is the absence of a guard: nothing
    in the runtime's replies may carry the credential it was given.
    """
    from pathlib import Path

    import api.runtime as runtime_module

    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    assert "MIND_PALACE_RUNTIME_CREDENTIAL" in source, "the runtime must read it"
    # Every reply shape is a literal dict of a fixed code and message; none may
    # interpolate the credential.
    assert 'os.getenv("MIND_PALACE_RUNTIME_CREDENTIAL") or None' in source
    for literal in (
        '{"ok": False, "error": {"code": "forbidden", "message": "bad runtime token"}}',
        '"runtime_failed", "message": str(exc)',
    ):
        assert literal in source


def test_the_mcp_credential_is_read_from_the_environment_not_a_tool_argument():
    """A tool argument is chosen by the caller; a credential must not be.

    `MIND_PALACE_MCP_CREDENTIAL` is an environment variable precisely because an
    MCP client that could pass its own credential needs no authorization.
    """
    import inspect

    import mcp_server

    source = inspect.getsource(mcp_server)
    assert "MIND_PALACE_MCP_CREDENTIAL" in source
    # No MCP tool takes a credential parameter.
    assert "credential: str" not in source
    assert "api_key: str" not in source


async def test_the_authorization_boundary_is_the_corpus_not_the_username(world):
    """Two credentials in one workspace see the same corpus; neither is special.

    The isolation model is corpus-scoped by design, so a collaborator inside a
    workspace is not over-isolated: a different principal id changes nothing.
    """
    creator = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    admin = await world.post(
        "/api/memory/query",
        "alpha-admin",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert creator.status_code == 200 and admin.status_code == 200
    assert (
        creator.json()["current_memories"] == admin.json()["current_memories"]
    ), "two principals in one workspace must see the same authorised memory"
    assert BETA_PRIVATE_PRODUCT_FACT_7Q2 not in admin.text
    World.assert_no_foreign_canary(admin.json(), own=("alpha",))


def test_corpus_ids_are_derived_not_supplied(world=None):
    """The mapping is a pure function of records the application owns."""
    import hashlib

    # Two organisations' corpora never collide even for the same campaign name.
    a = corpus_id_for(ALPHA_CORPORA["brand"])
    b = corpus_id_for(BETA_CORPORA["brand"])
    assert a != b
    assert a == hashlib.sha256(f"corpus:{ALPHA_CORPORA['brand']}".encode()).hexdigest()


async def authz_revoke(db, principal_id):
    from api.services.authz import revoke_credential

    return await revoke_credential(db, principal_id)


def test_the_shipped_compose_file_does_not_ship_an_unauthenticated_superuser_service():
    """The defect: the default `docker compose up` disabled all three layers.

    Auth off, a database superuser, and a published port. Each is now refused or
    reported, so the compose file has to stop presenting that combination as the
    default: it must declare the authorization mode it ships with.
    """
    from pathlib import Path

    text = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert (
        "MIND_PALACE_AUTH_MODE" in text
    ), "docker-compose must state the authorization mode it ships with"
    assert (
        "MIND_PALACE_DEPLOYMENT" in text
    ), "docker-compose must state which kind of deployment it is"


def test_the_runtime_role_script_can_actually_provision_a_usable_role():
    """The defect: the documented `-v mindpalace_runtime_password` was never read.

    The role was created `LOGIN` with no password, so an operator following the
    documentation verbatim got a role that could not authenticate -- which reads
    as an RLS problem and is not one.
    """
    from pathlib import Path

    sql = Path("scripts/db/runtime_roles.sql").read_text(encoding="utf-8")
    assert "runtime_password" in sql
    assert "ALTER ROLE mindpalace_runtime PASSWORD" in sql
    # ... and the header must document the variable the file actually reads.
    assert "-v runtime_password=" in sql


def test_no_unauthenticated_admin_endpoint_exists():
    """Credential and corpus creation must not be reachable over HTTP.

    Both are administrative and local: a network route that mints a tenant
    boundary or a credential is the thing the whole design exists to avoid.
    """
    import api.main as main_module

    paths = {route.path for route in main_module.app.routes if hasattr(route, "path")}
    forbidden = {
        "/api/credentials",
        "/api/credentials/issue",
        "/api/corpora/create",
        "/api/admin/credentials",
    }
    assert not (paths & forbidden)


# ---------------------------------------------------------------------------
# A client naming a directory on the server's filesystem
# ---------------------------------------------------------------------------


def test_an_ingest_path_outside_the_allowed_roots_is_refused(tmp_path, monkeypatch):
    """The defect: `sync` and `ingest/repo` read any directory the process can see.

    Both routes check the corpus, which made them look bounded — but the path is
    a server-side filesystem path supplied by the caller, so an authenticated
    tenant could ask the service to walk and read a directory it was never given,
    into a corpus it does own. Narrow (`*.md` only), and still not something a
    tenant should be able to do.
    """
    from api.services.ingest_paths import IngestPathRefused, require_permitted

    inside = tmp_path / "content"
    inside.mkdir()
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(tmp_path))

    assert require_permitted(str(inside)) == inside.resolve()
    assert require_permitted(str(inside / ".." / "content")) == inside.resolve()

    with pytest.raises(IngestPathRefused):
        require_permitted("/etc")


def test_an_ingest_path_cannot_escape_through_a_symlink(tmp_path, monkeypatch):
    """Comparing strings would pass this. Resolving is what stops it.

    `/data/link -> /etc` starts with the allowed root as a string and is not in
    it. `Path.resolve()` follows the link first, so the check sees `/etc`.
    """
    from api.services.ingest_paths import IngestPathRefused, require_permitted

    allowed = tmp_path / "data"
    allowed.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.md").write_text("# not yours\n")
    link = allowed / "link"
    link.symlink_to(outside, target_is_directory=True)

    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(allowed))
    with pytest.raises(IngestPathRefused):
        require_permitted(str(link))


def test_an_ingest_path_refusal_does_not_disclose_the_allowed_roots(tmp_path, monkeypatch):
    """A caller refused a path should not learn the directory layout from it."""
    from api.services.ingest_paths import IngestPathRefused, require_permitted

    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(tmp_path / "very-secret-place"))
    with pytest.raises(IngestPathRefused) as caught:
        require_permitted("/etc")
    assert "very-secret-place" not in str(caught.value)


def test_unset_roots_preserve_the_local_single_operator_capability(monkeypatch):
    """Local use is not narrowed by a control that only matters when shared."""
    from api.services.ingest_paths import require_permitted

    monkeypatch.delenv("MIND_PALACE_INGEST_ROOTS", raising=False)
    assert require_permitted("/tmp").as_posix() == "/tmp"


def test_a_server_deployment_refuses_to_start_without_ingest_roots(monkeypatch):
    """Same class of problem as authentication off, refused the same way."""
    from api.services import authz

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.delenv("MIND_PALACE_INGEST_ROOTS", raising=False)

    problems = authz.server_mode_errors()
    assert any("INGEST_ROOTS" in problem for problem in problems), problems
    with pytest.raises(RuntimeError, match="refusing to start"):
        authz.assert_server_configuration()

    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    assert authz.server_mode_errors() == []
    authz.assert_server_configuration()


def test_a_local_deployment_is_not_refused_for_ingest_roots(monkeypatch):
    from api.services import authz

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "local")
    monkeypatch.delenv("MIND_PALACE_INGEST_ROOTS", raising=False)
    assert authz.server_mode_errors() == []


async def test_the_sync_route_refuses_a_path_outside_the_roots(world, tmp_path, monkeypatch):
    """The end-to-end version: a real request, refused before any read."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "doc.md").write_text("# Not yours\n\nSECRET_MARKER_9Q2 content.\n")
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(allowed))
    response = await world.post(
        f"/api/corpora/{ALPHA_CORPORA['brand']}/sync",
        "alpha-creator",
        json={"path": str(outside)},
    )
    assert response.status_code == 403
    assert "SECRET_MARKER_9Q2" not in response.text
    assert str(allowed) not in response.text


async def test_ingested_content_has_a_bound(world, monkeypatch):
    """One request must not be able to hand the embedder an unbounded string."""
    from api.models.schemas import MAX_INGEST_CHARS, IngestFileRequest

    assert MAX_INGEST_CHARS > 0
    with pytest.raises(Exception):
        IngestFileRequest(content="x" * (MAX_INGEST_CHARS + 1), corpus="whatever")
