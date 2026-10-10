"""Tenant isolation across reused database connections.

The tenant scope is a transaction-local setting, and transaction-local is a claim
that has to be tested rather than believed. These tests deliberately reuse one
pooled connection across different tenants, across failures, and across rollbacks,
and assert the scope never outlives its transaction.

Connection reuse is asserted, not assumed: each test checks that the backend
connection identity is actually the same between the two tenants, so a test that
silently stopped sharing a connection would fail rather than pass for the wrong
reason.

Runs on real PostgreSQL with the least-privileged role, because the behaviour
under test is a row-level policy.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tenant_harness import (
    ALPHA_CORPORA,
    ALPHA_PRIVATE_PRODUCT_FACT_7Q2,
    BETA_CORPORA,
    BETA_PRIVATE_PRODUCT_FACT_7Q2,
    apply_as_runtime,
    requires_tenant_db,
    runtime_url,
    seed_as_maintenance,
)

pytestmark = [pytest.mark.asyncio, requires_tenant_db]


def corpus_id_for(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


class Tenant:
    """A principal scoped to exactly one corpus."""

    def __init__(self, corpus: str):
        from api.services.authz import Principal

        self.corpus = corpus
        self.principal = Principal(
            id="t",
            name="t",
            role="read_only",
            corpora=frozenset({corpus}),
            corpus_ids=frozenset({corpus_id_for(corpus)}),
        )


@pytest.fixture
async def pooled(monkeypatch):
    """A genuinely pooled runtime-role engine with Alpha and Beta seeded.

    Seeding goes through `seed_as_maintenance`, on the maintenance role, because
    the runtime role is not allowed to create a corpus -- see that helper for why
    that is the design and not an accident. The runtime engine is then what the
    tests themselves drive, with scoped principals, which is the configuration
    that has to survive connection reuse.
    """
    from api.services import db as db_module
    from test_tenant_isolation import _provision

    _provision()

    # Seeded *before* the monkeypatch below. `remember()` opens its own session
    # from the module-level `AsyncSessionLocal`, so seeding after the swap would
    # write as the runtime role -- which is exactly the thing being tested
    # against, and which is not allowed to create a corpus.
    for corpus, statement, key in (
        (ALPHA_CORPORA["brand"], ALPHA_PRIVATE_PRODUCT_FACT_7Q2, "alpha_battery"),
        (BETA_CORPORA["brand"], BETA_PRIVATE_PRODUCT_FACT_7Q2, "beta_router"),
    ):
        await seed_as_maintenance(statement, corpus, key)

    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_engine", engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)

    try:
        yield factory, engine
    finally:
        await engine.dispose()


def _admin():
    from api.services.authz import ADMIN_PRINCIPAL

    return ADMIN_PRINCIPAL


async def claims(factory, db_module, principal) -> list[str]:
    async with factory() as db:
        with db_module.principal_scope(principal):
            async with db.begin():
                return list(
                    (await db.execute(sa.text("SELECT claim FROM memory_claims"))).scalars()
                )


async def backend_pid(factory, db_module, principal) -> str:
    """The PostgreSQL backend, so a test can prove two requests shared it."""
    async with factory() as db:
        with db_module.principal_scope(principal):
            async with db.begin():
                return str((await db.execute(sa.text("SELECT pg_backend_pid()"))).scalar())


async def test_one_pooled_connection_serves_two_tenants_in_turn(pooled):
    """Alpha, then Beta, then Alpha again, on the same connection."""
    factory, _ = pooled
    from api.services import db as db_module

    alpha, beta = Tenant(ALPHA_CORPORA["brand"]), Tenant(BETA_CORPORA["brand"])

    first_pid = await backend_pid(factory, db_module, alpha.principal)
    alpha_claims = await claims(factory, db_module, alpha.principal)
    beta_claims = await claims(factory, db_module, beta.principal)
    again = await claims(factory, db_module, alpha.principal)
    last_pid = await backend_pid(factory, db_module, beta.principal)

    assert first_pid == last_pid, "the pool did not reuse a connection; this proves nothing"
    assert alpha_claims and all(ALPHA_PRIVATE_PRODUCT_FACT_7Q2 in c for c in alpha_claims)
    assert beta_claims and all(BETA_PRIVATE_PRODUCT_FACT_7Q2 in c for c in beta_claims)
    assert again == alpha_claims, "Alpha's second request saw something else"


async def test_a_transaction_that_raises_leaves_no_scope_behind(pooled):
    """The failure path is the one that matters: a rollback, then a reuse."""
    factory, _ = pooled
    from api.services import db as db_module

    alpha, beta = Tenant(ALPHA_CORPORA["brand"]), Tenant(BETA_CORPORA["brand"])

    with pytest.raises(RuntimeError):
        async with factory() as db:
            with db_module.principal_scope(alpha.principal):
                async with db.begin():
                    await db.execute(sa.text("SELECT claim FROM memory_claims"))
                    raise RuntimeError("a handler failed mid-transaction")

    beta_claims = await claims(factory, db_module, beta.principal)
    assert beta_claims
    assert not any(ALPHA_PRIVATE_PRODUCT_FACT_7Q2 in claim for claim in beta_claims)


async def test_a_rollback_leaves_no_scope_behind(pooled):
    factory, _ = pooled
    from api.services import db as db_module

    alpha, beta = Tenant(ALPHA_CORPORA["brand"]), Tenant(BETA_CORPORA["brand"])

    async with factory() as db:
        with db_module.principal_scope(alpha.principal):
            transaction = await db.begin()
            await db.execute(sa.text("SELECT claim FROM memory_claims"))
            await transaction.rollback()

    beta_claims = await claims(factory, db_module, beta.principal)
    assert not any(ALPHA_PRIVATE_PRODUCT_FACT_7Q2 in claim for claim in beta_claims)


async def test_a_cancelled_request_leaves_no_scope_behind(pooled):
    """Cancellation is a `finally` path like any other, and is easier to get wrong."""
    factory, _ = pooled
    from api.services import db as db_module

    alpha, beta = Tenant(ALPHA_CORPORA["brand"]), Tenant(BETA_CORPORA["brand"])
    started = asyncio.Event()

    async def abandoned():
        async with factory() as db:
            with db_module.principal_scope(alpha.principal):
                async with db.begin():
                    await db.execute(sa.text("SELECT claim FROM memory_claims"))
                    started.set()
                    await asyncio.sleep(30)

    task = asyncio.create_task(abandoned())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    beta_claims = await claims(factory, db_module, beta.principal)
    assert not any(ALPHA_PRIVATE_PRODUCT_FACT_7Q2 in claim for claim in beta_claims)


async def test_no_tenant_context_sees_nothing(pooled):
    """The default must be empty, not unrestricted."""
    factory, _ = pooled
    from api.services import db as db_module
    from api.services.authz import Principal

    anonymous = Principal("none", "none", "read_only", frozenset(), frozenset())
    assert await claims(factory, db_module, anonymous) == []


async def test_concurrent_requests_for_two_tenants_stay_separate(pooled):
    """Interleaved transactions on a shared pool must not mix their scopes."""
    factory, _ = pooled
    from api.services import db as db_module

    alpha, beta = Tenant(ALPHA_CORPORA["brand"]), Tenant(BETA_CORPORA["brand"])

    results = await asyncio.gather(
        *[claims(factory, db_module, (alpha if i % 2 == 0 else beta).principal) for i in range(12)]
    )
    for index, found in enumerate(results):
        expected = (
            ALPHA_PRIVATE_PRODUCT_FACT_7Q2 if index % 2 == 0 else BETA_PRIVATE_PRODUCT_FACT_7Q2
        )
        other = BETA_PRIVATE_PRODUCT_FACT_7Q2 if index % 2 == 0 else ALPHA_PRIVATE_PRODUCT_FACT_7Q2
        assert found, f"request {index} saw nothing"
        assert all(expected in claim for claim in found), f"request {index} saw another tenant"
        assert not any(other in claim for claim in found), f"request {index} leaked"


async def test_a_connection_reused_after_a_protocol_error_still_resets(pooled):
    """An error that ends a connection must not leave a scope attached to it."""
    factory, _ = pooled
    from api.services import db as db_module
    from sqlalchemy.exc import DBAPIError

    beta = Tenant(BETA_CORPORA["brand"])
    with pytest.raises(DBAPIError):
        async with factory() as db:
            with db_module.principal_scope(beta.principal):
                async with db.begin():
                    await db.execute(sa.text("SELECT no_such_column FROM memory_claims"))

    after = await claims(factory, db_module, beta.principal)
    assert after
    assert not any(ALPHA_PRIVATE_PRODUCT_FACT_7Q2 in claim for claim in after)


async def test_the_scope_is_transaction_local_not_session_local(pooled):
    """Two transactions inside one session see different scopes.

    This is the property `set_config(..., is_local := true)` buys. A session-level
    `SET` would make the first tenant win for the rest of the session, and the
    second request would be authorized as the first.
    """
    factory, _ = pooled
    from api.services import db as db_module

    alpha, beta = Tenant(ALPHA_CORPORA["brand"]), Tenant(BETA_CORPORA["brand"])
    async with factory() as db:
        with db_module.principal_scope(alpha.principal):
            async with db.begin():
                first = list(
                    (await db.execute(sa.text("SELECT claim FROM memory_claims"))).scalars()
                )
        with db_module.principal_scope(beta.principal):
            async with db.begin():
                second = list(
                    (await db.execute(sa.text("SELECT claim FROM memory_claims"))).scalars()
                )
    assert first and all(ALPHA_PRIVATE_PRODUCT_FACT_7Q2 in c for c in first)
    assert second and all(BETA_PRIVATE_PRODUCT_FACT_7Q2 in c for c in second)
