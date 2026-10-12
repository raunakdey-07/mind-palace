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
