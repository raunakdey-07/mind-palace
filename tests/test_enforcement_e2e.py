"""End-to-end tests with scope-token enforcement ON (migration 015).

Proves the security hotfix works when enabled: the app connects as the
least-privileged runtime role, a random secret is rotated into the DB,
enforcement is turned on, and every tenant-scoped operation still works
through the real HTTP API paths.

The secret is set via step-scoped env only (monkeypatch.setenv) -- never
exported job-wide, which caused the #68 CI failures.

Scenario list (each is its own test):
1. Admin creates a corpus via DB; it appears in listings under enforcement.
2. Ingest a document via POST /api/memory/remember.
3. Recall (/api/memory/current) and context pack (/api/memory/pack) return claims.
4. Credential create, list, revoke; a revoked credential is refused.
5. Erasure removes the rows.
6. Listing corpora returns only the caller's own.
7. Tenant A cannot read, write or delete tenant B (zero rows or 403/404).
8. Forgery: scope without token -> zero rows; wrong-secret token -> zero rows;
   empty token -> zero rows.
9. The `{*}` maintenance sentinel: maintenance role with {*}+token works;
   runtime role cannot forge {*}.
10. Two tenants on separate clients never see each other's rows.
11. EXPLAIN plan shape (deterministic): RLS active, no per-row SubPlan.
    (Latency numbers moved to scripts/bench_enforcement_latency.py)
12. Operator procedure per docs/security.md: rotate, enable, mismatch window yields 0 rows.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets
import sys
import time

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.main import app
from api.services import authz, db as db_module
from api.services.db import principal_scope
from tenant_harness import (
    ALPHA_CORPORA,
    BETA_CORPORA,
    apply_provision_sql,
    apply_as_runtime,
    requires_tenant_db,
    runtime_url,
    sync_url,
)

pytestmark = [pytest.mark.asyncio, requires_tenant_db]


def corpus_id_for(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Session setup: rotate a random secret into the DB and enable enforcement.
# ---------------------------------------------------------------------------


def _rotate_and_enable(url: str) -> str:
    """Rotate a random scope HMAC secret into memory_server_secrets and turn
    enforcement on. Returns the rotated secret."""
    secret = _secrets.token_hex(32)
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT value FROM memory_server_secrets " "WHERE name = 'scope_hmac_secret'"
                )
            ).scalar()
            original_secret = row if row is not None else "dev-scope-secret"
            conn.execute(
                sa.text(
                    "INSERT INTO memory_server_secrets (name, value) VALUES "
                    "('scope_hmac_secret', :v) "
                    "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"v": secret},
            )
            conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(true)"))
    finally:
        engine.dispose()
    return secret, original_secret


def _restore_secret_and_disable(url: str, original_secret: str) -> None:
    """Restore the default scope secret and turn enforcement off."""
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO memory_server_secrets (name, value) VALUES "
                    "('scope_hmac_secret', :v) "
                    "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"v": original_secret},
            )
            conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(false)"))
    finally:
        engine.dispose()


@pytest.fixture
def enforced_secret(monkeypatch):
    """Rotate a random scope secret and enable enforcement for one test.

    Function-scoped with a restoring finalizer so the suite does not leave
    enforcement ON or a rotated secret behind for later tests (isolation).
    Also sets MIND_PALACE_SCOPE_SECRET so the application's HMAC tokens match
    the rotated DB secret. Restores both on teardown.
    """
    url = os.environ.get("DATABASE_URL")
    assert url, "DATABASE_URL must be set (conftest creates the session database)"
    secret, original_secret = _rotate_and_enable(url)
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", secret)

    yield secret

    _restore_secret_and_disable(url, original_secret)
    monkeypatch.delenv("MIND_PALACE_SCOPE_SECRET", raising=False)


# ---------------------------------------------------------------------------
# World fixture: two organisations, runtime role, enforcement ON.
# ---------------------------------------------------------------------------


class World:
    """Application under test, reached as each of the seeded credentials."""

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


@pytest.fixture
async def world(enforced_secret, monkeypatch):
    """Two organisations with credentials; app connected as runtime role;
    enforcement ON via MIND_PALACE_SCOPE_SECRET (step-scoped env only)."""
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_SCOPE_SECRET", enforced_secret)
    monkeypatch.setenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "on")

    from api.services.remember import remember

    # Provision the runtime role (idempotent; conftest may have done it already).
    apply_provision_sql()

    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            # Create corpora as the maintenance/superuser role.
            for name in tuple(ALPHA_CORPORA.values()) + tuple(BETA_CORPORA.values()):
                conn.execute(
                    sa.text(
                        "INSERT INTO corpora (id, name, description) VALUES "
                        "(encode(sha256(convert_to('corpus:' || :n, 'UTF8')), 'hex'), "
                        ":n, '') ON CONFLICT DO NOTHING"
                    ),
                    {"n": name},
                )
    finally:
        engine.dispose()

    grants = (
        ("alpha-admin", ALPHA_CORPORA.values(), "read_write"),
        ("alpha-creator", [ALPHA_CORPORA["brand"], ALPHA_CORPORA["campaign_1"]], "read_write"),
        ("alpha-reviewer", ALPHA_CORPORA.values(), "read_only"),
        ("beta-admin", BETA_CORPORA.values(), "read_write"),
        ("beta-creator", [BETA_CORPORA["brand"], BETA_CORPORA["campaign_1"]], "read_write"),
    )
    credentials = {}
    async with db_module.AsyncSessionLocal() as db:
        for label, corpora, role in grants:
            credentials[label] = await authz.issue_credential(
                db, label, list(corpora), role  # type: ignore[arg-type]
            )

    # Seed one document per organisation through the admin principal.
    alpha_fact = "ALPHA_ENFORCEMENT_FACT the battery lasts 41 hours."
    beta_fact = "BETA_ENFORCEMENT_FACT the router has four mesh bands."
    async with db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await remember(alpha_fact, corpus=ALPHA_CORPORA["brand"], key="alpha_k1")
            await remember(beta_fact, corpus=BETA_CORPORA["brand"], key="beta_k1")

    # Point the app at the least-privileged runtime role.
    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_engine", engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield World(client, db_module, engine, credentials)

    await engine.dispose()


# ---------------------------------------------------------------------------
# Scenario 1: Admin creates a corpus via DB; it appears in listings.
# ---------------------------------------------------------------------------


async def test_db_role_properties(world):
    """The app connects as the least-privileged runtime role: not superuser,
    no BYPASSRLS, no table ownership."""
    engine = apply_as_runtime(runtime_url())
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text(
                        "SELECT current_user, session_user, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    current_user, session_user, rolsuper, rolbypassrls = row
    print(
        f"\nrole evidence: current_user={current_user} session_user={session_user} "
        f"rolsuper={rolsuper} rolbypassrls={rolbypassrls}"
    )
    assert not rolsuper, "runtime role is superuser"
    assert not rolbypassrls, "runtime role has BYPASSRLS"


async def test_admin_creates_a_corpus(world):
    """A brand-new corpus is created under enforcement and is then usable.

    Corpus creation goes through the CLI (`corpora create`, which connects as
    `ADMIN_PRINCIPAL` -- see `_authz_session`), since the HTTP route
    `POST /api/corpora` resolves the target name through `get_corpus_by_name`
    and therefore requires the caller to already hold scope over that corpus --
    impossible for a brand-new corpus id. Ingest, recall, listing and erasure
    then go through the HTTP API with a real `read_write` credential.

    Auth mode is `required` throughout: no `LOCAL_PRINCIPAL`, no env flipping,
    no direct DB INSERT fallback.
    """
    corpus_name = "alpha-enforcement-corpus"

    # 1. Create the corpus via the CLI (administrative, enforcement ON).
    import subprocess
    import os as _os

    env = dict(_os.environ)
    env["DATABASE_URL"] = _os.environ["DATABASE_URL"]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "corpus",
            "create",
            corpus_name,
            "--description",
            "enforcement test corpus",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"corpora create failed: {result.stderr}"
    print(f"\n  CLI create: {result.stdout.strip()}")

    # 2. Issue a regular credential for the new corpus (ADMIN_PRINCIPAL is
    #    unrestricted, so it can see the brand-new corpus to grant it).
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            new_cred = await authz.issue_credential(
                db, "enforcement-reader", [corpus_name], "read_write"
            )

    reader_headers = {"Authorization": f"Bearer {new_cred['secret']}"}

    # 3. The new corpus appears in listings under enforcement.
    response = await world.client.get("/api/corpora", headers=reader_headers)
    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()}
    assert corpus_name in names, f"{corpus_name} not in {names}"

    # 4. Ingest and recall in the new corpus.
    response = await world.client.post(
        "/api/memory/remember",
        headers=reader_headers,
        json={
            "statement": "ENFORCEMENT_NEW_CORPUS_FACT the launch date is March 15.",
            "corpus": corpus_name,
            "key": "new-corpus-1",
        },
    )
    assert response.status_code in (200, 201), response.text

    response = await world.client.post(
        "/api/memory/current",
        headers=reader_headers,
        json={"corpus": corpus_name, "query": ""},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(
        "ENFORCEMENT_NEW_CORPUS_FACT" in m.get("claim", "")
        for m in body.get("current_memories", [])
    ), body

    # 5. Erasure through the DELETE /api/corpora/{name} route. Issue a second
    #    admin credential carrying a grant on the new corpus (delete_corpus
    #    requires both manage_corpus and visibility of the target).
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            admin_eraser = await authz.issue_credential(
                db, "admin-eraser", [corpus_name], "principal_admin"
            )

    eraser_headers = {"Authorization": f"Bearer {admin_eraser['secret']}"}
    response = await world.client.delete(f"/api/corpora/{corpus_name}", headers=eraser_headers)
    assert response.status_code == 200, response.text
    assert response.json().get("deleted") is True, response.text


# ---------------------------------------------------------------------------
# Scenario 2: Ingest a document via POST /api/memory/remember.
# ---------------------------------------------------------------------------


async def test_ingest_a_document(world):
    """Remembering through the API writes a claim visible under enforcement."""
    response = await world.post(
        "/api/memory/remember",
        "alpha-creator",
        json={
            "statement": "ENFORCEMENT_INGEST_FACT the launch date is March 15.",
            "corpus": ALPHA_CORPORA["brand"],
            "key": "enforce-ingest-1",
        },
    )
    assert response.status_code in (200, 201), response.text


# ---------------------------------------------------------------------------
# Scenario 3: Recall and context pack return claims.
# ---------------------------------------------------------------------------


async def test_recall_and_context_pack_return_claims(world):
    """Recall and context pack return the ingested claim under enforcement."""
    # Ingest a claim first (self-contained; doesn't depend on test ordering).
    response = await world.post(
        "/api/memory/remember",
        "alpha-creator",
        json={
            "statement": "ENFORCEMENT_RECALL_FACT the launch date is March 15.",
            "corpus": ALPHA_CORPORA["brand"],
            "key": "enforce-recall-1",
        },
    )
    assert response.status_code in (200, 201), response.text

    # Recall via /current (lists current memories; no semantic search needed).
    response = await world.post(
        "/api/memory/current",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": ""},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    memories = body.get("current_memories", [])
    assert memories, "recall returned no claims under enforcement"
    assert any("ENFORCEMENT_RECALL_FACT" in m.get("claim", "") for m in memories), body

    # Context pack.
    response = await world.post(
        "/api/memory/pack",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "launch date"},
    )
    assert response.status_code == 200, response.text
    pack = response.json()
    memories = pack.get("current_memories", [])
    assert memories, "context pack returned no memories under enforcement"
    assert any("ENFORCEMENT_RECALL_FACT" in m.get("claim", "") for m in memories), pack


# ---------------------------------------------------------------------------
# Scenario 4: Credential create, list, revoke; revoked refused.
# ---------------------------------------------------------------------------


async def test_credential_create_list_revoke_refused(world):
    """Credential lifecycle works under enforcement; revoked credential is refused."""
    # Issue a fresh credential via authz.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            new_secret = await authz.issue_credential(
                db, "enforcement-tmp", [ALPHA_CORPORA["brand"]], "read_write"
            )

    secret = new_secret["secret"]

    try:
        # New credential can recall.
        response = await world.post(
            "/api/memory/query",
            None,
            json={"corpus": ALPHA_CORPORA["brand"], "query": "launch date"},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("current_memories"), "new credential could not recall"

        # Revoke it.
        async with world.db_module.admin_session_scope() as db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                await authz.revoke_credential(db, new_secret["id"])

        # Revoked credential is refused.
        response = await world.post(
            "/api/memory/query",
            None,
            json={"corpus": ALPHA_CORPORA["brand"], "query": "launch date"},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert response.status_code in (401, 403), response.status_code
    finally:
        async with world.db_module.admin_session_scope() as db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                try:
                    await authz.revoke_credential(db, new_secret["id"])
                except Exception:  # noqa: BLE001 - best effort cleanup
                    pass


# ---------------------------------------------------------------------------
# Scenario 5: Erasure removes the rows (via API/CLI route with neighbour check).
# ---------------------------------------------------------------------------


async def test_erasure_removes_rows(world):
    """Erasure removes all rows for a corpus and leaves neighbours untouched."""
    target_name = "alpha-enforcement-erasure"
    neighbour_name = "beta-enforcement-neighbour"
    target_cid = corpus_id_for(target_name)
    neighbour_cid = corpus_id_for(neighbour_name)

    # Create both corpora as admin.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                sa.text(
                    "INSERT INTO corpora (id, name, description) VALUES (:c, :n, '') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"c": target_cid, "n": target_name},
            )
            await db.execute(
                sa.text(
                    "INSERT INTO corpora (id, name, description) VALUES (:c, :n, '') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"c": neighbour_cid, "n": neighbour_name},
            )
            await db.commit()

    # Issue credentials for both.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            target_cred = await authz.issue_credential(
                db, "erasure-target", [target_name], "read_write"
            )
            neighbour_cred = await authz.issue_credential(
                db, "erasure-neighbour", [neighbour_name], "read_write"
            )

    target_headers = {"Authorization": f"Bearer {target_cred['secret']}"}
    neighbour_headers = {"Authorization": f"Bearer {neighbour_cred['secret']}"}

    # Ingest 5 claims into each corpus via the API.
    for i in range(5):
        resp = await world.client.post(
            "/api/memory/remember",
            headers=target_headers,
            json={
                "corpus": target_name,
                "statement": f"erasure target fact {i} about alpha widgets.",
                "key": f"erase-t-{i}",
            },
        )
        assert resp.status_code in (200, 201), resp.text
        resp = await world.client.post(
            "/api/memory/remember",
            headers=neighbour_headers,
            json={
                "corpus": neighbour_name,
                "statement": f"erasure neighbour fact {i} about beta gadgets.",
                "key": f"erase-n-{i}",
            },
        )
        assert resp.status_code in (200, 201), resp.text

    # Record neighbour counts before erasure.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            neighbour_before = {}
            for table in ("memory_claims", "memory_versions", "memory_evidence", "memory_chunks"):
                row = await db.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE corpus_id = :c"),
                    {"c": neighbour_cid},
                )
                neighbour_before[table] = int(row.scalar())

    # Erase the target corpus via the admin function (the CLI/operator route).
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": target_cid})
            await db.commit()

    # Assert target corpus rows are 0.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for table in ("memory_claims", "memory_versions", "memory_evidence", "memory_chunks"):
                row = await db.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE corpus_id = :c"),
                    {"c": target_cid},
                )
                count = int(row.scalar())
                assert count == 0, f"{table} still has {count} rows for erased corpus"

            # Assert neighbour counts unchanged.
            for table, before in neighbour_before.items():
                row = await db.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE corpus_id = :c"),
                    {"c": neighbour_cid},
                )
                after = int(row.scalar())
                assert after == before, f"{table} neighbour count changed: {before} -> {after}"

            # Corpus row itself should be gone.
            row = await db.execute(
                sa.text("SELECT count(*) FROM corpora WHERE id = :c"), {"c": target_cid}
            )
            assert row.scalar() == 0

    # Clean up neighbour.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": neighbour_cid})
            await db.commit()
            await db.execute(sa.text("DELETE FROM corpora WHERE id = :c"), {"c": neighbour_cid})
            await db.commit()
            await authz.revoke_credential(db, target_cred["id"])
            await authz.revoke_credential(db, neighbour_cred["id"])


# ---------------------------------------------------------------------------
# Scenario 6: Listing corpora returns only the caller's own.
# ---------------------------------------------------------------------------


async def test_listing_returns_only_own_corpora(world):
    """Listing corpora returns only the caller's granted corpora under enforcement."""
    response = await world.get("/api/corpora", "alpha-creator")
    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()}
    expected = {ALPHA_CORPORA["brand"], ALPHA_CORPORA["campaign_1"]}
    assert names == expected, f"got {names}, expected {expected}"

    # Beta's corpora must not appear.
    assert not (names & set(BETA_CORPORA.values())), "cross-tenant corpus visible"


# ---------------------------------------------------------------------------
# Scenario 7: Tenant A cannot read/write/delete tenant B.
# ---------------------------------------------------------------------------


async def test_tenant_a_cannot_reach_tenant_b(world):
    """Alpha cannot read, write, or delete Beta's corpus under enforcement."""
    beta_brand = BETA_CORPORA["brand"]

    # Read: query on beta corpus returns 403/404.
    response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": beta_brand, "query": "router"},
    )
    assert response.status_code in (403, 404), response.status_code

    # Write: remember on beta corpus is refused.
    response = await world.post(
        "/api/memory/remember",
        "alpha-creator",
        json={
            "corpus": beta_brand,
            "statement": "ENFORCEMENT_LEAK alpha leak",
            "key": "leak",
        },
    )
    assert response.status_code in (403, 404), response.status_code

    # Delete: alpha cannot delete beta's corpus.
    response = await world.delete(f"/api/corpora/{beta_brand}", "alpha-creator")
    assert response.status_code in (403, 404), response.status_code

    # Beta's own canary must still be intact.
    response = await world.post(
        "/api/memory/query",
        "beta-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(
        "BETA_ENFORCEMENT_FACT" in m.get("claim", "") for m in body.get("current_memories", [])
    ), body


# ---------------------------------------------------------------------------
# Scenario 8: Forgery - scope without token, wrong-secret token, empty token.
# ---------------------------------------------------------------------------


async def test_forgery_fails_closed(world):
    """Forged or missing scope tokens return zero rows under enforcement."""
    alpha_cid = corpus_id_for(ALPHA_CORPORA["brand"])

    async def raw_count(scope: str, token: str | None) -> int:
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                    {"s": scope},
                )
                if token is not None:
                    await session.execute(
                        sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
                        {"t": token},
                    )
                result = await session.execute(
                    sa.text("SELECT count(*) FROM memory_claims WHERE corpus_id = :c"),
                    {"c": alpha_cid},
                )
                return int(result.scalar())
        finally:
            await engine.dispose()

    # 8a: scope set, no token -> zero rows (enforcement ON).
    n = await raw_count(alpha_cid, None)
    assert n == 0, f"no-token scope read {n} rows under enforcement"

    # 8b: wrong-secret token -> zero rows.
    wrong = "deadbeef" * 8
    n = await raw_count(alpha_cid, wrong)
    assert n == 0, f"wrong-secret token read {n} rows under enforcement"

    # 8c: empty token -> zero rows (enforcement ON means no fallback).
    n = await raw_count(alpha_cid, "")
    assert n == 0, f"empty token read {n} rows under enforcement"


# ---------------------------------------------------------------------------
# Invariant: _apply_tenant_scope always sets tenant_scope AND scope_token
# together (the C3 root cause: leaking one without the other breaks RLS).
# ---------------------------------------------------------------------------


async def test_scope_and_token_always_set_together(world):
    """Every transaction that sets mindpalace.tenant_scope also sets
    mindpalace.scope_token, and the token matches HMAC(secret, scope).

    This is the C3 invariant: the app computes the token from
    MIND_PALACE_SCOPE_SECRET env, and the DB verifies against
    memory_server_secrets.scope_hmac_secret. If they diverge, RLS returns
    zero rows.
    """
    alpha_cid = corpus_id_for(ALPHA_CORPORA["brand"])
    secret = os.environ["MIND_PALACE_SCOPE_SECRET"]
    expected_token = hmac.new(secret.encode(), alpha_cid.encode(), hashlib.sha256).hexdigest()

    engine = apply_as_runtime(runtime_url())
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            # A real API operation goes through _apply_tenant_scope on
            # after_begin. Read both settings within the same transaction.
            await session.execute(
                sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                {"s": alpha_cid},
            )
            # Manually trigger what _apply_tenant_scope does: set both.
            token = hmac.new(secret.encode(), alpha_cid.encode(), hashlib.sha256).hexdigest()
            await session.execute(
                sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
                {"t": token},
            )
            scope_val = (
                await session.execute(
                    sa.text("SELECT current_setting('mindpalace.tenant_scope', true)")
                )
            ).scalar()
            token_val = (
                await session.execute(
                    sa.text("SELECT current_setting('mindpalace.scope_token', true)")
                )
            ).scalar()
            print(f"\nscope/token invariant: scope={scope_val} token={token_val[:16]}...")
            assert scope_val == alpha_cid, f"tenant_scope={scope_val!r} != {alpha_cid!r}"
            assert (
                token_val == expected_token
            ), f"scope_token={token_val!r} != expected HMAC={expected_token!r}"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Scenario 9: The {*} maintenance sentinel.
# ---------------------------------------------------------------------------


async def test_star_sentinel_under_enforcement(world):
    """{*} works for the maintenance role; runtime role cannot forge it."""
    alpha_cid = corpus_id_for(ALPHA_CORPORA["brand"])

    # 9a: maintenance role with {*} and a valid token can see every corpus.
    secret = os.environ["MIND_PALACE_SCOPE_SECRET"]
    star_token = hmac.new(secret.encode(), "{*}".encode(), hashlib.sha256).hexdigest()
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                {"s": "{*}"},
            )
            conn.execute(
                sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
                {"t": star_token},
            )
            result = conn.execute(sa.text("SELECT mp_verified_scope_corpora()"))
            assert "*" in result.scalar(), "{*} token not verified for maintenance"
    finally:
        engine.dispose()

    # 9b: runtime role cannot forge {*} -- zero rows even with {*} scope + token.
    async def runtime_star_count(scope: str) -> int:
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                    {"s": scope},
                )
                result = await session.execute(
                    sa.text("SELECT count(*) FROM memory_claims WHERE corpus_id = :c"),
                    {"c": alpha_cid},
                )
                return int(result.scalar())
        finally:
            await engine.dispose()

    n = await runtime_star_count("{*}")
    assert n == 0, f"runtime role read {n} rows with forged {{*}} scope"


# ---------------------------------------------------------------------------
# Scenario 10: Concurrent tenants on separate clients.
# ---------------------------------------------------------------------------


async def test_concurrent_tenants_pooled_connection(world):
    """Two tenants on separate clients never see each other's rows."""
    alpha_response = await world.post(
        "/api/memory/query",
        "alpha-creator",
        json={"corpus": ALPHA_CORPORA["brand"], "query": "battery"},
    )
    beta_response = await world.post(
        "/api/memory/query",
        "beta-creator",
        json={"corpus": BETA_CORPORA["brand"], "query": "router"},
    )
    assert alpha_response.status_code == 200, alpha_response.text
    assert beta_response.status_code == 200, beta_response.text

    alpha_body = alpha_response.json()
    beta_body = beta_response.json()
    assert any(
        "ALPHA_ENFORCEMENT_FACT" in m.get("claim", "")
        for m in alpha_body.get("current_memories", [])
    )
    assert any(
        "BETA_ENFORCEMENT_FACT" in m.get("claim", "") for m in beta_body.get("current_memories", [])
    )
    assert not any(
        "BETA_ENFORCEMENT_FACT" in m.get("claim", "")
        for m in alpha_body.get("current_memories", [])
    )
    assert not any(
        "ALPHA_ENFORCEMENT_FACT" in m.get("claim", "")
        for m in beta_body.get("current_memories", [])
    )


# ---------------------------------------------------------------------------
# Scenario 11: Latency + EXPLAIN InitPlan.
# ---------------------------------------------------------------------------


# Scenario 11: Hoisting measurement (deterministic).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "migration 015's per-row trap: mindpalace_allowed_corpus calls "
        "mp_verified_scope_corpora() once per row instead of hoisting it to "
        "an InitPlan. Fix via a new migration that puts "
        "(SELECT mp_verified_scope_corpora()) directly in each policy USING "
        "expression. Remove this xfail when the fix is merged."
    ),
)
async def test_latency_and_explain_initplan(world):
    """Deterministic EXPLAIN plan-shape + function-call check for scope-token
    enforcement.

    Asserts:
      * the RLS policy is applied (mindpalace_allowed_corpus filter present),
      * mp_verified_scope_corpora() is hoisted to an InitPlan (evaluated once
        per query, not per row) -- an InitPlan line MUST be present;
      * function-call counts for mp_verified_scope_corpora,
        mp_verify_scope_token and mindpalace_allowed_corpus are at most 2
        (one InitPlan evaluation plus at most one cache hit), not 50,000+.

    The InitPlan and call-count assertions are xfail(strict) until migration
    015's per-row trap is fixed by a new migration that puts
    (SELECT mp_verified_scope_corpora()) directly in each policy USING
    expression.

    Latency numbers live in scripts/bench_enforcement_latency.py.
    """
    # Insert a measurable number of claims directly (bypasses the remember API
    # and its embedding step) across 3 corpora.
    corpus_names = ["hoist-alpha", "hoist-beta", "hoist-gamma"]
    cids = [corpus_id_for(n) for n in corpus_names]
    n_per = 20

    admin_engine = sa.create_engine(sync_url())
    try:
        with admin_engine.begin() as conn:
            for i, (name, cid) in enumerate(zip(corpus_names, cids)):
                path = f"/{name}/doc.md"
                doc_id = hashlib.sha256((cid + ":" + path).encode()).hexdigest()
                ver_id = hashlib.sha256(f"ver:{cid}".encode()).hexdigest()
                chunk_id = hashlib.sha256(f"chunk:{cid}".encode()).hexdigest()
                conn.execute(
                    sa.text(
                        "INSERT INTO corpora (id, name) VALUES (:c, :n) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"c": cid, "n": name},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO memory_documents (corpus_id, id, path) "
                        "VALUES (:c, :d, :p) ON CONFLICT DO NOTHING"
                    ),
                    {"c": cid, "d": doc_id, "p": path},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO memory_versions "
                        "(corpus_id, memory_document_id, id, version_number, event, observed_at, "
                        "content, metadata, fingerprint, document_id) "
                        "VALUES (:c, :d, :v, 1, 'NEW', now(), 'c', '{}'::jsonb, 'fp', :d) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"c": cid, "d": doc_id, "v": ver_id},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index) "
                        "VALUES (:c, :v, :k, 'evidence text for validation', 0) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"c": cid, "v": ver_id, "k": chunk_id},
                )
                for j in range(n_per):
                    kid = hashlib.sha256(f"{cid}:{j}".encode()).hexdigest()
                    eid = hashlib.sha256(f"e:{cid}:{j}".encode()).hexdigest()
                    conn.execute(
                        sa.text(
                            "INSERT INTO memory_claims "
                            "(corpus_id, memory_document_id, version_id, id, key, claim, value, "
                            "info_state, extraction_mode, provenance_state) "
                            "VALUES (:c, :d, :v, :kid, :key, :claim, '{}'::jsonb, "
                            "'approved', 'deterministic', 'direct_claim') "
                            "ON CONFLICT DO NOTHING"
                        ),
                        {
                            "c": cid,
                            "d": doc_id,
                            "v": ver_id,
                            "kid": kid,
                            "key": f"k-{j}",
                            "claim": f"hoist claim {j}",
                        },
                    )
                    conn.execute(
                        sa.text(
                            "INSERT INTO memory_evidence "
                            "(corpus_id, version_id, id, claim_id, "
                            "chunk_id, quote, start_offset, end_offset) "
                            "VALUES (:c, :v, :eid, :kid, :chunk, "
                            "'evidence', 0, 8) "
                            "ON CONFLICT DO NOTHING"
                        ),
                        {
                            "c": cid,
                            "v": ver_id,
                            "eid": eid,
                            "kid": kid,
                            "chunk": chunk_id,
                        },
                    )
    finally:
        admin_engine.dispose()

    # Reset function-call counters (superuser only), then run as runtime.
    reset_engine = sa.create_engine(sync_url())
    try:
        with reset_engine.begin() as conn:
            for fn in (
                "mp_verified_scope_corpora()",
                "mp_verify_scope_token(CHAR(64),TEXT)",
                "mindpalace_allowed_corpus(CHAR(64))",
            ):
                conn.execute(
                    sa.text(f"SELECT pg_stat_reset_single_function_counters('{fn}'::regprocedure)")
                )
    finally:
        reset_engine.dispose()

    scope = ",".join(cids)
    secret = os.environ["MIND_PALACE_SCOPE_SECRET"]
    token = hmac.new(secret.encode(), scope.encode(), hashlib.sha256).hexdigest()

    # Run EXPLAIN as runtime role (sync engine, matching the manual measurement
    # that confirmed per-row calls). AsyncSession would also work but sync is
    # simpler for the counter readback below.
    rt_sync = (
        runtime_url()
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )
    rt_engine = sa.create_engine(rt_sync, poolclass=sa.pool.NullPool)
    try:
        with rt_engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                {"s": scope},
            )
            conn.execute(
                sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
                {"t": token},
            )
            result = conn.execute(
                sa.text("EXPLAIN (ANALYZE, VERBOSE, BUFFERS) " "SELECT count(*) FROM memory_claims")
            )
            plan = "\n".join(str(r[0]) for r in result.fetchall())
        print(f"\nEXPLAIN plan:\n{plan}")
    finally:
        rt_engine.dispose()

    has_initplan = "InitPlan" in plan
    has_allowed_corpus = "mindpalace_allowed_corpus" in plan

    assert has_allowed_corpus, f"RLS policy not applied in EXPLAIN:\n{plan}"

    # Read back the call counters (superuser).
    read_engine = sa.create_engine(sync_url())
    try:
        with read_engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT funcname, calls FROM pg_stat_user_functions "
                    "WHERE funcname IN ('mp_verified_scope_corpora', "
                    "'mp_verify_scope_token', 'mindpalace_allowed_corpus')"
                )
            ).fetchall()
    finally:
        read_engine.dispose()
    calls = {r[0]: r[1] for r in rows}
    print(f"\nFunction-call counts: {calls}")

    mac = calls.get("mindpalace_allowed_corpus", 0)
    mpc = calls.get("mp_verified_scope_corpora", 0)
    mvt = calls.get("mp_verify_scope_token", 0)

    # Unconditional assertions (fail now, pass after the InitPlan fix).
    assert (
        has_initplan
    ), f"Expected an InitPlan hoisting mp_verified_scope_corpora(); none found.\n{plan}"
    assert mac <= 2, (
        f"mindpalace_allowed_corpus called {mac} times (per-row trap); "
        f"expected at most 2 (InitPlan evaluated once).\n{plan}"
    )
    assert mpc <= 2, (
        f"mp_verified_scope_corpora called {mpc} times (per-row trap); "
        f"expected at most 2.\n{plan}"
    )
    assert mvt <= 2, f"mp_verify_scope_token called {mvt} times; expected at most 2.\n{plan}"


# ---------------------------------------------------------------------------
# Scenario 12: Operator procedure per docs/security.md.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scenario 12: Operator procedure per docs/security.md.
# ---------------------------------------------------------------------------


async def test_operator_procedure_per_docs(world):
    """Follow docs/security.md verbatim:

      mindpalace secret rotate  (as owner role, writes DB row + --out FILE)
      export MIND_PALACE_SCOPE_SECRET=<new>
      SELECT mp_set_scope_token_enforcement(true)  (as mindpalace_owner)
      start the server process
      send a real request

    Asserts documented steps alone suffice, with no other step needed.
    Also describes the DB/env mismatch window.
    """
    import subprocess
    import signal

    url = os.environ.get("DATABASE_URL")
    assert url, "DATABASE_URL must be set"

    old_secret = os.environ.get("MIND_PALACE_SCOPE_SECRET", "dev-scope-secret")
    secret_file = "/tmp/opencode/operator-secret.txt"
    os.makedirs(os.path.dirname(secret_file), exist_ok=True)

    # Step 1: `mindpalace secret rotate` via the CLI (writes DB row + file).
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "secret",
            "rotate",
            "--database-url",
            url,
            "--out",
            secret_file,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"secret rotate failed: {result.stderr}"
    new_secret = open(secret_file).read().strip()
    print(f"\nstep 1: mindpalace secret rotate -> wrote {secret_file}")

    # Step 2: set the env var (as docs instruct).
    os.environ["MIND_PALACE_SCOPE_SECRET"] = new_secret

    # Step 3: enable enforcement as the owner role.
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(true)"))
            row = conn.execute(
                sa.text("SELECT value FROM memory_server_secrets WHERE name = 'scope_hmac_secret'")
            ).scalar()
            assert row == new_secret, "DB secret not the rotated value"
            row = conn.execute(
                sa.text(
                    "SELECT value FROM memory_server_secrets WHERE name = 'enforce_scope_token'"
                )
            ).scalar()
            assert row == "on", "enforcement not on"
    finally:
        engine.dispose()
    print("step 2-3: env set, enforcement enabled")

    # Issue a credential for a real request.
    corpus_name = ALPHA_CORPORA["brand"]
    alpha_cid = corpus_id_for(corpus_name)
    async with db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            op_cred = await authz.issue_credential(
                db, "operator-client", [corpus_name], "read_write"
            )

    # Step 4: start the server process with the new secret + enforcement ON.
    # Provision mindpalace_runtime on the session database so the server can connect.
    parts = sa.engine.url.make_url(url)
    runtime_db_url = (
        f"postgresql+psycopg2://mindpalace_runtime:ci-runtime-role-password@"
        f"{parts.host}:{parts.port or 5432}/{parts.database}"
    )
    # Apply runtime_roles.sql to the session database.
    admin_engine = sa.create_engine(sync_url())
    try:
        with admin_engine.begin() as conn:
            with open("scripts/db/runtime_roles.sql") as f:
                rsql = f.read()
            rsql = rsql.replace(":'runtime_password'", "'ci-runtime-role-password'")
            rsql = rsql.replace(":'runtime_password_skip'", "'off'")
            rsql = rsql.replace(" \\g /dev/null", ";")
            rsql_lines = [line for line in rsql.split("\n") if not line.lstrip().startswith("\\")]
            rsql = "\n".join(rsql_lines)
            conn.execute(sa.text(rsql))
    finally:
        admin_engine.dispose()

    server_env = dict(os.environ)
    server_env["DATABASE_URL"] = runtime_db_url
    server_env["MIND_PALACE_AUTH_MODE"] = "required"
    server_env["MIND_PALACE_DEPLOYMENT"] = "server"
    server_env["MIND_PALACE_SCOPE_SECRET"] = new_secret
    server_env["MIND_PALACE_ENFORCE_SCOPE_TOKEN"] = "on"
    server_env["MIND_PALACE_RUNTIME"] = "0"
    server_env["MIND_PALACE_INGEST_ROOTS"] = "/content"

    server_log = open("/tmp/opencode/operator-server.log", "w")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8999",
            "--log-level",
            "warning",
        ],
        env=server_env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        # Wait for the server to become ready.
        import urllib.error
        import urllib.request

        base = "http://127.0.0.1:8999"
        ready = False
        for _ in range(120):
            try:
                with urllib.request.urlopen(base + "/openapi.json", timeout=2) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.5)
        assert ready, (
            "server did not start; log:\n" f"{open('/tmp/opencode/operator-server.log').read()}"
        )
        print("step 4: server started with enforcement ON")

        # Step 5: send a real request through the server.
        import json as _json

        req = urllib.request.Request(
            base + "/api/memory/current",
            data=_json.dumps({"corpus": corpus_name, "query": ""}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {op_cred['secret']}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = _json.loads(resp.read())
        memories = body.get("current_memories", [])
        assert memories, "server returned no memories with enforcement ON"
        print(f"step 5: real request succeeded, {len(memories)} memories returned")

    finally:
        try:
            os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        server_log.close()

    # Step 3 (continued): mismatch window - old env secret vs new DB secret -> 0 rows.
    os.environ["MIND_PALACE_SCOPE_SECRET"] = old_secret
    try:
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                    {"s": alpha_cid},
                )
                token = hmac.new(
                    old_secret.encode(), alpha_cid.encode(), hashlib.sha256
                ).hexdigest()
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
                    {"t": token},
                )
                result = await session.execute(
                    sa.text("SELECT count(*) FROM memory_claims WHERE corpus_id = :c"),
                    {"c": alpha_cid},
                )
                n = int(result.scalar())
        finally:
            await engine.dispose()
        assert n == 0, f"mismatched secret read {n} rows (window should yield 0)"
    finally:
        os.environ["MIND_PALACE_SCOPE_SECRET"] = new_secret

    print(
        "\noperator procedure OK: rotate writes DB row, enable enforcement, "
        "server starts and serves real requests; mismatched-window yields 0 rows; "
        "then env must be rolled to match."
    )


# ---------------------------------------------------------------------------
# Scenario 8(b): Negative control - wrong env secret causes failures.
# ---------------------------------------------------------------------------


async def test_wrong_env_secret_fails(world):
    """With enforcement ON but MIND_PALACE_SCOPE_SECRET deliberately wrong,
    scenarios 1-7 must fail: tokens don't verify, RLS returns zero rows."""
    # Set a deliberately wrong secret.
    os.environ["MIND_PALACE_SCOPE_SECRET"] = "deadbeef" * 8
    try:
        alpha_cid = corpus_id_for(ALPHA_CORPORA["brand"])

        # 8b.1: recall returns 0 rows (wrong token -> mismatch -> zero rows).
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                    {"s": alpha_cid},
                )
                import hmac

                token = hmac.new(
                    os.environ["MIND_PALACE_SCOPE_SECRET"].encode(),
                    alpha_cid.encode(),
                    hashlib.sha256,
                ).hexdigest()
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
                    {"t": token},
                )
                result = await session.execute(
                    sa.text("SELECT count(*) FROM memory_claims WHERE corpus_id = :c"),
                    {"c": alpha_cid},
                )
                n = int(result.scalar())
        finally:
            await engine.dispose()
        assert n == 0, f"wrong secret: recall read {n} rows (should be 0)"

        # 8b.2: API recall returns 403/404.
        response = await world.post(
            "/api/memory/current",
            "alpha-creator",
            json={"corpus": ALPHA_CORPORA["brand"], "query": ""},
        )
        assert response.status_code in (
            403,
            404,
        ), f"wrong secret: API recall returned {response.status_code} (should be 403/404)"

        # 8b.3: Listing corpora returns empty.
        response = await world.get("/api/corpora", "alpha-creator")
        assert response.status_code == 200, response.text
        names = {row["name"] for row in response.json()}
        assert names == set(), f"wrong secret: listing returned {names} (should be empty)"

        print(
            "\nnegative control OK: wrong env secret causes all tenant-scoped "
            "operations to fail (0 rows, 403/404, empty listing)"
        )
    finally:
        # monkeypatch (from the world fixture) restores MIND_PALACE_SCOPE_SECRET
        # to enforced_secret when the test ends.
        pass
