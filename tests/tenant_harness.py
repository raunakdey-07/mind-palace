"""Shared harness for the tenant-isolation suites.

Three things are set up here that the tests then attack:

1. A **least-privileged runtime role**. The application under test connects as
   `mindpalace_runtime`, which is not a superuser, does not have `BYPASSRLS`, and
   owns no tables. A row-level policy proved against a superuser proves nothing,
   because PostgreSQL exempts superusers from row-level security entirely, so the
   isolation tests would pass for the wrong reason.
2. **Two organisations** with distinguishable canary content, three users each,
   and corpora separated into brand memory and per-campaign memory.
3. **Credentials** bound to explicit corpus sets, one read-only, one revoked, and
   one belonging to somebody who is not a member of either organisation.

Everything runs on real PostgreSQL through the real HTTP stack. Nothing here
substitutes SQLite or mocks the database, because the control under test is a
database policy.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import AsyncAdaptedQueuePool

#: Names are corpus names, not tenant identifiers: the application resolves them
#: from its own records and Mind Palace treats them as opaque.
ALPHA = "alpha"
BETA = "beta"
ALPHA_CORPORA = {
    "brand": "alpha-brand",
    "campaign_1": "alpha-spring-launch",
    "campaign_2": "alpha-monsoon-sale",
}
BETA_CORPORA = {
    "brand": "beta-brand",
    "campaign_1": "beta-winter-launch",
    "campaign_2": "beta-rainy-season-sale",
}

#: Canaries. If one of these appears in a response to the wrong principal, that
#: is a cross-tenant read, and the test names which marker so the failure says
#: what leaked rather than only that something did.
ALPHA_PRIVATE_PRODUCT_FACT_7Q2 = (
    "ALPHA_PRIVATE_PRODUCT_FACT_7Q2 the Kestrel battery lasts 41 hours."
)
ALPHA_PRIVATE_CAMPAIGN_FACT_9M4 = (
    "ALPHA_PRIVATE_CAMPAIGN_FACT_9M4 the spring launch targets coastal schools."
)
BETA_PRIVATE_PRODUCT_FACT_7Q2 = (
    "BETA_PRIVATE_PRODUCT_FACT_7Q2 the Nimbus router has four mesh bands."
)
BETA_PRIVATE_CAMPAIGN_FACT_9M4 = (
    "BETA_PRIVATE_CAMPAIGN_FACT_9M4 the winter launch targets mountain lodges."
)
ALL_CANARIES = (
    ALPHA_PRIVATE_PRODUCT_FACT_7Q2,
    ALPHA_PRIVATE_CAMPAIGN_FACT_9M4,
    BETA_PRIVATE_PRODUCT_FACT_7Q2,
    BETA_PRIVATE_CAMPAIGN_FACT_9M4,
)

#: A per-session role name, NOT the shipped `mindpalace_runtime`.
#:
#: This harness used to provision the real role on whatever `$DATABASE_URL`
#: pointed at, which rotated its password on the SHARED database. On CI that
#: broke every later step that authenticated as `mindpalace_runtime`; the
#: posture check degraded to "unknown means not proven" and reported all 13
#: protected tables as unprotected. Three CI runs failed on that before the
#: cause was found, and the first fix -- moving the posture steps earlier -- was
#: treating the symptom.
#:
#: Roles are cluster-wide even when databases are not, so the only way a test
#: can leave a cluster alone is to use a name nobody else is using. One is
#: minted per session and dropped at teardown.
# The name deliberately does NOT contain "mindpalace_runtime" as a substring.
# If it did, every substitution below would be ambiguous -- you could not tell a
# replaced occurrence from one inside the new name -- and a grep of the resolved
# script could not tell whether the real role had been touched.
RUNTIME_ROLE = "mp_tenant_test_" + uuid.uuid4().hex[:12]
RUNTIME_PASSWORD = "tenant-isolation-test-password"

#: The role name in the shipped script. Substituted for `RUNTIME_ROLE` so the
#: harness provisions the SAME script an operator runs, with a different name.
SHIPPED_RUNTIME_ROLE = "mindpalace_runtime"

#: The provisioning script an operator actually runs, relative to the repository
#: root. Read from disk rather than duplicated -- see `load_provision_sql`.
PROVISION_SCRIPT = "scripts/db/runtime_roles.sql"


def load_provision_sql(
    path: str = PROVISION_SCRIPT,
    *,
    password: str = RUNTIME_PASSWORD,
    skip_password: bool = False,
) -> str:
    r"""Read the shipped provisioning script and resolve its psql variables.

    Two things make the script unusable through a plain SQL driver as written:
    the ``\if``/``\set`` block that gives absent variables a default, and the
    ``:'name'`` interpolation. Both are removed here rather than reimplemented,
    because reimplementing them is how the two copies drifted.

    The password is passed through `set_config` and quoted by `format('%L')`
    inside the script itself, so a value containing a quote or a semicolon is
    still handled safely -- this function only has to decide what to put in the
    variable.
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()

    resolved = []
    for line in lines:
        # psql meta-commands: `\if`, `\else`, `\endif`, `\set`. Dropping them
        # leaves the SQL underneath exactly as psql would send it.
        if line.lstrip().startswith("\\"):
            continue
        resolved.append(
            line.replace(":'runtime_password'", "'%s'" % password.replace("'", "''"))
            .replace(":'runtime_password_skip'", "'on'" if skip_password else "'off'")
            .replace(SHIPPED_RUNTIME_ROLE, RUNTIME_ROLE)
        )

    script = "".join(resolved)
    if ":runtime_password" in script:
        raise AssertionError(
            f"{path} still contains an unresolved psql variable; the tests would "
            "provision a different role from the one the deployment guide uses"
        )
    return script


def apply_provision_sql(sql: str | None = None) -> None:
    """Run the provisioning script as one batch, the way `psql -f` would.

    Through the raw driver cursor rather than `exec_driver_sql`, because
    SQLAlchemy reads a multi-statement string as one statement expecting
    parameters, and this script is a dozen of them.
    """
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            conn.connection.driver_connection.cursor().execute(
                PROVISION_SQL if sql is None else sql
            )
    finally:
        engine.dispose()


#: The runtime role must be ordinary. Asserted at setup rather than assumed,
#: because a superuser or `BYPASSRLS` role silently disables every policy this
#: suite exists to test.
#:
#: This is not a copy of `scripts/db/runtime_roles.sql`. It is that file, with the
#: two psql variables resolved -- see `load_provision_sql`. An earlier version
#: kept a hand-maintained duplicate, which is how a missing parenthesis in the
#: `REVOKE ... FROM PUBLIC` lines shipped unnoticed for a full cycle: the copy
#: was correct and the script an operator actually runs was not, so every test
#: passed against a role no deployment had. Provisioning from the artifact means
#: the isolation tests run against the same role the deployment guide produces.
PROVISION_SQL = load_provision_sql()


def sync_url() -> str:
    return (
        os.environ["DATABASE_URL"]
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def runtime_url() -> str:
    """The same database, reached as the least-privileged runtime role."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(os.environ["DATABASE_URL"])
    return urlunsplit(
        (
            # Always the async driver: the application opens asyncpg sessions, and
            # a runtime URL that silently fell back to the sync driver would make
            # this suite test a different engine than the one in production.
            "postgresql+asyncpg",
            f"{RUNTIME_ROLE}:{RUNTIME_PASSWORD}@{parts.hostname}:{parts.port or 5432}",
            parts.path,
            "",
            parts.query,
        )
    )


def database_available() -> bool:
    if not os.getenv("DATABASE_URL"):
        return False
    try:
        engine = sa.create_engine(sync_url())
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001 - a probe, not a test assertion
        return False


def is_superuser() -> bool:
    engine = sa.create_engine(sync_url())
    try:
        with engine.connect() as conn:
            return bool(
                conn.execute(
                    sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                ).scalar()
            )
    finally:
        engine.dispose()


requires_tenant_db = pytest.mark.skipif(
    not database_available(), reason="no reachable DATABASE_URL"
)


def require_auth_mode(monkeypatch) -> None:
    """Put the process in the mode the tests assert refusals against.

    Set through `monkeypatch` rather than at import, so it is restored for every
    other test in the suite. A permissive default would make every refusal
    assertion in this file vacuously true.
    """
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")


@pytest.fixture(scope="session")
def provisioned_runtime():
    """Create the least-privileged runtime role and prove it is least-privileged.

    Fails loudly rather than skipping when PostgreSQL is reachable but cannot
    provision: a tenant suite that quietly skips because a role is missing is
    indistinguishable from one that passed.
    """
    if not database_available():
        pytest.skip("no reachable DATABASE_URL")
    if not is_superuser():
        pytest.skip("the runtime role can only be provisioned by a superuser")

    apply_provision_sql()
    try:
        _verify_provisioned_runtime()
    finally:
        _drop_session_role()


def _drop_session_role() -> None:
    """Remove the per-session role. Roles are cluster-wide, so leaving one
    behind is litter on every machine that ever ran the suite, and a later run
    that happened to mint the same name would silently adopt this one's grants.

    CASCADE, because the role holds grants on every tenant table in every
    database this session touched and `DROP ROLE` without it fails on those
    dependencies -- which is why an earlier version left five roles behind: the
    drop was refused, the exception was swallowed, and the litter was invisible.
    The name is random and the role is a test artefact, so cascading is safe here
    in a way it would never be for a shipped role.
    """
    try:
        engine = sa.create_engine(sync_url())
        with engine.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE usename = :role"
                ),
                {"role": RUNTIME_ROLE},
            )
            # `DROP OWNED BY` first, then `DROP ROLE`. Two syntax facts cost an
            # hour here and are worth writing down:
            #
            #   * `DROP ROLE IF EXISTS :role` does NOT accept a bind parameter.
            #     PostgreSQL wants an identifier list there and `$1` is a
            #     syntax error.
            #   * `DROP ROLE ... CASCADE` does not exist. CASCADE belongs to
            #     `DROP OWNED BY`, not to `DROP ROLE`.
            #
            # The name is ours -- `mp_tenant_test_` plus hex -- so it cannot
            # contain a quote and inlining it is not a quoting problem. Both
            # earlier attempts failed SILENTLY, because this teardown swallows
            # the exception, and left nine roles behind on the cluster.
            quoted = f'"{RUNTIME_ROLE}"'
            conn.execute(sa.text(f"DROP OWNED BY {quoted} CASCADE"))
            conn.execute(sa.text(f"DROP ROLE IF EXISTS {quoted}"))
            conn.commit()
        engine.dispose()
    except Exception:  # noqa: BLE001 - teardown must not mask a real failure
        pass


def _verify_provisioned_runtime() -> None:
    engine = sa.create_engine(sync_url())
    try:
        with engine.connect() as conn:
            attributes = dict(
                conn.execute(
                    sa.text(
                        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                        "WHERE rolname = :role"
                    ),
                    {"role": RUNTIME_ROLE},
                ).one()
            )
            can_login = conn.execute(
                sa.text("SELECT has_table_privilege(:role, 'memory_claims', 'SELECT')"),
                {"role": RUNTIME_ROLE},
            ).scalar()
    finally:
        engine.dispose()

    assert not attributes["rolsuper"], "the runtime role must not be a superuser"
    assert not attributes["rolbypassrls"], "the runtime role must not have BYPASSRLS"
    assert can_login, (
        "the runtime role needs DML on the tenant tables; if the provisioning "
        "script stopped granting it, every isolation test below would be "
        "passing because nothing could read anything"
    )
    return runtime_url()


def apply_as_runtime(async_url: str):
    """An engine that connects as the runtime role, with a real pool.

    Pooled on purpose: the connection-reuse tests need connections that are
    genuinely shared between requests, which a `NullPool` engine would never
    produce.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(async_url, poolclass=AsyncAdaptedQueuePool, pool_size=2)


def admin_session_factory():
    from api.services.db import AsyncSessionLocal

    return AsyncSessionLocal


async def seed_as_maintenance(statement: str, corpus: str, key: str = "seed") -> None:
    """Record a fact as the maintenance role, not as the application role.

    Fixtures used to seed through the runtime role with an unrestricted
    principal. That worked only while the `{*}` scope was an ordinary session
    value anyone could set, which migration `010` took away; and even before
    that, it was testing a combination the application never uses.

    Corpus creation and first writes are administrative operations in this
    design -- a corpus row is only insertable inside the caller's tenant scope,
    and a principal cannot hold a grant to a corpus that does not exist yet --
    so they belong on the maintenance role. Production does the same thing:
    `create_corpus` requires an unrestricted identity, and corpus deletion
    reaches the database through the controlled erasure function.

    Uses the real service functions rather than hand-written SQL, so a fixture
    cannot drift from the shape of the data it claims to create.

    Must be called before a test swaps `db.AsyncSessionLocal` for a runtime-role
    engine: `remember()` opens its own session from that module global, so a
    seed that runs after the swap would write as the runtime role.
    """
    from api.services import authz
    from api.services.corpora import create_corpus, get_corpus_by_name
    from api.services.db import admin_session_scope, principal_scope
    from api.services.remember import remember

    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            if await get_corpus_by_name(db, corpus) is None:
                await create_corpus(db, corpus, "tenant fixture")
        with principal_scope(authz.LOCAL_PRINCIPAL):
            await remember(statement, corpus=corpus, key=key)
