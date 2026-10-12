"""Give each pytest session its own database, and take it away afterwards.

## Why this exists

The suite used to run against whatever `DATABASE_URL` pointed at, forever. That
makes every run depend on the history of every previous run, and the dependency is
invisible: nothing in a test says "this row was here before I started".

It was not hypothetical. The working development database carried a policy on
`memory_documents` that compared the *document* id against a tenant scope holding
*corpus* ids:

```sql
USING (mindpalace_allowed_corpus(id))          -- should be (corpus_id)
```

Every row failed that predicate, so the least-privileged role saw an empty
archive and every write was refused. Five isolation tests failed. The same tests
passed on a database built from `migrations/`, and on a *truncated copy of the
working database* they still failed -- so the cause was not the accumulated data
either, it was that one expression, hand-written into a long-lived database at a
moment when somebody was trying to get something else working.

A suite that shares a database with a human cannot distinguish "the code is
wrong" from "someone changed the database". This module removes the category.

## What it does

At session start, if `MIND_PALACE_TEST_DB=session` (the default):

1. connect to the maintenance database named in `DATABASE_URL`;
2. `CREATE DATABASE mindpalace_test_<pid>_<n>`;
3. point `DATABASE_URL` at it, so every test, fixture and URL helper that reads
   the environment sees the new database;
4. run `alembic upgrade head` and `scripts/db/runtime_roles.sql` against it.

At session end, `DROP DATABASE ... WITH (FORCE)`.

The result is that the schema under test is whatever `migrations/` says it is,
built during this session, and that nothing survives it.

## Opting out

`MIND_PALACE_TEST_DB=off` runs against `DATABASE_URL` as-is, and prints that it
is doing so. Use it only when deliberately pointing the suite at a database whose
contents are the thing under examination.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]

#: The maintenance database used to issue CREATE/DROP DATABASE. It must already
#: exist; the suite never creates a cluster.
MAINTENANCE_DB = os.getenv("MIND_PALACE_TEST_MAINTENANCE_DB", "postgres")

#: The maintenance database used to issue CREATE/DROP DATABASE. It must already
#: exist; the suite never creates a cluster.
MAINTENANCE_DB = os.getenv("MIND_PALACE_TEST_MAINTENANCE_DB", "postgres")


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, ""))


def _sync_url(url: str) -> str:
    """Force the psycopg2 driver, whatever the scheme in `DATABASE_URL` says.

    A bare `postgresql://` leaves the driver to SQLAlchemy, and the answer
    changed underneath this file: SQLAlchemy 2.0 resolved it to psycopg2,
    2.1 resolves it to psycopg (v3). CI installs `psycopg2-binary` and not
    `psycopg`, so on a 2.1 runner every `CREATE DATABASE` raised
    `No module named 'psycopg'`, the session database was never created, and
    the suite quietly fell back to running against the shared `mindpalace`
    database -- printing "this run is NOT hermetic" and going on to report
    eight skips as though they were the design.

    The whole suite was therefore passing on a database it did not build, on
    one runner version, and would fail on the next. `tenant_harness.sync_url`
    pins the driver for exactly this reason; this module had to as well.

    No new dependency: psycopg2 is already a runtime requirement, so pinning
    names a driver the project ships with instead of adding one it does not.
    """
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def _maintenance_engine(base_url: str):
    import sqlalchemy as sa

    url = _sync_url(_with_database(base_url, MAINTENANCE_DB))
    return sa.create_engine(url, isolation_level="AUTOCOMMIT")


def _database_statement(base_url: str, statement: str) -> None:
    """Run one CREATE/DROP DATABASE with the autocommit they require.

    Neither can run inside a transaction block. SQLAlchemy opens one by default,
    hence the explicit AUTOCOMMIT engine rather than a psql subprocess: psql is
    not on PATH everywhere, and a test harness that only works on the machine
    where it was written is not a harness.
    """
    engine = _maintenance_engine(base_url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(statement)
    finally:
        engine.dispose()


def _database_name() -> str:
    # The pid keeps two concurrent sessions apart; the counter keeps two sessions
    # in the same second apart, which a reused pid would otherwise collide on.
    counter = os.environ.get("MIND_PALACE_TEST_DB_COUNTER", "0")
    return f"mindpalace_test_{os.getpid()}_{counter}"


def create_session_database(base_url: str) -> str:
    """Build a fresh database and return the URL the suite should use."""
    from tenant_harness import apply_provision_sql

    name = _database_name()
    _database_statement(base_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    _database_statement(base_url, f'CREATE DATABASE "{name}"')

    url = _with_database(base_url, name)
    previous, os.environ["DATABASE_URL"] = os.environ["DATABASE_URL"], url
    try:
        migrations = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", "upgrade", "head"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if migrations.returncode != 0:
            raise RuntimeError(f"alembic upgrade head failed:\n{migrations.stderr.strip()}")

        # `apply_provision_sql` reads DATABASE_URL from the environment, which now
        # points at the new database. It resolves the script's psql variables and
        # runs the shipped file, so the suite provisions from the artifact rather
        # than from a copy of it.
        apply_provision_sql()
    finally:
        os.environ["DATABASE_URL"] = previous

    return url


def drop_session_database(base_url: str) -> None:
    _database_statement(base_url, f'DROP DATABASE IF EXISTS "{_database_name()}" WITH (FORCE)')
