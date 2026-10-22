"""Shared test fixtures for Mind Palace."""

import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# M009 cursor tests exercise the real HMAC boundary. Production configuration
# supplies this value explicitly; tests use a deterministic non-secret value.
os.environ.setdefault("MIND_PALACE_CURSOR_SECRET", "m009-test-cursor-secret-" + "x" * 48)

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: The repository root, for the paths a fixture has to reach.
ROOT = Path(__file__).resolve().parent.parent


def pytest_sessionstart(session) -> None:
    """Run the suite against a database this session builds and then destroys.

    See `tests/session_database.py` for why. The short version: a suite that
    shares a database with a human cannot tell "the code is wrong" from "someone
    changed the database", and that distinction cost a full investigation this
    milestone.
    """
    import session_database

    if os.getenv("MIND_PALACE_TEST_DB", "session").lower() == "off":
        print(
            "\n[tests] MIND_PALACE_TEST_DB=off: running against DATABASE_URL as-is. "
            "Results depend on whatever is already in that database."
        )
        return

    base_url = os.environ.get("DATABASE_URL") or os.environ.get("MEMORY_TEST_DATABASE_URL")
    if not base_url:
        # Previously this returned quietly and every database-backed test skipped.
        # The suite then exited 0 having run nothing: 1645 skipped in 9.75 s, and
        # a green build that proved nothing. `check_skips.py` validates that the
        # skips it sees are allowlisted, not that anything ran at all.
        #
        # A silent skip-all is worse than a loud failure, so this stops the
        # session instead. Set either variable to a PostgreSQL that has pgvector.
        #
        # UsageError rather than RuntimeError: pytest prints it as a plain
        # message and exits 4. A RuntimeError here surfaces as INTERNALERROR and
        # a traceback, which is true but reads like a broken checkout.
        raise pytest.UsageError(
            "no database configured, so every database-backed test would skip and "
            "this run would exit 0 having proved nothing.\n"
            "Set DATABASE_URL (or MEMORY_TEST_DATABASE_URL) to a PostgreSQL with "
            "pgvector available, for example:\n"
            "  docker compose up -d postgresql\n"
            "  export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace\n"
            "  python -m alembic -c migrations/alembic.ini upgrade head\n"
            "To run deliberately against a shared database without creating a "
            "session one, set MIND_PALACE_TEST_DB=off."
        )

    # `create_session_database` saves and restores `os.environ["DATABASE_URL"]`
    # directly, so the variable has to exist before it is called. Setting it
    # here also means a run configured only through MEMORY_TEST_DATABASE_URL
    # gets a session database instead of a directory full of skips.
    os.environ.setdefault("DATABASE_URL", base_url)

    try:
        os.environ["DATABASE_URL"] = session_database.create_session_database(base_url)
        _announce_databases(base_url, os.environ["DATABASE_URL"])
    except Exception as exc:  # noqa: BLE001 - re-raised below, deliberately
        # A non-hermetic run is worse than no run.
        #
        # This used to print the reason and carry on against `DATABASE_URL`
        # as-is. On CI that meant the entire suite passed against the SHARED
        # `mindpalace` database -- the exact situation this module exists to
        # prevent -- while reporting a green result and eight skips that read
        # like the design. The failure was loud in the log and silent in the
        # exit code, which is the worst place for it to be loud.
        #
        # So it now stops the session. A missing privilege to CREATE DATABASE
        # is an environment problem, and the way to fix it is documented rather
        # than worked around. `MIND_PALACE_TEST_DB=off` is the explicit opt-out
        # for anyone who genuinely wants the shared database -- and it still
        # prints what it is doing.
        if os.getenv("MIND_PALACE_TEST_DB_ALLOW_NONHERMETIC", "").strip() == "1":
            print(
                f"\n[tests] could not create a session database: {exc}\n"
                "[tests] MIND_PALACE_TEST_DB_ALLOW_NONHERMETIC=1: falling back to "
                "DATABASE_URL as-is. This run is NOT hermetic."
            )
            return
        raise pytest.UsageError(
            f"could not create a session database, so this run would not be "
            f"hermetic: {exc}\n"
            "Refusing to run the suite against DATABASE_URL as-is, because a "
            "shared database cannot distinguish 'the code is wrong' from "
            "'someone changed the database'.\n"
            "Fix the environment (the role needs CREATEDB, or the maintenance "
            f"database {session_database.MAINTENANCE_DB!r} must exist), or set "
            "MIND_PALACE_TEST_DB_ALLOW_NONHERMETIC=1 to accept a non-hermetic "
            "run deliberately."
        ) from exc

    session.config._mindpalace_session_db = os.environ["DATABASE_URL"]  # noqa: SLF001


def pytest_sessionfinish(session, exitstatus) -> None:
    import session_database

    url = getattr(session.config, "_mindpalace_session_db", None)
    if url:
        try:
            session_database.drop_session_database(url)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask results
            print(f"\n[tests] could not drop the session database: {exc}")


def _announce_databases(base_url: str, session_url: str) -> None:
    """Print, once per session, which database each variable actually names.

    Two variables point at PostgreSQL and they do not agree, and that has cost a
    full investigation: `tests/test_memory_core.py::memory_db` reads
    `MEMORY_TEST_DATABASE_URL` first and `DATABASE_URL` second, while this hook
    rewrites `DATABASE_URL` to a database it creates for this session. On CI
    both are set, so half the suite runs against the session database and half
    against the shared one, and nothing in the output says which is which.

    `MEMORY_TEST_DATABASE_URL` is deliberately NOT rewritten: what it means is
    "the database these tests may create schemas in", and quietly pointing it
    somewhere else would change what a variable means. Its absence is the
    interesting case, so it is printed as absent rather than omitted.
    """
    from urllib.parse import urlsplit

    def name_of(url: str | None) -> str:
        if not url:
            return "(unset)"
        path = urlsplit(url).path.lstrip("/")
        return f"{path} on {urlsplit(url).hostname or '?'}:{urlsplit(url).port or 5432}"

    override = os.environ.get("MEMORY_TEST_DATABASE_URL")
    print(f"\n[tests] DATABASE_URL was            {name_of(base_url)}")
    print(f"[tests] DATABASE_URL is now         {name_of(session_url)}  (built and dropped here)")
    print(
        f"[tests] MEMORY_TEST_DATABASE_URL is  {name_of(override)}"
        + ("" if override else "  (unset: these tests fall back to the session database)")
    )
    if override and urlsplit(override).path.lstrip("/") != urlsplit(session_url).path.lstrip("/"):
        print(
            "[tests] NOTE: MEMORY_TEST_DATABASE_URL names a DIFFERENT database from the "
            "session one.\n[tests]       Tests that read it create their schemas there, "
            "not here."
        )


@pytest.fixture(scope="session", autouse=True)
def session_recorder_isolation(tmp_path_factory):
    """Point the flight recorder at a scratch file for the whole session.

    The recorder is on by default for a loopback connection, which is exactly what
    the test session's database is. So every product call the suite made appended
    a row to the developer's real `~/.mind-palace/logs/queries.jsonl`: 32,738 rows
    of fake product facts -- battery runtimes, mesh bands, campaign targets -- in
    the file whose entire purpose is grading two weeks of actual usage.

    The test suite exercises the recorder through the product's own path, so it
    cannot simply be switched off; `test_query_log.py` sets the variable itself,
    and it is the one place that should decide where rows go. A session-scoped
    default that every test inherits and the few that care about override.

    Applied by mutating `os.environ` rather than through `monkeypatch`, because
    `monkeypatch` is function-scoped and this is not. The previous value is put
    back so running pytest from inside another pytest is not surprising.

    The teardown check is what makes this hold. Setting the variable covers every
    subprocess that inherits the environment; the handful that hand-build their
    own `env=` dict -- `test_m015_golden_path.py` runs the documented CLI sequence
    that way, on purpose -- do not, and each one has to pass `recorder_path()`
    explicitly. A new one that forgets would put invented rows back into the user's
    log and nothing else would notice, so the count is compared on the way out and
    the suite fails naming the file.
    """
    home = Path(os.path.expanduser("~/.mind-palace")) / "logs" / "queries.jsonl"
    before = _row_count(home)
    scratch = tmp_path_factory.mktemp("flight-recorder") / "queries.jsonl"
    previous = os.environ.get("MIND_PALACE_QUERY_LOG")
    os.environ["MIND_PALACE_QUERY_LOG"] = str(scratch)
    yield scratch
    if previous is None:
        os.environ.pop("MIND_PALACE_QUERY_LOG", None)
    else:
        os.environ["MIND_PALACE_QUERY_LOG"] = previous
    after = _row_count(home)
    assert after == before, (
        f"the test suite added {after - before} row(s) to {home}. A subprocess "
        "built its own `env=` dict and did not pass MIND_PALACE_QUERY_LOG; "
        "request the `recorder_path` fixture and pass it in that dict."
    )


def _row_count(path: Path) -> int:
    """Lines in a JSONL log, or 0 when it does not exist."""
    return len(path.read_text().splitlines()) if path.exists() else 0


@pytest.fixture(scope="session")
def recorder_path():
    """The session's scratch log path, for a subprocess that inherits nothing.

    A hand-built `env=` dict does not inherit `MIND_PALACE_QUERY_LOG`, so a CLI
    subprocess started that way resolves the recorder's default and writes into
    the developer's home directory. Request this and pass the value as
    `"MIND_PALACE_QUERY_LOG": recorder_path` in that dict.

    A fixture rather than a module-level function because the session fixture that
    sets the variable must be the single source of the path; a function here would
    read the environment at call time and be right only by accident.
    """
    return os.environ.get("MIND_PALACE_QUERY_LOG", "0")


@pytest.fixture(scope="session", autouse=True)
def session_runtime_role():
    """Provision the least-privileged role once per session, then remove it.

    `autouse` because the role is CLUSTER state, not per-test state: one role per
    session, and every test that reaches the database expects it to exist. Asking
    each test to request it would mean a test that forgot would fail in a way that
    reads like a product defect.

    The body lives in `tenant_harness.provisioned_runtime`, which is where the
    role name, the provisioning and the cluster-wide teardown are defined. It is
    delegated to rather than written here because pytest only collects fixtures
    from `conftest.py`, and a fixture defined in an imported module -- even with
    `autouse` -- is silently never instantiated. That is precisely how 24 leaked
    roles accumulated: the teardown existed, looked correct, and ran zero times.
    """
    from tenant_harness import provision_session_runtime

    yield from provision_session_runtime()


@pytest.fixture
def sample_markdown() -> str:
    return """---
title: "Test Project"
date: 2024-01-15
tags: ["test", "ai"]
status: Completed
summary: "A test project for validation"
---

# Introduction

This is the introduction section of the test project.

## Methods

We used several methods including:
- Data collection
- Model training
- Evaluation

## Results

The results show significant improvement over baseline.
"""


@pytest.fixture
def sample_query_text() -> str:
    return "What methods were used in the test project?"


# --- The archive corpus fixture -------------------------------------------
#
# A real corpus on the real schema, in a rolled-back transaction. The service
# keeps its real transaction ownership and only the connection belongs to the
# test, so a test that writes and reads sees the same archive a caller would.


@pytest.fixture
async def store(monkeypatch):
    """A real corpus on the real schema, in a rolled-back transaction.

    Same shape as the golden-path fixture: the service keeps its real transaction
    ownership and only the connection belongs to the test.
    """
    from api.services import ingestion as ingestion_service
    from api.services import remember as remember_service

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("set MEMORY_TEST_DATABASE_URL or DATABASE_URL for PostgreSQL tests")
    url = (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_async_engine(url, poolclass=NullPool)
    schema = "xdoc_" + uuid4().hex
    corpus = hashlib.sha256(uuid4().bytes).hexdigest()
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))

                def migrate(sync_conn):
                    import importlib.util
                    from pathlib import Path

                    for path in sorted((Path(ROOT) / "migrations" / "versions").glob("*.py")):
                        spec = importlib.util.spec_from_file_location(path.stem, path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        with Operations.context(MigrationContext.configure(sync_conn)):
                            module.upgrade()

                await conn.run_sync(migrate)
                await conn.execute(
                    text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                    {"id": corpus, "name": schema},
                )
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:

                    class SavepointSession:
                        def begin(self):
                            return db.begin_nested()

                        def __getattr__(self, name):
                            return getattr(db, name)

                    from contextlib import asynccontextmanager

                    @asynccontextmanager
                    async def sessions():
                        yield SavepointSession()

                    # `related_active` opens its own `session_scope`, imported
                    # inside the function, so the patch has to be on the module it
                    # imports from rather than on `remember` itself.
                    #
                    # `sync_session_scope` is the other way a session is opened:
                    # `sync_repo` uses it to hold one connection for the length of
                    # a repository sync. A fixture that replaces one and not the
                    # other sends the sync to the wrong database.
                    from api.services import db as db_module

                    for module in (ingestion_service, remember_service, db_module):
                        for name in ("session_scope", "sync_session_scope"):
                            if hasattr(module, name):
                                monkeypatch.setattr(module, name, sessions)

                    async def resolve(name, corpus_id=None):
                        return corpus

                    monkeypatch.setattr(remember_service, "ensure_corpus", resolve)
                    yield SimpleNamespace(db=db, corpus=corpus, name=schema)
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()
