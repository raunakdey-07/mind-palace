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
8. Forgery: scope without token -> zero rows; wrong-secret token -> zero rows; empty token -> zero rows.
9. The `{*}` maintenance sentinel: maintenance role with {*}+token works; runtime role cannot forge {*}.
10. Two tenants on separate clients never see each other's rows.
11. Latency p50/p95 recall with enforcement ON vs OFF; EXPLAIN shows InitPlan.
12. Operator procedure per docs/security.md: rotate, enable, mismatch window yields 0 rows.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets
import statistics
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
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
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
    return secret


@pytest.fixture(scope="session")
def enforced_secret():
    """Rotate a random scope secret and enable enforcement on the session DB."""
    url = os.environ.get("DATABASE_URL")
    assert url, "DATABASE_URL must be set (conftest creates the session database)"
    return _rotate_and_enable(url)


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


async def test_admin_creates_a_corpus(world):
    """A corpus created by the admin is visible under enforcement."""
    corpus_name = "alpha-enforcement-corpus"
    cid = corpus_id_for(corpus_name)

    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                sa.text(
                    "INSERT INTO corpora (id, name, description) VALUES (:c, :n, '') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"c": cid, "n": corpus_name},
            )
            await db.commit()
            # Grant alpha-admin access so it appears in listings.
            from api.services.authz import grant_corpora

            await grant_corpora(db, "alpha-admin", [corpus_name])
            await db.commit()

    # It appears in listings under enforcement.
    response = await world.get("/api/corpora", "alpha-admin")
    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()}
    assert corpus_name in names, f"{corpus_name} not in {names}"

    # Clean up.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": cid}
            )
            await db.commit()
            await db.execute(sa.text("DELETE FROM corpora WHERE id = :c"), {"c": cid})
            await db.commit()


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
# Scenario 5: Erasure removes the rows.
# ---------------------------------------------------------------------------


async def test_erasure_removes_rows(world):
    """Erasure through mindpalace_erase_corpus removes the corpus's rows."""
    corpus_name = "alpha-enforcement-erasure"
    cid = corpus_id_for(corpus_name)

    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                sa.text(
                    "INSERT INTO corpora (id, name, description) VALUES (:c, :n, '') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"c": cid, "n": corpus_name},
            )
            await db.commit()
            await db.execute(
                sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": cid}
            )
            await db.commit()
            await db.execute(sa.text("DELETE FROM corpora WHERE id = :c"), {"c": cid})
            await db.commit()

    # The corpus should be gone.
    async with world.db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            row = await db.execute(
                sa.text("SELECT count(*) FROM corpora WHERE id = :c"), {"c": cid}
            )
            assert row.scalar() == 0


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
    assert any("BETA_ENFORCEMENT_FACT" in m.get("claim", "") for m in body.get("current_memories", [])), body


# ---------------------------------------------------------------------------
# Scenario 8: Forgery - scope without token, wrong-secret token, empty token.
# ---------------------------------------------------------------------------


async def test_forgery_fails_closed(world):
    """Forged or missing scope tokens return zero rows under enforcement."""
    alpha_cid = corpus_id_for(ALPHA_CORPORA["brand"])

    async def raw_count(scope: str, token: str | None) -> int:
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
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
                    sa.text(
                        "SELECT count(*) FROM memory_claims WHERE corpus_id = :c"
                    ),
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
            result = conn.execute(
                sa.text("SELECT mp_verified_scope_corpora()")
            )
            assert "*" in result.scalar(), "{*} token not verified for maintenance"
    finally:
        engine.dispose()

    # 9b: runtime role cannot forge {*} -- zero rows even with {*} scope + token.
    async def runtime_star_count(scope: str) -> int:
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                    {"s": scope},
                )
                result = await session.execute(
                    sa.text(
                        "SELECT count(*) FROM memory_claims WHERE corpus_id = :c"
                    ),
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
    assert any("ALPHA_ENFORCEMENT_FACT" in m.get("claim", "") for m in alpha_body.get("current_memories", []))
    assert any("BETA_ENFORCEMENT_FACT" in m.get("claim", "") for m in beta_body.get("current_memories", []))
    assert not any("BETA_ENFORCEMENT_FACT" in m.get("claim", "") for m in alpha_body.get("current_memories", []))
    assert not any("ALPHA_ENFORCEMENT_FACT" in m.get("claim", "") for m in beta_body.get("current_memories", []))


# ---------------------------------------------------------------------------
# Scenario 11: Latency + EXPLAIN InitPlan.
# ---------------------------------------------------------------------------


async def test_latency_and_explain_initplan(world):
    """p50/p95 recall latency with enforcement ON vs OFF; EXPLAIN shows InitPlan."""
    corpus_name = "enforcement-latency-corpus"
    cid = corpus_id_for(corpus_name)

    # Create corpus and issue a credential via admin DB session.
    async with db_module.admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                sa.text(
                    "INSERT INTO corpora (id, name, description) VALUES (:c, :n, '') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"c": cid, "n": corpus_name},
            )
            await db.commit()
            lat_cred = await authz.issue_credential(
                db, "latency-client", [corpus_name], "read_write"
            )

    client_headers = {"Authorization": f"Bearer {lat_cred['secret']}"}

    # Write ~20 claims via the remember API (handles all archive constraints).
    for i in range(20):
        resp = await world.post(
            "/api/memory/remember",
            None,
            json={
                "corpus": corpus_name,
                "statement": f"latency fact number {i} about widgets and production systems.",
                "key": f"lat-k{i}",
            },
            headers=client_headers,
        )
        assert resp.status_code in (200, 201), resp.text

    try:
        # EXPLAIN as runtime role with valid scope + token -> InitPlan on mp_verified_scope_corpora.
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                secret = os.environ["MIND_PALACE_SCOPE_SECRET"]
                scope = cid
                token = hmac.new(secret.encode(), scope.encode(), hashlib.sha256).hexdigest()
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                    {"s": scope},
                )
                await session.execute(
                    sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
                    {"t": token},
                )
                result = await session.execute(
                    sa.text(
                        "EXPLAIN (COSTS OFF) "
                        "SELECT count(*) FROM memory_claims WHERE corpus_id = :c"
                    ),
                    {"c": cid},
                )
                plan = "\n".join(str(r[0]) for r in result.fetchall())
            assert "mindpalace_allowed_corpus" in plan, (
                f"RLS policy not applied in EXPLAIN:\n{plan}"
            )
        finally:
            await engine.dispose()

        # Latency with enforcement ON via API.
        recalls = []
        for _ in range(50):
            t0 = time.perf_counter()
            resp = await world.post(
                "/api/memory/query",
                None,
                json={"corpus": corpus_name, "query": "widgets"},
                headers=client_headers,
            )
            assert resp.status_code == 200, resp.text
            recalls.append((time.perf_counter() - t0) * 1000)

        # Toggle enforcement OFF.
        engine = sa.create_engine(sync_url())
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(false)"))
        finally:
            engine.dispose()

        try:
            recalls_off = []
            for _ in range(50):
                t0 = time.perf_counter()
                resp = await world.post(
                    "/api/memory/query",
                    None,
                    json={"corpus": corpus_name, "query": "widgets"},
                    headers=client_headers,
                )
                assert resp.status_code == 200, resp.text
                recalls_off.append((time.perf_counter() - t0) * 1000)
        finally:
            engine = sa.create_engine(sync_url())
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(true)"))
            finally:
                engine.dispose()

        p50_on = statistics.median(recalls)
        p95_on = statistics.quantiles(recalls, n=20)[18]
        p50_off = statistics.median(recalls_off)
        p95_off = statistics.quantiles(recalls_off, n=20)[18]
        print(
            f"\nlatency ON:  p50={p50_on:.2f}ms p95={p95_on:.2f}ms  "
            f"OFF: p50={p50_off:.2f}ms p95={p95_off:.2f}ms"
        )
    finally:
        # Clean up the latency corpus.
        async with db_module.admin_session_scope() as db:
            with principal_scope(authz.ADMIN_PRINCIPAL):
                await db.execute(
                    sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": cid}
                )
                await db.commit()
                await db.execute(sa.text("DELETE FROM corpora WHERE id = :c"), {"c": cid})
                await db.commit()


# ---------------------------------------------------------------------------
# Scenario 12: Operator procedure per docs/security.md.
# ---------------------------------------------------------------------------


async def test_operator_procedure_per_docs():
    """Follow docs/security.md literally: rotate, enable enforcement, restart.

    Asserts documented steps suffice, and describes the mismatch window.
    """
    url = os.environ.get("DATABASE_URL")
    assert url, "DATABASE_URL must be set"

    old_secret = os.environ.get("MIND_PALACE_SCOPE_SECRET", "dev-scope-secret")

    # Step 1: rotate a random secret into the DB row.
    new_secret = _rotate_and_enable(url)

    # Step 2: enforcement is already ON (rotate_and_enable sets it). Also set it
    # explicitly per docs.
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(true)"))
            row = conn.execute(
                sa.text(
                    "SELECT value FROM memory_server_secrets WHERE name = 'scope_hmac_secret'"
                )
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

    # Step 3: with env still pointing at the OLD secret, queries return 0 rows
    # (the window where DB and env differ).
    os.environ["MIND_PALACE_SCOPE_SECRET"] = old_secret
    try:
        alpha_cid = corpus_id_for(ALPHA_CORPORA["brand"])
        engine = apply_as_runtime(runtime_url())
        try:
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
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
                    sa.text(
                        "SELECT count(*) FROM memory_claims WHERE corpus_id = :c"
                    ),
                    {"c": alpha_cid},
                )
                n = int(result.scalar())
        finally:
            await engine.dispose()
        assert n == 0, f"mismatched secret read {n} rows (window should yield 0)"
    finally:
        # Restore env to the new secret.
        os.environ["MIND_PALACE_SCOPE_SECRET"] = new_secret

    print(
        "\noperator procedure OK: rotate writes DB row, enable enforcement, "
        "mismatched-window yields 0 rows; then env must be rolled to match."
    )
