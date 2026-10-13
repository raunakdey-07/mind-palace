"""Shared test fixtures for Mind Palace."""

import os
import sys
from pathlib import Path

import pytest

# M009 cursor tests exercise the real HMAC boundary. Production configuration
# supplies this value explicitly; tests use a deterministic non-secret value.
os.environ.setdefault("MIND_PALACE_CURSOR_SECRET", "m009-test-cursor-secret-" + "x" * 48)

sys.path.insert(0, str(Path(__file__).resolve().parent))


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

    base_url = os.environ.get("DATABASE_URL")
    if not base_url:
        # No database configured. The suite's own skip markers handle this; a
        # session database needs a server to ask.
        return

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
        raise RuntimeError(
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
