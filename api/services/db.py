# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Database setup, async session management and the tenant scope.

Schema is managed by Alembic migrations. This module provides async session
management, connectivity checks, and the one place the tenant scope is
established.

The tenant scope is set on `after_begin`, which is the earliest point a
transaction exists and before any statement in it runs. Two properties follow
from that choice, and both are load-bearing:

* **It is transaction-local.** `set_config(..., is_local := true)` is discarded
  when the transaction ends. A connection handed back to the pool cannot carry a
  tenant scope into the next request, so connection reuse cannot change an
  authorization decision. This is asserted by
  `tests/test_tenant_connection_isolation.py`, which reuses one pooled connection
  across tenants, across an exception, and across a rollback.
* **It is derived from the request, not from the connection.** The value comes
  from a `ContextVar` set by the request that opened the session, so the same
  connection serves different tenants correctly and the scope cannot be pinned to
  whichever tenant happened to create the connection.

Fail-closed is the default: with no principal in context the scope is empty, the
row-level policy in migration `008` matches no corpus, and a query without a
tenant scope sees nothing rather than everything. Only the documented single-user
mode sets `{*}`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from api.services.authz import LOCAL_PRINCIPAL, Principal

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mpadmin:secret@localhost:5432/mindpalace",
)

# Normalize any sync-style URL to asyncpg
ASYNC_DATABASE_URL = (
    DATABASE_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://")
    .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    .replace("postgresql://", "postgresql+asyncpg://")
)

# NullPool: connections are not reused across event loops. The API server
# runs a single long-lived loop so pooling helps there, but SDK/CLI callers
# create short-lived loops per operation and pooled connections would break
# with 'attached to a different loop' errors.
async_engine: AsyncEngine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

#: The transaction-local setting the row-level policy reads. Named with a
#: `mindpalace.` prefix so it cannot collide with a PostgreSQL built-in.
TENANT_SETTING = "mindpalace.tenant_scope"

#: The transaction-local setting resource-level authorization reads: which
#: principal is acting, so a private document can be recognised as its own.
#: Same trust properties as `TENANT_SETTING` -- the application writes it -- and
#: documented the same way. See `docs/security.md`.
PRINCIPAL_SETTING = "mindpalace.acting_principal"

#: The single-user value. Not a wildcard a caller can choose: the application
#: writes it, and only in the mode documented as local and single-user.
UNRESTRICTED_SCOPE = "{*}"

_current_principal: ContextVar[Principal] = ContextVar(
    "mindpalace_principal", default=LOCAL_PRINCIPAL
)


def current_principal() -> Principal:
    """The identity whose scope this request runs under."""
    return _current_principal.get()


@contextmanager
def principal_scope(principal: Principal):
    """Run a block as one principal, restoring the previous one afterwards.

    Restoration in a `finally` is what stops a request from leaking its identity
    into the next request on the same task, and what lets a background job or a
    nested operation establish and drop its own context deliberately.
    """
    token = _current_principal.set(principal)
    try:
        yield principal
    finally:
        _current_principal.reset(token)


def _scope_value() -> str:
    # Ids, not names: the row-level policy compares against `corpora.id`, and both
    # are resolved from the same join at authentication time, so a policy can never
    # be scoped to a corpus id that a differently-spelled name would select.
    #
    # Kept as a named function rather than inlined at the call site: the negative
    # tests monkeypatch it to strip the scoping and prove the leak appears without
    # it. Inlining it would silently disarm that control.
    return _current_principal.get().scope()


def _principal_value() -> str:
    """Which principal resource-level authorization is acting as.

    Empty for the local, unrestricted principal, which owns nothing and therefore
    is granted nothing explicitly -- it reaches shared documents by being
    unrestricted, exactly as before.
    """
    return _current_principal.get().id or ""


@event.listens_for(Session, "after_begin")
def _apply_tenant_scope(session, transaction, connection):
    """Establish the tenant scope and acting principal for every transaction.

    Registered on `Session` rather than on this sessionmaker so it also covers
    sessions created by callers that never touch `session_scope` -- a new query
    path cannot forget to set its scope, because the scope is not set by the
    query path.

    An acting-principal setting was added here for resource-level authorization
    and then removed with it: it was the one change that touches every
    runtime-role transaction, and it broke writes that have always succeeded
    (`new row violates row-level security policy for table "memory_documents"`).
    `PRINCIPAL_SETTING` and `_principal_value()` remain, unused and documented,
    so reinstating the capability is a migration rather than a hunt.
    """
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_SETTING, "value": _scope_value()},
    )


async def init_db() -> None:
    """Verify database connectivity. Schema is managed by Alembic."""
    async with async_engine.begin() as conn:
        await conn.execute(text("SELECT 1"))


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI async session dependency."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Standalone async session context manager for non-FastAPI callers."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def admin_session_scope(
    url: str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """A session on the maintenance role, for administrative commands.

    The least-privileged runtime role is denied `SELECT` on `memory_principals`
    precisely so an ordinary query cannot enumerate every tenant's credential
    hashes. Issuing, listing and revoking credentials are administrative actions
    that need exactly that access, so they connect with a different role rather
    than widening the application role to allow them.

    Falls back to `DATABASE_URL` when no administrative URL is configured, which
    is what makes the single-user local workflow work with one setting. A
    tenant-scoped deployment sets `MIND_PALACE_ADMIN_DATABASE_URL`; the refusal
    then comes from PostgreSQL, which is the right place for it.

    `NullPool` like the application engine, so a session is never shared with a
    request that arrived under a different tenant scope.
    """
    resolved = (url or DATABASE_URL).replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(resolved, echo=False, poolclass=NullPool)
    try:
        async with async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )() as session:
            yield session
    finally:
        await engine.dispose()
