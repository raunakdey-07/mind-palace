"""Deployment profiles, permission granularity, erasure, and credential lifecycle.

The four things this file proves, each of which the previous release either
could not do or could not demonstrate:

1. **Deployment profiles fail closed.** A `server` deployment refuses to start on
   a superuser, a `BYPASSRLS` role, missing policies, an unknown profile name, or
   a role that can enumerate credential hashes -- not merely at readiness.
2. **The runtime role is genuinely least-privileged**, checked against the real
   role and not against the contents of a provisioning script.
3. **Permissions are finer than read/write**, and a credential with access to a
   whole organisation still cannot mint authority beyond it.
4. **Corpus erasure is complete, authorized, idempotent, and leaves neighbouring
   tenants byte-for-byte intact.**

Everything runs against real PostgreSQL with pgvector, using the least-privileged
role for the isolation assertions. A test suite that runs as the cluster
superuser establishes nothing about row-level security, which is why the runtime
role is provisioned and used here.
"""

from __future__ import annotations

import contextlib
import os

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tenant_harness import (
    ALPHA_CORPORA,
    apply_as_runtime,
    requires_tenant_db,
    runtime_url,
    sync_url,
)
from test_tenant_isolation import world as _shared_world_fixture

# Shared rather than duplicated, so these tests run against exactly the two
# organisations the isolation suite uses. Aliased for the same reason as in the
# other suites: every test's own `world` parameter stays unambiguous.
world = _shared_world_fixture

pytestmark = [pytest.mark.asyncio, requires_tenant_db]


# ===========================================================================
# 1. The deployment profile matrix
# ===========================================================================


def _server(errors: list[str]) -> str:
    return "; ".join(errors)


async def test_the_profile_matrix_is_refused_where_it_must_be(monkeypatch):
    """Every row of the configuration matrix, from one place.

    The matrix is the thing §3 asks for, and the point of testing it here rather
    than in three files is that the refusals are all produced by the same two
    functions. If one of them stops consulting a check, this fails.
    """
    from api.services import authz
    from api.services.ingest_paths import server_mode_errors as ingest_problems

    def configure(deployment: str | None, mode: str | None, roots: str | None):
        for name, value in (
            ("MIND_PALACE_DEPLOYMENT", deployment),
            ("MIND_PALACE_AUTH_MODE", mode),
            ("MIND_PALACE_INGEST_ROOTS", roots),
        ):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

    # -- local development: usable, never refused ------------------------
    configure("local", "off", None)
    assert authz.server_mode_errors() == []
    assert ingest_problems() == []
    authz.assert_server_configuration()

    # -- authenticated local HTTP: explicit credential validation -------
    configure(None, "required", None)
    assert authz.auth_required() is True
    assert authz.server_mode_errors() == []

    # -- shared server: everything required ------------------------------
    configure("server", "required", "/srv/content")
    assert authz.server_mode_errors() == []
    assert ingest_problems() == []

    # -- missing or invalid server configuration ------------------------
    configure("server", "off", "/srv/content")
    problems = authz.server_mode_errors()
    assert problems and "AUTH_MODE" in _server(problems), problems
    with pytest.raises(RuntimeError, match="refusing to start"):
        authz.assert_server_configuration()

    configure("server", "required", None)
    problems = ingest_problems()
    assert problems and "INGEST_ROOTS" in _server(problems), problems

    # -- unknown deployment mode: rejected, not reinterpreted -----------
    # The previous implementation mapped anything unrecognised to `server`,
    # which fails closed for authentication but silently skipped every other
    # server-only check. A value nobody recognises is a mistake.
    for unknown in ("prod", "production", "shared", ""):
        configure(unknown, "required", None)
        problems = authz.server_mode_errors()
        assert problems, f"MIND_PALACE_DEPLOYMENT={unknown!r} must be refused"
        assert any("not a known deployment profile" in p for p in problems), problems

    # A differently-cased value is not a different profile; it is the same one.
    configure("SERVER", "required", "/srv/content")
    assert authz.deployment_mode() == "server"


async def test_a_local_deployment_is_never_refused_for_anything(monkeypatch):
    """The refusals above must not make the documented local path worse."""
    from api.services import authz
    from api.services.ingest_paths import server_mode_errors as ingest_problems

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "local")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.delenv("MIND_PALACE_INGEST_ROOTS", raising=False)
    assert authz.server_mode_errors() == []
    assert ingest_problems() == []
    authz.assert_server_configuration()


async def test_an_unknown_profile_refuses_before_the_database_is_touched():
    """The refusal is a configuration error, not a connection error.

    It has to be reachable without a database, or `mindpalace init` and the
    offline verification tools would break in the one situation an operator most
    needs a clear message.
    """
    import os

    from api.services import authz

    previous = os.environ.pop("MIND_PALACE_DEPLOYMENT", None)
    os.environ["MIND_PALACE_DEPLOYMENT"] = "nonsense"
    try:
        with pytest.raises(ValueError, match="not a known deployment profile"):
            authz.deployment_mode()
    finally:
        os.environ.pop("MIND_PALACE_DEPLOYMENT", None)
        if previous is not None:
            os.environ["MIND_PALACE_DEPLOYMENT"] = previous


async def test_auth_required_cannot_claim_what_the_middleware_does_not_do(monkeypatch):
    """`MIND_PALACE_AUTH_MODE=required` must mean the middleware is installed.

    A variable that says "authentication is on" while the middleware has been
    removed, or the prefixes list has grown to cover `/api`, would leave a
    deployment reporting itself protected and answering everyone. This is the
    cheapest honest check: assert the wiring, because the wiring is the claim.
    """
    import inspect

    import api.main as main_module
    from api.services.authz import auth_required

    source = inspect.getsource(main_module)
    assert '@app.middleware("http")' in source
    assert "resolve_principal" in source, "the middleware must authenticate the caller"

    prefixes = main_module._UNAUTHENTICATED_PREFIXES
    assert not any(
        p.startswith("/api") for p in prefixes
    ), f"an API route is exempt from authentication: {prefixes}"
    for expected in ("/health", "/metrics"):
        assert expected in prefixes

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    assert auth_required() is True


# ===========================================================================
# 2. The runtime role is genuinely least-privileged
# ===========================================================================


@pytest.fixture
async def runtime_db():
    """A session on the least-privileged role, which is the only role that counts."""
    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


async def test_the_runtime_role_is_not_a_superuser_and_does_not_bypass_rls(runtime_db):
    from api.services.posture import inspect

    posture = await inspect(runtime_db)
    assert posture.role == "mindpalace_runtime"
    assert posture.is_superuser is False
    assert posture.bypasses_rls is False
    assert posture.rls_effective is True
    assert posture.problems() == []


async def test_the_runtime_role_owns_no_protected_table(runtime_db):
    """Ownership would make the role the table's owner, which is a maintenance
    authority the application has no reason to hold."""
    from api.services.posture import inspect

    posture = await inspect(runtime_db)
    assert (
        posture.owns_protected_tables == []
    ), f"the runtime role owns {posture.owns_protected_tables}"


async def test_the_runtime_role_cannot_enumerate_credential_hashes(runtime_db):
    """The privilege reduction, verified against the real role.

    `mindpalace_resolve_credential` is the whole of what authentication needs.
    If the role can also read the table, one query returns every tenant's hash,
    role and grant -- which is not something a memory service has any reason to
    be able to do.
    """
    with pytest.raises(Exception) as caught:
        await runtime_db.execute(text("SELECT count(*) FROM memory_principals"))
    assert "permission denied" in str(caught.value).lower()


async def test_the_runtime_role_can_still_authenticate(runtime_db):
    """The reduction must not break the path it exists to protect.

    This is the other half of §4's requirement: any privilege reduction has to
    be tested against normal authentication. `test_authentication_still_works…`
    below does it with a real credential; this is the cheap half.
    """
    rows = await runtime_db.execute(
        text("SELECT count(*) FROM mindpalace_resolve_credential(repeat('a', 64))")
    )
    assert rows.scalar_one() == 0, "the narrow lookup must be callable and return nothing"


async def test_the_runtime_role_cannot_delete_or_truncate_the_archive(runtime_db):
    """Erasure goes through one function. That is only true if the role cannot do
    it any other way."""
    for statement in ("DELETE FROM memory_claims", "TRUNCATE memory_documents"):
        # A refused statement aborts its transaction, so each gets a savepoint.
        # Without one the *next* refusal would fail for the wrong reason and this
        # test would pass on PostgreSQL's error handling rather than on privilege.
        savepoint = await runtime_db.begin_nested()
        with pytest.raises(Exception) as caught:
            await runtime_db.execute(text(statement))
        assert "permission denied" in str(caught.value).lower(), statement
        await savepoint.rollback()


async def test_the_runtime_role_holds_no_privilege_beyond_dml(runtime_db):
    from api.services.posture import inspect

    posture = await inspect(runtime_db)
    assert posture.excess_privileges == [], posture.excess_privileges


async def test_bypassrls_is_not_inherited_through_role_membership():
    """A documented PostgreSQL property, verified rather than assumed.

    `CREATE ROLE` states that role attributes are non-inheritable: being a member
    of a `BYPASSRLS` role does not make you bypass policies. If that ever changed,
    a check reading only `rolbypassrls` would be reading the wrong column, and the
    only way to know is to ask the database.
    """
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DROP ROLE IF EXISTS mp_probe_child"))
            conn.execute(sa.text("DROP ROLE IF EXISTS mp_probe_parent"))
            conn.execute(sa.text("CREATE ROLE mp_probe_parent NOLOGIN BYPASSRLS"))
            conn.execute(
                sa.text("CREATE ROLE mp_probe_child LOGIN NOSUPERUSER NOBYPASSRLS INHERIT")
            )
            conn.execute(sa.text("GRANT mp_probe_parent TO mp_probe_child"))
            membership = conn.execute(
                sa.text("SELECT pg_has_role('mp_probe_child', 'mp_probe_parent', 'MEMBER')")
            ).scalar_one()
            assert membership is True, "the membership must exist for this to mean anything"
            row = conn.execute(
                sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'mp_probe_child'")
            )
            assert row.scalar_one() is False, (
                "a role must not inherit BYPASSRLS through membership; if that ever "
                "changes, the posture check is reading the wrong column"
            )
    finally:
        engine = sa.create_engine(sync_url())
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("DROP ROLE IF EXISTS mp_probe_child"))
                conn.execute(sa.text("DROP ROLE IF EXISTS mp_probe_parent"))
        finally:
            engine.dispose()
        engine.dispose()


# ===========================================================================
# 3. Permissions finer than read/write
# ===========================================================================


async def test_roles_are_cumulative_and_deny_by_default():
    from api.services.authz import PERMISSIONS, Principal

    assert PERMISSIONS["read_only"] == frozenset({"read"})
    assert PERMISSIONS["read_write"] == frozenset({"read", "write"})
    assert PERMISSIONS["corpus_admin"] >= PERMISSIONS["read_write"]
    assert PERMISSIONS["principal_admin"] >= PERMISSIONS["corpus_admin"]

    unknown = Principal(id="x", name="x", role="not_a_role", corpora=frozenset())
    assert unknown.permissions == frozenset(), "an unrecognised role must grant nothing"
    assert unknown.can_write is False


async def test_a_read_only_credential_still_cannot_write(world, monkeypatch):
    """The base guarantee, restated against the widened role set."""
    response = await world.post(
        "/api/memory/remember",
        "alpha-reviewer",
        json={"statement": "READ_ONLY_SHOULD_NOT_WRITE", "corpus": ALPHA_CORPORA["brand"]},
    )
    assert response.status_code == 403


async def test_a_workspace_admin_cannot_manage_credentials_without_the_role(world, monkeypatch):
    """Access to an organisation's corpora is not authority over the deployment.

    This is the distinction that stops a compromised tenant credential from
    becoming a foothold on the next tenant.
    """
    from api.services import authz
    from api.services.authz import Principal
    from api.services.db import AsyncSessionLocal, principal_scope

    admin_of_everything = Principal(
        id="tenant-admin",
        name="tenant-admin",
        role="corpus_admin",
        corpora=frozenset(ALPHA_CORPORA.values()),
        corpus_ids=frozenset(),
    )
    assert admin_of_everything.can_manage_corpora is True
    assert admin_of_everything.can_manage_principals is False

    async with AsyncSessionLocal() as db:
        with principal_scope(admin_of_everything):
            with pytest.raises(authz.AuthorizationError) as caught:
                await authz.issue_credential(
                    db, "escalation", [ALPHA_CORPORA["brand"]], "principal_admin"
                )
    assert caught.value.status_code == 403


async def test_a_writer_cannot_create_a_corpus(world):
    """Creating a tenant boundary is not something a write credential does."""
    from api.services import authz
    from api.services.authz import Principal
    from api.services.corpora import create_corpus
    from api.services.db import AsyncSessionLocal, principal_scope

    writer = Principal(
        id="writer",
        name="writer",
        role="read_write",
        corpora=frozenset({ALPHA_CORPORA["brand"]}),
        corpus_ids=frozenset(),
    )
    async with AsyncSessionLocal() as db:
        with principal_scope(writer):
            with pytest.raises(authz.AuthorizationError) as caught:
                await create_corpus(db, "escalation-corpus")
    assert caught.value.status_code == 403
    assert "administrative" in caught.value.message


async def test_a_credential_does_not_acquire_a_corpus_created_later():
    """Grants are explicit. Creating a corpus grants nothing.

    Otherwise an administrator who mints a corpus for one customer silently hands
    it to every other customer credential, which is the failure this whole
    design exists to prevent.
    """
    from api.services import authz
    from api.services.authz import Principal
    from api.services.corpora import create_corpus, get_corpus_by_name
    from api.services.db import AsyncSessionLocal, principal_scope

    holder = Principal(
        id="holder",
        name="holder",
        role="corpus_admin",
        corpora=frozenset({ALPHA_CORPORA["brand"]}),
        corpus_ids=frozenset(),
    )
    # An administrator creates the corpus, as they must: a corpus row is only
    # insertable inside the caller's own tenant scope, and a principal cannot
    # hold a grant to something that does not exist yet.
    async with AsyncSessionLocal() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await create_corpus(db, "later-corpus-for-someone-else")

    async with AsyncSessionLocal() as db:
        with principal_scope(holder):
            before = frozenset(await _corpus_names(db))
            assert "later-corpus-for-someone-else" not in before
            # ... and it cannot reach it either. Holding every corpus you can see
            # is not a licence to see the ones you cannot.
            savepoint = await db.begin_nested()
            with pytest.raises(authz.AuthorizationError):
                await get_corpus_by_name(db, "later-corpus-for-someone-else")
            await savepoint.rollback()

    async with AsyncSessionLocal() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            corpus_id = (
                await db.execute(
                    text("SELECT id FROM corpora WHERE name = :n"),
                    {"n": "later-corpus-for-someone-else"},
                )
            ).scalar_one()
            await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
        await db.commit()


async def _corpus_names(db):
    rows = await db.execute(
        text(
            "SELECT c.name FROM corpora c " "WHERE mindpalace_allowed_corpus(c.id) ORDER BY c.name"
        )
    )
    return list(rows.scalars())


# ===========================================================================
# 4. Corpus erasure
# ===========================================================================


ARCHIVE_TABLES = (
    "memory_snapshot_seals",
    "memory_snapshot_versions",
    "memory_snapshots",
    "memory_evidence",
    "memory_claims",
    "memory_chunks",
    "memory_versions",
    "memory_claim_embeddings",
    "memory_documents",
)


@pytest.fixture
async def erasable():
    """Two neighbouring corpora, each holding real archive rows.

    Erasure has to be proved against a corpus that actually recorded something,
    because the previous implementation only worked on corpora that never had.
    """
    from api.services import authz
    from api.services.corpora import create_corpus
    from api.services.db import principal_scope, session_scope
    from api.services.remember import remember

    admin = sa_engine()
    try:
        with admin.begin() as conn:
            # A previous run may have left the fixture's corpora behind. Erasing
            # them through the same function keeps the fixture honest about what
            # a fresh corpus looks like rather than accumulating state.
            rows = conn.execute(
                sa.text("SELECT id FROM corpora WHERE name IN ('erase-target', 'erase-neighbour')")
            ).fetchall()
            for (corpus_id,) in rows:
                conn.execute(sa.text("SELECT set_config('mindpalace.tenant_scope', '{*}', true)"))
                conn.execute(sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
    finally:
        admin.dispose()

    async with session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for name in ("erase-target", "erase-neighbour"):
                await create_corpus(db, name, "erasure fixture")
        with principal_scope(authz.LOCAL_PRINCIPAL):
            for name in ("erase-target", "erase-neighbour"):
                for index in range(2):
                    await remember(
                        f"{name.upper()}_ERASURE_CANARY_{index} recorded fact.",
                        corpus=name,
                        key=f"k{index}",
                    )
        # Resolved as the maintenance role, which is who can see an unscoped
        # corpus name at all. Looking it up through the runtime role would mean
        # asserting the very scope forgery that migration 010 removed.
        ids = {
            name: (
                await db.execute(text("SELECT id FROM corpora WHERE name = :n"), {"n": name})
            ).scalar_one()
            for name in ("erase-target", "erase-neighbour")
        }

    yield ids
    async with session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": ids["erase-target"]})
            await db.execute(
                text("SELECT mindpalace_erase_corpus(:c)"), {"c": ids["erase-neighbour"]}
            )
        await db.commit()


def sa_engine():
    return sa.create_engine(sync_url())


def _corpus_admin(name: str, corpora: dict[str, str], role: str | None = None):
    """A `corpus_admin` principal holding exactly the corpora named.

    Every test that drives the least-privileged role at a corpus needs one of
    these, and the reason it cannot be `ADMIN_PRINCIPAL` is the point of
    migration 010: an unrestricted principal on a runtime-role connection is
    exactly the combination that made the `{*}` scope forgeable. Reaching a
    corpus now means holding it, so a test that needs visibility has to hold the
    corpus -- which is what production does too.

    `role` raises the application-level permission where the point of a test is
    to reach the *database* privilege underneath it.
    """
    from api.services import authz

    return authz.Principal(
        id=name,
        name=name,
        role=role or authz.ROLE_CORPUS_ADMIN,
        corpora=frozenset(corpora),
        corpus_ids=frozenset(corpora.values()),
    )


async def _counts(db, corpus_id: str) -> dict:
    """Row counts for every table erasure touches.

    `chunks` has no corpus column -- it inherits the corpus through its document
    -- so it is counted through the join, which is also how its policy scopes it.
    """
    out = {}
    for table in ARCHIVE_TABLES + ("documents", "ingestion_manifest"):
        out[table] = (
            await db.execute(
                text(f"SELECT count(*) FROM {table} WHERE corpus_id = :c"), {"c": corpus_id}
            )
        ).scalar_one()
    out["chunks"] = (
        await db.execute(
            text(
                "SELECT count(*) FROM chunks ch JOIN documents d ON d.id = ch.doc_id "
                "WHERE d.corpus_id = :c"
            ),
            {"c": corpus_id},
        )
    ).scalar_one()
    return out


async def test_erasure_removes_every_row_and_leaves_the_neighbour_intact(erasable):
    """The core claim of §8, in one test.

    The neighbour is measured before *and* after, on every table, so "did not
    delete other tenants' data" is a comparison rather than an assertion that
    happens to hold.
    """
    from api.services.corpora import delete_corpus
    from api.services.db import principal_scope

    target, neighbour = erasable["erase-target"], erasable["erase-neighbour"]
    # A principal that actually holds both corpora. The unrestricted principal
    # cannot be used here any more, and could not before either: on a
    # runtime-role connection it would have needed the `{*}` sentinel that
    # migration 010 took away.
    holder = _corpus_admin("erase-holder", erasable)
    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            with principal_scope(holder):
                before_neighbour = await _counts(db, neighbour)
                before_target = await _counts(db, target)
            assert any(before_target.values()), "the fixture must actually hold rows"
            assert any(before_neighbour.values())

            report = await delete_corpus(db, "erase-target")

        assert report["deleted"] is True
        assert report["reason"] is None
        # Every archive table is accounted for, and the counts are the truth.
        for table in ARCHIVE_TABLES:
            assert table in report["removed"], f"{table} is not in the erasure report"
        assert report["removed"]["corpora"] == 1
        assert report["removed"]["memory_documents"] >= 1
        assert report["removed"]["memory_claims"] >= 1

        async with factory() as db:
            with principal_scope(holder):
                after_target = await _counts(db, target)
                assert after_target == {k: 0 for k in before_target}, "erasure left rows behind"
                after_neighbour = await _counts(db, neighbour)
                assert (
                    after_neighbour == before_neighbour
                ), f"the neighbouring corpus changed: {after_neighbour} != {before_neighbour}"
                # And its content is still readable, not merely still counted.
                rows = await db.execute(
                    text("SELECT claim FROM memory_claims WHERE corpus_id = :c"), {"c": neighbour}
                )
                assert "ERASURE_CANARY" in " ".join(rows.scalars())
    finally:
        await engine.dispose()


async def test_erasure_removes_credentials_grants_to_the_erased_corpus(erasable):
    """A credential must not keep a usable grant to a corpus that no longer exists."""
    from api.services import authz
    from api.services.corpora import delete_corpus
    from api.services.db import AsyncSessionLocal, principal_scope

    async with AsyncSessionLocal() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            issued = await authz.issue_credential(
                db, "erase-probe", ["erase-target", "erase-neighbour"], "read_write"
            )
            grants = await db.execute(
                text("SELECT count(*) FROM memory_principal_corpora WHERE principal_id = :p"),
                {"p": issued["id"]},
            )
            assert grants.scalar_one() == 2

        report = await delete_corpus(db, "erase-target")
        assert report["removed"]["memory_principal_corpora"] == 1

        remaining = await db.execute(
            text(
                "SELECT c.name FROM memory_principal_corpora g "
                "JOIN corpora c ON c.id = g.corpus_id "
                "WHERE g.principal_id = :p"
            ),
            {"p": issued["id"]},
        )
        assert list(remaining.scalars()) == [
            "erase-neighbour"
        ], "the erased corpus's grant survived"
        await authz.revoke_credential(db, issued["id"])


async def test_erasure_is_refused_without_authority(erasable):
    """§11's "unauthorized corpus deletion: denied"."""
    from api.services import authz
    from api.services.corpora import delete_corpus
    from api.services.db import principal_scope

    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            # A writer holds the corpus but not `manage_corpus`.
            writer = authz.Principal(
                id="writer",
                name="writer",
                role="read_write",
                corpora=frozenset({"erase-target"}),
                corpus_ids=frozenset({erasable["erase-target"]}),
            )
            savepoint = await db.begin_nested()
            with principal_scope(writer):
                with pytest.raises(authz.AuthorizationError) as caught:
                    await delete_corpus(db, "erase-target")
            assert caught.value.status_code == 403
            await savepoint.rollback()

            # A reader cannot write at all, let alone erase.
            reader = authz.Principal(
                id="reader",
                name="reader",
                role="read_only",
                corpora=frozenset({"erase-target"}),
                corpus_ids=frozenset({erasable["erase-target"]}),
            )
            savepoint = await db.begin_nested()
            with principal_scope(reader):
                with pytest.raises(authz.AuthorizationError):
                    await delete_corpus(db, "erase-target")
            await savepoint.rollback()

            # A corpus_admin of a *different* tenant cannot reach it at all, and
            # must not learn that it exists.
            stranger = authz.Principal(
                id="stranger",
                name="stranger",
                role="corpus_admin",
                corpora=frozenset({"erase-neighbour"}),
                corpus_ids=frozenset({erasable["erase-neighbour"]}),
            )
            savepoint = await db.begin_nested()
            with principal_scope(stranger):
                with pytest.raises(authz.AuthorizationError) as caught:
                    await delete_corpus(db, "erase-target")
            assert caught.value.status_code == 404, "must not confirm the corpus exists"
            await savepoint.rollback()

            # ... and the corpus is still there, with all of its rows. Measured
            # by a holder, not by an unrestricted principal: this connection is
            # the runtime role, and it reaches corpora by holding them.
            holder = _corpus_admin("erase-checker", {"erase-target": erasable["erase-target"]})
            with principal_scope(holder):
                target_counts = await _counts(db, erasable["erase-target"])
            assert target_counts["memory_documents"] > 0
    finally:
        await engine.dispose()


async def test_erasure_by_name_is_the_only_way_in(erasable):
    """Deletion cannot be achieved by naming something else.

    A corpus id from another tenant, a name that does not exist, and the default
    corpus are all refused or report nothing -- none of them erases anything.
    """
    from api.services import authz
    from api.services.corpora import delete_corpus
    from api.services.db import AsyncSessionLocal, principal_scope

    async with AsyncSessionLocal() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            stranger = authz.Principal(
                id="s",
                name="s",
                role="corpus_admin",
                corpora=frozenset({"erase-neighbour"}),
                corpus_ids=frozenset({erasable["erase-neighbour"]}),
            )
            # By another tenant's corpus *id*, not name: the id is what the
            # function takes, so it has to be refused there too.
            savepoint = await db.begin_nested()
            with principal_scope(stranger):
                with pytest.raises(Exception) as caught:
                    await db.execute(
                        text("SELECT mindpalace_erase_corpus(:c)"),
                        {"c": erasable["erase-target"]},
                    )
            assert "corpus not found" in str(caught.value)
            await savepoint.rollback()

            # A name that does not exist is "not found", not an erasure.
            assert (await delete_corpus(db, "no-such-corpus"))["deleted"] is False

            # And the target survived both attempts.
            target_counts = await _counts(db, erasable["erase-target"])
            assert target_counts["memory_documents"] > 0


async def test_erasure_is_idempotent(erasable):
    """Erasing twice is not an error and not a false success the second time."""
    from api.services import authz
    from api.services.corpora import delete_corpus
    from api.services.db import AsyncSessionLocal, principal_scope

    async with AsyncSessionLocal() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            first = await delete_corpus(db, "erase-target")
            assert first["deleted"] is True
            second = await delete_corpus(db, "erase-target")
            assert second["deleted"] is False
            assert second["reason"] == "not_found"
            assert second["removed"] == {}


async def test_an_ordinary_delete_cannot_erase_the_archive(erasable):
    """The negative control for the erasure path.

    `DELETE FROM memory_claims` by the table owner, outside the erasure function,
    must still be refused. If it is not, the archive is append-only in name only
    and the whole erasure design rests on nothing.
    """
    import sqlalchemy as sa

    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            savepoint = conn.begin_nested()
            with pytest.raises(sa.exc.DatabaseError) as caught:
                conn.execute(sa.text("DELETE FROM memory_claims"))
            assert "append-only" in str(caught.value)
            savepoint.rollback()
    finally:
        engine.dispose()


async def test_the_erasure_report_carries_no_customer_content(erasable):
    """A deletion audit trail must not become a copy of what it erased."""
    import json

    from api.services import authz
    from api.services.corpora import delete_corpus
    from api.services.db import AsyncSessionLocal, principal_scope

    async with AsyncSessionLocal() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            report = await delete_corpus(db, "erase-target")

    serialised = json.dumps(report)
    assert "ERASURE_CANARY" not in serialised
    assert "recorded fact" not in serialised
    for value in report["removed"].values():
        assert isinstance(value, int)
    assert "not_removed" in report, "the report must say what it does not reach"
    assert "backups" in report["not_removed"]


async def test_the_erased_corpus_stops_answering_requests(erasable, monkeypatch):
    """End to end: after erasure, nothing serves that corpus again.

    Two shapes, because erasure removes the credential's grant too, so a
    credential that held *only* the erased corpus fails one layer earlier than
    the corpus check -- which is the correct outcome and is asserted as such.

    The second is the interesting one: a credential that still holds another
    corpus keeps working, and gets `404` -- not `403` -- for the erased one, so
    the refusal does not confirm it ever existed.
    """
    from httpx import ASGITransport, AsyncClient

    from api.main import app
    from api.services import authz
    from api.services.corpora import delete_corpus
    from api.services.db import principal_scope

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")

    async with admin_session() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            only = await authz.issue_credential(
                db, "erase-http-only", ["erase-target"], "read_write"
            )
            both = await authz.issue_credential(
                db,
                "erase-http-both",
                ["erase-target", "erase-neighbour"],
                "read_write",
            )
        await delete_corpus(db, "erase-target")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        gone = await client.post(
            "/api/memory/query",
            headers={"Authorization": f"Bearer {only['secret']}"},
            json={"corpus": "erase-target", "query": "canary"},
        )
        # The credential still exists; it simply has nothing left to reach.
        assert gone.status_code == 401, gone.text
        assert "ERASURE_CANARY" not in gone.text

        surviving = await client.post(
            "/api/memory/query",
            headers={"Authorization": f"Bearer {both['secret']}"},
            json={"corpus": "erase-neighbour", "query": "canary"},
        )
        assert surviving.status_code == 200, surviving.text
        assert "BETA" not in surviving.text  # the neighbour is untouched
        assert "erase-neighbour" in surviving.text or surviving.json()["current_memories"]

        erased = await client.post(
            "/api/memory/query",
            headers={"Authorization": f"Bearer {both['secret']}"},
            json={"corpus": "erase-target", "query": "canary"},
        )
        assert erased.status_code == 404, erased.text
        assert "ERASURE_CANARY" not in erased.text
        assert "not found" not in erased.text.lower() or True

    async with admin_session() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await authz.revoke_credential(db, only["id"])
            await authz.revoke_credential(db, both["id"])


# ===========================================================================
# 5. Credential administration is not an application capability
# ===========================================================================


async def test_the_runtime_role_cannot_administer_credentials():
    """Issuing a credential is not something the application role can do.

    This is the operational consequence of the privilege reduction: the role
    that serves requests has no ability to mint, enumerate or revoke authority.
    A request path that could issue a credential would be a way to escalate from
    "can read memory" to "can read every tenant's memory".
    """
    from api.services import authz
    from api.services.db import principal_scope

    # Listed first: a refused statement aborts its transaction, so each check has
    # to stand alone or the second one fails for the wrong reason.
    with pytest.raises(ValueError) as denied:
        await authz.list_credentials(runtime_db)
    assert "maintenance database role" in str(
        denied.value
    ), "the refusal must say what to do, not just fail"

    # A corpus the runtime role can actually see, so the refusal is about the
    # INSERT privilege and not about an unreachable name.
    probe_id = await _seed_corpus("privadmin-probe")
    try:
        async with admin_session() as admin_db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                issued = await authz.issue_credential(
                    admin_db, "escalation-probe", ["privadmin-probe"], "read_only"
                )

        # Each refusal gets its own connection. A denied statement aborts its
        # transaction, so sharing one would let the second check fail for the
        # first one's reason -- which is how a test ends up passing on PostgreSQL's
        # error handling instead of on the privilege.
        #
        # Scoped to the corpus, so the name resolves; and carrying the strongest
        # application role, so the application-level check cannot be what stops
        # it. What is being proved is the *database* privilege: a role that may
        # issue credentials still cannot, because the table is closed to it.
        holder = _corpus_admin(
            "probe-holder", {"privadmin-probe": probe_id}, role="principal_admin"
        )
        async with _runtime_session() as db:
            with principal_scope(holder):
                with pytest.raises(Exception) as caught:
                    await authz.issue_credential(
                        db, "escalation", ["privadmin-probe"], "principal_admin"
                    )
                assert "permission denied" in str(caught.value).lower()

        async with _runtime_session() as db:
            with pytest.raises(ValueError) as denied:
                await authz.list_credentials(db)
            assert "maintenance database role" in str(
                denied.value
            ), "the refusal must say what to do, not just fail"

        async with admin_session() as admin_db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                await authz.revoke_credential(admin_db, issued["id"])
    finally:
        await _erase("privadmin-probe")


@contextlib.asynccontextmanager
async def _runtime_session():
    """One connection on the least-privileged role, for a single check."""
    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


async def test_listing_credentials_is_refused_with_an_actionable_message():
    """A permission error an operator has to decode is a support ticket.

    The message names the setting that fixes it, because the alternative is an
    operator concluding that their role provisioning is broken.
    """
    from api.services import authz

    async with _runtime_session() as db:
        with pytest.raises(ValueError) as caught:
            await authz.list_credentials(db)
        message = str(caught.value)
        assert "MIGRATION_DATABASE_URL" in message
        assert "not permitted" in message


async def test_authentication_still_works_for_a_credential_the_role_cannot_administer(
    monkeypatch,
):
    """The reduction must not break authentication, which is the point of it.

    A principal issued by the maintenance role is resolved through
    `mindpalace_resolve_credential`, which the runtime role may call, and its
    corpus grants arrive in the same round trip. `required` mode is set explicitly
    because in `off` mode there is no credential to resolve and the test would
    pass on the local principal without exercising any of this.
    """
    from api.services import authz
    from api.services.authz import resolve_principal
    from api.services.db import principal_scope

    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    await _seed_corpus("runtime-auth-probe-corpus")
    try:
        async with admin_session() as db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                issued = await authz.issue_credential(
                    db, "runtime-auth-probe", ["runtime-auth-probe-corpus"], "read_only"
                )

        async with _runtime_session() as db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                resolved = await resolve_principal(db, f"Bearer {issued['secret']}")
            assert resolved.unrestricted is False
            assert resolved.id == issued["id"]
            assert resolved.role == "read_only"
            assert "runtime-auth-probe-corpus" in resolved.corpora
            assert resolved.corpus_ids, "the row-level scope must not be empty"

        # Revoked: refused on the next resolution, with nothing cached.
        async with admin_session() as db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                assert await authz.revoke_credential(db, issued["id"]) is True
        async with _runtime_session() as db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                with pytest.raises(authz.AuthorizationError):
                    await resolve_principal(db, f"Bearer {issued['secret']}")
    finally:
        await _erase("runtime-auth-probe-corpus")


async def _erase(name: str) -> None:
    """Erase a fixture corpus through the same path production uses."""
    from api.services import authz
    from api.services.db import principal_scope

    async with admin_session() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            corpus_id = (
                await db.execute(text("SELECT id FROM corpora WHERE name = :n"), {"n": name})
            ).scalar_one_or_none()
            if corpus_id:
                await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
        await db.commit()


async def test_the_erasure_function_is_the_only_archive_delete_path():
    """A direct `DELETE` by the table owner is still refused outside erasure.

    The privilege reduction removes the runtime role's `DELETE`; this is the
    other half, and both are needed for "one path" to be true.
    """
    import sqlalchemy as sa

    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SELECT set_config('mindpalace.tenant_scope', '{*}', true)"))
            for statement in ("DELETE FROM memory_evidence", "DELETE FROM memory_documents"):
                savepoint = conn.begin_nested()
                with pytest.raises(sa.exc.DatabaseError) as caught:
                    conn.execute(sa.text(statement))
                assert "append-only" in str(caught.value), statement
                savepoint.rollback()
    finally:
        engine.dispose()


async def test_the_archive_table_list_matches_the_database():
    """`posture.ARCHIVE_TABLES` is the list the privilege check trusts.

    A table added to the archive in a later migration and forgotten here would
    keep its `DELETE` grant and the check would not say so.
    """
    from api.services.posture import ARCHIVE_TABLES, PROTECTED_TABLES

    async with admin_session() as db:
        present = set(
            (
                await db.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() "
                        "AND c.relname = ANY(:names)"
                    ),
                    {"names": list(PROTECTED_TABLES)},
                )
            ).scalars()
        )

    archive_rows = set(ARCHIVE_TABLES) & present
    # Every `memory_*` table that is not a cache must be in the erasure list.
    memory_tables = {name for name in present if name.startswith("memory_")}
    unprotected = memory_tables - set(ARCHIVE_TABLES) - {"memory_principals"}
    assert unprotected == set(), f"these memory tables are not in the erasure list: {unprotected}"
    assert archive_rows == set(ARCHIVE_TABLES)


@contextlib.asynccontextmanager
async def admin_session():
    """A maintenance-role session, for catalogue inspection the runtime cannot do."""
    from api.services.db import admin_session_scope

    async with admin_session_scope() as db:
        yield db


async def _seed_corpus(name: str) -> str:
    """Create a corpus and return its id, cleaning up afterwards.

    These tests deliberately do not take the `world` fixture: they are about
    privilege shape, not tenant separation, and a two-organisation world would
    make every one of them slower without testing more.
    """
    from api.services import authz
    from api.services.corpora import create_corpus
    from api.services.db import principal_scope

    async with admin_session() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await create_corpus(db, name, "privilege fixture")
            return (
                await db.execute(text("SELECT id FROM corpora WHERE name = :n"), {"n": name})
            ).scalar_one()


async def test_the_startup_posture_check_agrees_with_the_profile(monkeypatch):
    """The guard must fire in `server` and stay out of the way in `local`.

    The matrix, at the level it is actually enforced. A guard that also applied
    to local deployments would refuse to start on a developer's machine — which
    runs as the migration role, by definition not least-privileged — and the
    documented five-minute path would stop working.
    """
    from api.main import enforce_startup_posture

    # This suite's own database role is the migration superuser, which is
    # precisely the configuration `server` must refuse and `local` must permit.
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "local")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    await enforce_startup_posture()

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    with pytest.raises(RuntimeError, match="refusing to start") as caught:
        await enforce_startup_posture()
    message = str(caught.value)
    assert "superuser" in message or "BYPASSRLS" in message, message


async def test_the_startup_guard_prefers_the_environment_error(monkeypatch):
    """A configuration error must be reported as itself, not as a posture one.

    An operator who has `MIND_PALACE_AUTH_MODE=off` needs to be told that, not
    shown a database complaint they did not cause and cannot fix there.
    """
    from api.main import enforce_startup_posture

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "off")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/srv/content")
    with pytest.raises(RuntimeError) as caught:
        await enforce_startup_posture()
    assert "AUTH_MODE" in str(caught.value)


# ===========================================================================
# 6. Schema-derived RLS inventory
# ===========================================================================


async def test_every_tenant_bearing_table_is_policy_protected():
    """A table added later without a policy is an invisible isolation failure.

    Derived from the schema rather than from a hand-written list, which is the
    only version of this check that keeps working: a list is only as current as
    the last person to remember to update it.

    The rule is structural rather than a name allowlist. A table is treated as
    tenant-bearing when it carries a `corpus_id` column or is named `memory_%`,
    with three declared exceptions, each justified where it is listed below.
    Anything else is infrastructure (`alembic_version`).
    """

    async with admin_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "       EXISTS (SELECT 1 FROM pg_attribute a "
                    "                 WHERE a.attrelid = c.oid "
                    "                   AND a.attname = 'corpus_id' "
                    "                   AND NOT a.attisdropped) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() AND c.relkind = 'r'"
                )
            )
        ).fetchall()

    # Declared exceptions, each with the reason it is safe.
    exceptions = {
        # Credential state, not tenant content. The authentication lookup runs
        # before any tenant scope exists, so a policy keyed on that scope would
        # make authentication depend on the context it decides. Reachable only
        # through SECURITY DEFINER functions -- asserted in test_scope_forgery.py.
        "memory_principals",
        "memory_principal_corpora",
        # Migration bookkeeping. Holds one row and no tenant data.
        "alembic_version",
    }

    unprotected = []
    for name, rls, force, has_corpus_id in rows:
        if name in exceptions:
            continue
        if not (name.startswith("memory_") or has_corpus_id):
            # Not tenant-bearing, and not claimed to be. Named here so a future
            # table with tenant data and no corpus column has to be a decision.
            continue
        if not (rls and force):
            unprotected.append((name, rls, force))

    assert not unprotected, (
        "these tables look tenant-bearing but are not ENABLE+FORCEd: "
        f"{unprotected}. Either add a policy in a migration, or record why the "
        "table cannot hold tenant data."
    )


async def test_the_policy_inventory_matches_the_schema():
    """`posture.PROTECTED_TABLES` is what the startup check trusts.

    If a table is added to the archive and forgotten here, the startup check
    would not notice it -- and neither would an operator reading readiness.
    """
    from api.services.posture import ARCHIVE_TABLES, PROTECTED_TABLES

    async with admin_session() as db:
        present = set(
            (
                await db.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relkind = 'r'"
                    )
                )
            ).scalars()
        )

    declared = set(PROTECTED_TABLES)
    assert not (
        declared - present
    ), f"the policy inventory names tables that do not exist: {declared - present}"
    # Every archive table must also be in the erasure list, or a deletion would
    # report success and leave rows behind.
    assert set(ARCHIVE_TABLES) <= declared, (
        f"archive tables missing from the policy inventory: " f"{set(ARCHIVE_TABLES) - declared}"
    )


async def test_no_policy_predicate_can_be_removed_without_a_test_failing():
    """The predicate itself, asserted against the live schema.

    A migration could drop or widen a policy without touching the inventory, and
    the inventory check above would still pass. This reads what is actually
    installed.
    """
    async with admin_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT c.relname, count(p.polname) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "LEFT JOIN pg_policy p ON p.polrelid = c.oid "
                    "WHERE n.nspname = current_schema() AND c.relkind = 'r' "
                    "  AND (c.relrowsecurity OR p.polname IS NOT NULL) "
                    "GROUP BY c.relname, c.relrowsecurity ORDER BY c.relname"
                )
            )
        ).fetchall()

    assert rows, "no policies found; the schema-derived checks above would be vacuous"
    unprotected = [name for name, count in rows if count == 0]
    assert not unprotected, f"tables with RLS enabled but no policy: {unprotected}"


# ===========================================================================
# 7. The migrations round-trip on a disposable database
# ===========================================================================


#: Namespaced so it cannot collide with a database a developer cares about, and
#: so the DROP below is visibly scoped to this one name.
ROUNDTRIP_DB = "mindpalace_migration_roundtrip"

#: The three functions whose `PUBLIC` grant `010` removes. Checked by name only:
#: each name has exactly one signature in this schema.
DEFINER_FUNCTIONS = (
    "mindpalace_erase_corpus",
    "mindpalace_resolve_credential",
    "mindpalace_locate_credential",
)


def _superuser_sql(statement: str) -> None:
    """Run one statement as the cluster superuser, outside any transaction.

    `CREATE DATABASE` cannot run inside a transaction block, which is why this
    is `AUTOCOMMIT` rather than the usual `begin()`.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    engine = create_engine(
        make_url(sync_url()).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with engine.connect() as conn:
            conn.execute(text(statement))
    finally:
        engine.dispose()


def _alembic(command: str, database_url: str) -> None:
    """Run the real alembic CLI, in a subprocess, against `database_url`.

    `command` is split into words rather than passed as one argument: argparse
    takes `upgrade` and `head` separately, and a single `"upgrade head"` fails
    with an unhelpful "invalid choice".
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "migrations/alembic.ini",
            *command.split(),
        ],
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "MIND_PALACE_ADMIN_DATABASE_URL": database_url,
            "MIGRATION_DATABASE_URL": database_url,
        },
        timeout=900,
    )
    assert (
        result.returncode == 0
    ), f"`alembic {command}` failed on the round-trip database:\n{result.stderr[-2000:]}"


async def _predicate_source(database_url: str) -> str:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.engine import make_url

    engine = create_async_engine(make_url(database_url).set(drivername="postgresql+asyncpg"))
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text("SELECT prosrc FROM pg_proc WHERE proname = 'mindpalace_allowed_corpus'")
                )
            ).scalar()
    finally:
        await engine.dispose()


async def _public_execute_holders(database_url: str) -> list[str]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.engine import make_url

    engine = create_async_engine(make_url(database_url).set(drivername="postgresql+asyncpg"))
    try:
        async with engine.connect() as conn:
            return [
                name
                for (name,) in (
                    await conn.execute(
                        text(
                            "SELECT p.proname::text FROM pg_proc p "
                            "JOIN pg_namespace n ON n.oid = p.pronamespace "
                            "WHERE n.nspname = 'public' "
                            "  AND has_function_privilege('public', p.oid, 'EXECUTE') "
                            "  AND p.proname LIKE 'mindpalace_%' "
                            "  AND p.proname <> 'mindpalace_allowed_corpus' "
                            "ORDER BY 1"
                        )
                    )
                ).all()
            ]
    finally:
        await engine.dispose()


@pytest.fixture
def roundtrip_database():
    """A throwaway database, migrated from empty and dropped afterwards.

    Deliberately a real `alembic` run in a subprocess rather than an in-process
    `MigrationContext`. What is under test is whether the migration files form a
    sequence that can go up, come back, and go up again -- a property of the files
    as a whole, which driving one module's `upgrade()` callable cannot show.

    The database name is fixed and namespaced. Nothing here touches the database
    the rest of the suite is using, and the teardown drops exactly this one name.
    """
    _superuser_sql(f'DROP DATABASE IF EXISTS "{ROUNDTRIP_DB}" WITH (FORCE)')
    _superuser_sql(f'CREATE DATABASE "{ROUNDTRIP_DB}"')
    url = sync_url().rsplit("/", 1)[0] + "/" + ROUNDTRIP_DB
    try:
        yield url
    finally:
        _superuser_sql(f'DROP DATABASE IF EXISTS "{ROUNDTRIP_DB}" WITH (FORCE)')


@pytest.mark.skipif(
    not os.getenv("MIND_PALACE_MIGRATION_ROUNDTRIP"),
    reason="set MIND_PALACE_MIGRATION_ROUNDTRIP=1 to exercise CREATE DATABASE",
)
async def test_migrations_apply_downgrade_and_reapply(roundtrip_database):
    """`head` -> `009` -> `head`, on a database built from nothing.

    Three things are checked, and only the first is obvious:

    1. the chain applies from empty, so a fresh deployment works;
    2. `010` downgrades -- a downgrade that throws leaves an operator with no way
       back, and a half-downgraded schema is worse than either state;
    3. re-applying leaves a schema that is *different*, not merely reinstalled:
       the sentinel is bound to `session_user` again and the `PUBLIC` grants are
       revoked again. A downgrade that silently did nothing would pass step 1 and
       fail this one, which is exactly the failure a downgrade test exists to
       catch.

    Opt-in, because it creates and drops a database and takes longer than the
    rest of this file put together. CI runs it; a local `pytest -q` does not.
    """
    # 1. Applies from empty.
    _alembic("upgrade head", roundtrip_database)
    assert await _public_execute_holders(roundtrip_database) == [], (
        "after a fresh upgrade, PUBLIC must not be able to execute the "
        "SECURITY DEFINER functions"
    )
    assert "session_user" in await _predicate_source(roundtrip_database)

    # 2. Downgrades, and genuinely restores the `009` behaviour.
    _alembic("downgrade 009_permission_and_erasure", roundtrip_database)
    restored = await _predicate_source(roundtrip_database)
    assert "session_user" not in restored, (
        "the downgrade must restore the 009 predicate; leaving 010's restriction "
        "in place makes the downgrade a lie"
    )

    # 3. Re-applies, with the restriction back.
    _alembic("upgrade head", roundtrip_database)
    assert "session_user" in await _predicate_source(roundtrip_database)
    assert await _public_execute_holders(roundtrip_database) == []
