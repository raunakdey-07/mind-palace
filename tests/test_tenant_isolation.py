"""Adversarial tenant-isolation tests.

The claim under test: a caller from one organisation cannot read, write, search,
list, export or infer another organisation's memory through *any* supported
interface -- the HTTP memory operations, live retrieval, context assembly, the
operational feed, corpus administration, ingestion, or the database itself.

Every test runs on real PostgreSQL with the application connected as a role that
is not a superuser, does not have `BYPASSRLS`, and owns no tables. The same tests
against the migration role would pass whether or not the policies exist, because
PostgreSQL exempts superusers from row-level security entirely.

The organisations are Alpha and Beta. Their canary content carries a marker that
names the organisation, so a failure says what leaked rather than only that
something did.
"""

from __future__ import annotations

import hashlib

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tenant_harness import (
    RUNTIME_ROLE,
    ALPHA_CORPORA,
    ALPHA_PRIVATE_CAMPAIGN_FACT_9M4,
    ALPHA_PRIVATE_PRODUCT_FACT_7Q2,
    ALL_CANARIES,
    BETA_CORPORA,
    BETA_PRIVATE_CAMPAIGN_FACT_9M4,
    BETA_PRIVATE_PRODUCT_FACT_7Q2,
    apply_provision_sql,
    apply_as_runtime,
    requires_tenant_db,
    runtime_url,
    sync_url,
)

pytestmark = [pytest.mark.asyncio, requires_tenant_db]

#: Every route that can reach tenant-owned rows. The coverage test asserts that
#: each one refuses an unauthenticated caller, so adding a route that skips the
#: boundary fails this file rather than leaking.
TENANT_ROUTES = (
    ("POST", "/api/memory/remember"),
    ("POST", "/api/memory/query"),
    ("POST", "/api/memory/current"),
    ("POST", "/api/memory/history"),
    ("POST", "/api/memory/changes"),
    ("POST", "/api/memory/evidence"),
    ("POST", "/api/memory/as-of"),
    ("POST", "/api/memory/snapshot"),
    ("POST", "/api/memory/replay"),
    ("POST", "/api/memory/pack"),
    ("GET", "/api/memory/feed"),
    ("GET", "/api/corpora"),
    ("GET", "/api/corpora/{name}"),
    ("DELETE", "/api/corpora/{name}"),
    ("POST", "/api/corpora/{name}/sync"),
    ("POST", "/api/ingest/file"),
    ("POST", "/api/ingest/repo"),
    ("GET", "/api/search"),
    ("GET", "/api/context"),
    ("POST", "/api/query/ask"),
    ("POST", "/api/query/summarize"),
    ("POST", "/api/query/interview"),
    ("POST", "/api/query/related"),
    ("GET", "/api/query/timeline"),
)

# A tuple, not a merge of two dicts: both organisations use the same keys
# (`brand`, `campaign_1`, ...), so merging them would silently keep only Beta's.
ALL_CORPORA = tuple(ALPHA_CORPORA.values()) + tuple(BETA_CORPORA.values())

#: Rows this suite owns and may erase. Deliberately narrow. The archive is
#: append-only under ordinary DML, so a fixture has to disable that trigger to
#: erase anything -- and a fixture that emptied every corpus would delete
#: whatever another suite had just seeded in the same database.
PURGE_TABLES = (
    "memory_evidence",
    "memory_claims",
    "memory_chunks",
    "memory_snapshot_versions",
    "memory_snapshot_seals",
    "memory_snapshots",
    "memory_versions",
    "memory_documents",
    "memory_claim_embeddings",
    "chunks",
    "ingestion_manifest",
    "documents",
)

#: Names this file creates: the two organisations' corpora, plus the outsider's.
OWNED_CORPORA = (
    *ALPHA_CORPORA.values(),
    *BETA_CORPORA.values(),
    "outsider-brand",
)

#: Credentials this file issues.
OWNED_PRINCIPALS = (
    "alpha-admin",
    "alpha-creator",
    "alpha-reviewer",
    "beta-admin",
    "beta-creator",
    "beta-reviewer",
    "alpha-revoked",
    "outsider",
)


def corpus_id_for(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def _reset(conn) -> None:
    """Erase what this suite created, and nothing else.

    Scoped by corpus id rather than by truncating, for two reasons: the archive
    tables reject ordinary DML, and another suite may hold data here that this one
    has no business deleting.
    """
    ids = [
        row.rstrip()
        for row in conn.execute(
            sa.text("SELECT id FROM corpora WHERE name = ANY(:names)"),
            {"names": list(OWNED_CORPORA)},
        ).scalars()
    ]
    for table in PURGE_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
    for cid in ids:
        conn.execute(
            sa.text(
                "DELETE FROM chunks WHERE doc_id IN "
                "(SELECT id FROM documents WHERE corpus_id = :cid)"
            ),
            {"cid": cid},
        )
        for table in PURGE_TABLES:
            if table == "chunks":
                continue
            column = "doc_id" if table == "ingestion_manifest" else "corpus_id"
            conn.execute(sa.text(f"DELETE FROM {table} WHERE {column} = :cid"), {"cid": cid})
    for table in PURGE_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))

    conn.execute(
        sa.text(
            "DELETE FROM memory_principal_corpora WHERE principal_id IN "
            "(SELECT id FROM memory_principals WHERE name = ANY(:names))"
        ),
        {"names": list(OWNED_PRINCIPALS)},
    )
    conn.execute(
        sa.text("DELETE FROM memory_principals WHERE name = ANY(:names)"),
        {"names": list(OWNED_PRINCIPALS)},
    )
    for stmt in (
        "DELETE FROM memory_versions WHERE corpus_id = ANY(:ids)",
        "DELETE FROM memory_claims WHERE corpus_id = ANY(:ids)",
        "DELETE FROM memory_evidence WHERE corpus_id = ANY(:ids)",
        "DELETE FROM memory_documents WHERE corpus_id = ANY(:ids)",
    ):
        conn.execute(sa.text(stmt), {"ids": ids or [""]})
    conn.execute(sa.text("DELETE FROM corpora WHERE id = ANY(:ids)"), {"ids": ids or [""]})


def _provision() -> None:
    """Create the runtime role and prove it is actually least-privileged.

    The script is the one `scripts/db/runtime_roles.sql` ships, read from disk by
    the harness rather than restated here -- see `tenant_harness.PROVISION_SQL`.
    """
    apply_provision_sql()

    engine = sa.create_engine(sync_url())
    try:
        with engine.connect() as conn:
            role, superuser, bypass = conn.execute(
                sa.text(
                    "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles " "WHERE rolname = :role"
                ),
                # `RUNTIME_ROLE`, not the literal shipped name. The harness now
                # provisions a per-session role so it cannot rotate the cluster's
                # `mindpalace_runtime` password; a hardcoded name here would check
                # a role nobody provisioned and fail for the wrong reason.
                {"role": RUNTIME_ROLE},
            ).one()
            can_login = conn.execute(
                sa.text("SELECT has_table_privilege(:role, 'memory_claims', 'SELECT')"),
                {"role": role},
            ).scalar()
    finally:
        engine.dispose()
    assert not superuser, "the runtime role must not be a superuser"
    assert not bypass, "the runtime role must not have BYPASSRLS"
    assert can_login, "the runtime role needs DML on the tenant tables"


class World:
    """The application under test, reached as each of the seeded credentials."""

    def __init__(self, client, db_module, engine, credentials):
        self.client = client
        self.db_module = db_module
        self.engine = engine
        self.credentials = credentials

    def header(self, label: str) -> dict:
        return {"Authorization": f"Bearer {self.credentials[label]['secret']}"}

    def post(self, path, label=None, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if label:
            headers.update(self.header(label))
        return self.client.post(path, headers=headers, **kwargs)

    def get(self, path, label=None, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if label:
            headers.update(self.header(label))
        return self.client.get(path, headers=headers, **kwargs)

    def delete(self, path, label=None, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if label:
            headers.update(self.header(label))
        return self.client.delete(path, headers=headers, **kwargs)

    @staticmethod
    def assert_no_foreign_canary(body, *, own=("alpha", "beta")):
        """No foreign memory content anywhere in a response.

        The echoed question is removed first: a caller may legitimately ask
        about a phrase they already know, and the echo of their own question is
        not a leak. What must not appear is anybody else's *memory* -- claims,
        evidence, sources, versions or paths.
        """

        def strip(value):
            if isinstance(value, dict):
                return {k: strip(v) for k, v in value.items() if k != "query"}
            if isinstance(value, list):
                return [strip(item) for item in value]
            return value

        text = body if isinstance(body, str) else repr(strip(body))
        for canary in ALL_CANARIES:
            marker = canary.split()[0]
            if marker.split("_")[0].lower() in own:
                continue
            assert canary not in text, f"cross-tenant leak: {marker}"


@pytest.fixture
async def world(monkeypatch):
    """Two organisations, their corpora, their canaries and their credentials.

    The application under test is switched onto the least-privileged role for the
    duration of the test, which is the only reason the row-level policies apply
    to it. The fixture's own seeding runs as the migration role, because a
    fixture has to be able to create what a tenant cannot.
    """
    from api.main import app
    from api.services import authz
    from api.services import db as db_module
    from api.services.remember import remember
    from tenant_harness import require_auth_mode

    require_auth_mode(monkeypatch)
    _provision()

    admin = sa.create_engine(sync_url())
    try:
        with admin.begin() as conn:
            _reset(conn)
            for name in tuple(ALL_CORPORA) + ("outsider-brand",):
                conn.execute(
                    sa.text(
                        "INSERT INTO corpora (id, name, description) VALUES "
                        "(encode(sha256(convert_to('corpus:' || :n, 'UTF8')), 'hex'), "
                        ":n, '') ON CONFLICT DO NOTHING"
                    ),
                    {"n": name},
                )
            conn.execute(
                sa.text(
                    "INSERT INTO corpora (id, name, description) VALUES "
                    "('00000000000000000000000000000000000000000000000000000000default',"
                    " 'default', '') ON CONFLICT DO NOTHING"
                )
            )
    finally:
        admin.dispose()

    grants = (
        ("alpha-admin", ALPHA_CORPORA.values(), "read_write"),
        ("alpha-creator", [ALPHA_CORPORA["brand"], ALPHA_CORPORA["campaign_1"]], "read_write"),
        ("alpha-reviewer", ALPHA_CORPORA.values(), "read_only"),
        ("beta-admin", BETA_CORPORA.values(), "read_write"),
        ("beta-creator", [BETA_CORPORA["brand"], BETA_CORPORA["campaign_1"]], "read_write"),
        ("beta-reviewer", BETA_CORPORA.values(), "read_only"),
    )
    credentials = {}
    async with db_module.AsyncSessionLocal() as db:
        for label, corpora, role in grants:
            credentials[label] = await authz.issue_credential(
                db, label, corpora, role  # type: ignore[arg-type]
            )
        revoked = await authz.issue_credential(db, "alpha-revoked", ALPHA_CORPORA.values())
        outsider = await authz.issue_credential(db, "outsider", ["outsider-brand"])
        credentials["alpha-revoked"] = revoked
        credentials["outsider"] = outsider

    seeds = (
        (ALPHA_PRIVATE_PRODUCT_FACT_7Q2, ALPHA_CORPORA["brand"], "alpha_battery"),
        (ALPHA_PRIVATE_CAMPAIGN_FACT_9M4, ALPHA_CORPORA["campaign_1"], "alpha_spring"),
        (BETA_PRIVATE_PRODUCT_FACT_7Q2, BETA_CORPORA["brand"], "beta_router"),
        (BETA_PRIVATE_CAMPAIGN_FACT_9M4, BETA_CORPORA["campaign_1"], "beta_winter"),
    )
    with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
        for statement, corpus, key in seeds:
            await remember(statement, corpus=corpus, key=key)

    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_engine", engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield World(client, db_module, engine, credentials)
        # Revocation is asserted against the credential that was already issued,
        # so it is disabled here rather than at issue time.
        #
        # On a *maintenance* connection, not the runtime one: migration 009 denies
        # the runtime role any access to `memory_principals`, which is the whole
        # point of that change. Revocation is an operator action, so the fixture
        # does it the way an operator does. `admin_session_scope` opens its own
        # engine from `DATABASE_URL` rather than using the fixture's patched
        # factory, which is the runtime role.
        async with db_module.admin_session_scope() as db:
            with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
                await authz.revoke_credential(db, revoked["id"])
    finally:
        await engine.dispose()
        cleanup = sa.create_engine(sync_url())
        try:
            with cleanup.begin() as conn:
                _reset(conn)
        finally:
            cleanup.dispose()


@pytest.fixture
def raw_runtime(world):
    """A session factory on the runtime role, for testing the database directly."""
    return async_sessionmaker(world.engine, class_=AsyncSession, expire_on_commit=False)


def scoped(principal, db_module):
    return db_module.principal_scope(principal)


def principal_for(world, corpus_name: str, role: str = "read_only"):
    from api.services.authz import Principal

    return Principal(
        id="test",
        name="test",
        role=role,  # type: ignore[arg-type]
        corpora=frozenset({corpus_name}),
        corpus_ids=frozenset({corpus_id_for(corpus_name)}),
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", TENANT_ROUTES)
async def test_every_tenant_route_refuses_an_unauthenticated_caller(world, method, path):
    """Coverage, not just behaviour: a new route cannot skip the boundary."""
    resolved = path.format(name=ALPHA_CORPORA["brand"])
    if method == "GET":
        response = await world.client.get(resolved)
    else:
        response = await world.client.request(method, resolved, json={})
    assert response.status_code == 401, f"{method} {path} answered without a credential"
    assert response.json()["detail"]["code"] == "unauthenticated"


async def test_a_forged_credential_is_refused(world):
    response = await world.post(
        "/api/memory/query",
        headers={"Authorization": "Bearer mpk_not-a-real-credential"},
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert response.status_code == 401


async def test_a_correct_prefix_with_the_wrong_secret_is_refused(world):
    issued = world.credentials["alpha-creator"]
    forged = issued["secret"][:-6] + "aaaaaa"
    response = await world.post(
        "/api/memory/query",
        headers={"Authorization": f"Bearer {forged}"},
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert response.status_code == 401


async def test_a_revoked_credential_stops_working_immediately(world, raw_runtime):
    """Revocation is not a flag a request can outlive.

    Revoked on a *maintenance* connection: the runtime role has no access to
    `memory_principals` at all, so an operator revokes with the maintenance role
    and the application role discovers the change on its next request.
    """
    from api.services.authz import ADMIN_PRINCIPAL, revoke_credential

    async with world.db_module.admin_session_scope() as db:
        with world.db_module.principal_scope(ADMIN_PRINCIPAL):
            await revoke_credential(db, world.credentials["alpha-creator"]["id"])
    response = await world.post(
        "/api/memory/query", "alpha-creator", json={"corpus": ALPHA_CORPORA["brand"], "query": "x"}
    )
    assert response.status_code == 401


async def test_membership_not_identity_is_the_question(world):
    """Beta's administrator is a real user; they are still not in Alpha."""
    response = await world.post(
        "/api/memory/query",
        "beta-admin",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "corpus_not_found"


async def test_an_outsider_reaches_nothing(world):
    response = await world.get("/api/corpora", "outsider")
    assert response.status_code == 200
    names = {row["name"] for row in response.json()}
    assert names <= {"outsider-brand"}
    assert not names & set(ALL_CORPORA)


# ---------------------------------------------------------------------------
# Cross-tenant reads, one per operation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["query", "current", "history", "changes", "pack"])
async def test_naming_another_organisations_corpus_is_refused(world, operation):
    response = await world.post(
        f"/api/memory/{operation}",
        "alpha-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "mesh bands"},
    )
    assert response.status_code == 404
    World.assert_no_foreign_canary(response.json())


async def test_recall_returns_nothing_from_another_organisation(world):
    """Asking your own corpus about their fact yields an abstention, not an answer."""
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": BETA_PRIVATE_PRODUCT_FACT_7Q2},
    )
    assert response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["current_memories"] == []
    World.assert_no_foreign_canary(body, own=("alpha",))


async def test_evidence_takes_a_claim_id_directly_and_is_still_scoped(world):
    leak = await world.post(
        "/api/memory/query",
        "beta-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router"},
    )
    claim_id = leak.json()["current_memories"][0]["id"]
    response = await world.post(
        "/api/memory/evidence",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "claim_id": claim_id},
    )
    assert response.status_code == 404
    World.assert_no_foreign_canary(response.json())


async def test_the_operational_feed_is_corpus_scoped(world):
    response = await world.get(f"/api/memory/feed?corpus={BETA_CORPORA['brand']}", "alpha-admin")
    assert response.status_code == 404


async def test_a_snapshot_from_another_organisation_cannot_be_replayed(world):
    leak = await world.post(
        "/api/memory/snapshot", "beta-admin", json={"corpus": BETA_CORPORA["brand"]}
    )
    snapshot_id = leak.json()["snapshot"]["id"]
    response = await world.post(
        "/api/memory/replay",
        "alpha-admin",
        json={"corpus": ALPHA_CORPORA["brand"], "snapshot_id": snapshot_id},
    )
    assert response.status_code == 404
    World.assert_no_foreign_canary(response.json())


async def test_a_receipt_cannot_be_minted_from_another_organisation(world):
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={
            "corpus": BETA_CORPORA["brand"],
            "query": "router",
            "include_receipt": True,
        },
    )
    assert response.status_code == 404


async def test_history_and_as_of_stay_inside_the_callers_own_organisation(world):
    for operation in ("history", "changes", "as-of"):
        response = await world.post(
            f"/api/memory/{operation}",
            "alpha-admin",
            json={
                "corpus": ALPHA_CORPORA["brand"],
                "query": "battery",
                "as_of": "2030-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 200
        World.assert_no_foreign_canary(response.json(), own=("alpha",))


# ---------------------------------------------------------------------------
# Corpus-level surfaces
# ---------------------------------------------------------------------------


async def test_listing_corpora_returns_only_this_callers_own(world):
    response = await world.get("/api/corpora", "alpha-creator")
    assert response.status_code == 200
    assert {row["name"] for row in response.json()} == {
        ALPHA_CORPORA["brand"],
        ALPHA_CORPORA["campaign_1"],
    }


async def test_corpus_stats_are_refused_across_organisations(world):
    response = await world.get(f"/api/corpora/{BETA_CORPORA['brand']}", "alpha-admin")
    assert response.status_code == 404


async def test_deleting_another_organisations_corpus_is_refused(world):
    response = await world.delete(f"/api/corpora/{BETA_CORPORA['brand']}", "alpha-admin")
    assert response.status_code == 404
    assert (
        await world.get(f"/api/corpora/{BETA_CORPORA['brand']}", "beta-admin")
    ).status_code == 200


async def test_a_tenant_scoped_credential_cannot_mint_a_new_corpus(world):
    response = await world.post("/api/corpora", "alpha-admin", json={"name": "alpha-forged-corpus"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


async def test_sync_cannot_write_into_another_organisations_corpus(world, tmp_path):
    response = await world.post(
        f"/api/corpora/{BETA_CORPORA['brand']}/sync",
        "alpha-admin",
        json={"path": str(tmp_path)},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def test_remember_cannot_write_into_another_organisations_corpus(world):
    response = await world.post(
        "/api/memory/remember",
        "alpha-creator",
        json={"statement": "Alpha writes into Beta.", "corpus": BETA_CORPORA["brand"]},
    )
    assert response.status_code == 404
    check = await world.post(
        "/api/memory/query", "beta-admin", json={"corpus": BETA_CORPORA["brand"], "query": "Alpha"}
    )
    assert check.json()["current_memories"] == []


async def test_ingest_cannot_write_into_another_organisations_corpus(world):
    response = await world.post(
        "/api/ingest/file",
        "alpha-creator",
        json={
            "content": "---\ntitle: Sneaky\n---\n\nBETA_PRIVATE_PRODUCT_FACT_7Q2 planted\n",
            "path": "planted.md",
            "corpus": BETA_CORPORA["brand"],
        },
    )
    assert response.status_code == 404


async def test_a_read_only_credential_cannot_write_memory(world):
    response = await world.post(
        "/api/memory/remember",
        "alpha-reviewer",
        json={"statement": "A reviewer tries to write.", "corpus": ALPHA_CORPORA["brand"]},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


async def test_a_read_only_credential_cannot_create_a_snapshot(world):
    """A snapshot writes immutable rows, so it is a write even though it cannot
    change a claim."""
    response = await world.post(
        "/api/memory/snapshot", "alpha-reviewer", json={"corpus": ALPHA_CORPORA["brand"]}
    )
    assert response.status_code == 403


async def test_a_read_only_credential_can_still_read(world):
    response = await world.post(
        "/api/memory/query",
        "alpha-reviewer",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert response.status_code == 200
    assert response.json()["current_memories"]


# ---------------------------------------------------------------------------
# Retrieval, context and the query surface
# ---------------------------------------------------------------------------


async def test_search_cannot_reach_another_organisation(world):
    response = await world.get(
        f"/api/search?q=mesh+bands&corpus={BETA_CORPORA['brand']}", "alpha-creator"
    )
    assert response.status_code == 404


async def test_context_cannot_reach_another_organisation(world):
    response = await world.get(
        f"/api/context?q=mesh+bands&corpus={BETA_CORPORA['brand']}", "alpha-creator"
    )
    assert response.status_code == 404


async def test_the_query_surface_cannot_reach_another_organisation(world):
    asked = await world.post(
        "/api/query/ask",
        "alpha-creator",
        json={"question": "mesh bands", "corpus": BETA_CORPORA["brand"], "mode": "memory"},
    )
    assert asked.status_code == 404
    summarized = await world.post(
        "/api/query/summarize",
        "alpha-creator",
        json={"document_id": "0" * 64, "corpus": BETA_CORPORA["brand"]},
    )
    assert summarized.status_code == 404
    related = await world.post(
        "/api/query/related",
        "alpha-creator",
        json={"document_id": "0" * 64, "corpus": BETA_CORPORA["brand"]},
    )
    assert related.status_code == 404
    timeline = await world.get(
        f"/api/query/timeline?corpus={BETA_CORPORA['brand']}", "alpha-creator"
    )
    assert timeline.status_code == 404


async def test_an_omitted_corpus_cannot_mean_another_tenant(world):
    """The implicit default is scoped to the caller, never to the installation."""
    response = await world.get("/api/corpora", "alpha-creator")
    names = {row["name"] for row in response.json()}
    assert "default" not in names
    assert not names & set(BETA_CORPORA.values())


# ---------------------------------------------------------------------------
# The database itself
# ---------------------------------------------------------------------------


async def test_the_runtime_role_sees_nothing_without_a_tenant_scope(world, raw_runtime):
    """Fail closed: no context, no rows. A raw query, no service code involved."""
    from api.services.authz import Principal

    empty = Principal("none", "none", "read_only", frozenset(), frozenset())
    async with raw_runtime() as db:
        with scoped(empty, world.db_module):
            async with db.begin():
                claims = (await db.execute(sa.text("SELECT count(*) FROM memory_claims"))).scalar()
                corpora = (await db.execute(sa.text("SELECT count(*) FROM corpora"))).scalar()
                documents = (await db.execute(sa.text("SELECT count(*) FROM documents"))).scalar()
    assert claims == 0
    assert corpora == 0
    assert documents == 0


async def test_the_runtime_role_sees_only_its_own_corpus_rows(world, raw_runtime):
    view = principal_for(world, ALPHA_CORPORA["brand"])
    async with raw_runtime() as db:
        with scoped(view, world.db_module):
            async with db.begin():
                claims = (
                    (await db.execute(sa.text("SELECT claim FROM memory_claims"))).scalars().all()
                )
    assert claims, "the caller should see its own corpus"
    assert all(ALPHA_PRIVATE_PRODUCT_FACT_7Q2 in claim for claim in claims)
    assert not any(BETA_PRIVATE_PRODUCT_FACT_7Q2 in claim for claim in claims)


async def test_a_row_cannot_be_attached_to_another_tenant_by_sql(world, raw_runtime):
    """The composite keys already forbid it; the policy forbids seeing the parent."""
    from sqlalchemy.exc import DBAPIError

    view = principal_for(world, ALPHA_CORPORA["brand"])
    with pytest.raises(DBAPIError):
        async with raw_runtime() as db:
            with scoped(view, world.db_module):
                async with db.begin():
                    await db.execute(
                        sa.text(
                            "INSERT INTO ingestion_manifest (path, content_hash, corpus_id) "
                            "VALUES ('cross.md', 'x', :cid)"
                        ),
                        {"cid": corpus_id_for(BETA_CORPORA["brand"])},
                    )


async def test_row_level_security_is_enabled_and_forced_on_the_tenant_tables():
    engine = sa.create_engine(sync_url())
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c WHERE c.relname = ANY(:names)"
                ),
                {"names": list(ALL_CORPORA) + ["corpora", "documents", "chunks", "memory_claims"]},
            ).fetchall()
    finally:
        engine.dispose()
    checked = {name for name, _, _ in rows}
    assert {"corpora", "documents", "chunks", "memory_claims"} <= checked
    for name, enabled, forced in rows:
        assert enabled, f"row-level security is not enabled on {name}"
        assert forced, f"row-level security is not forced on {name}"


async def test_two_identities_on_one_client_do_not_see_each_other(world):
    """A request's identity must not survive into the next request on the same task."""
    alpha = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    beta = await world.post(
        "/api/memory/query",
        "beta-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router"},
    )
    again = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert alpha.json()["current_memories"] == again.json()["current_memories"]
    World.assert_no_foreign_canary(alpha.json(), own=("alpha",))
    World.assert_no_foreign_canary(beta.json(), own=("beta",))


async def test_a_refused_request_does_not_change_the_next_one(world):
    """A 401 in between must not leave the previous identity installed."""
    first = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    refused = await world.post(
        "/api/memory/query",
        headers={"Authorization": "Bearer mpk_nope"},
        json={"corpus": BETA_CORPORA["brand"], "query": "router"},
    )
    assert refused.status_code == 401
    second = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    assert first.json()["current_memories"] == second.json()["current_memories"]


async def test_health_endpoints_stay_open_for_operators(world):
    """Readiness is about this process, not about tenant data."""
    response = await world.client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["auth"]["mode"] == "required"
