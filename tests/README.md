# Tests

`pytest -q -rs` from the repository root is the whole suite. This file is about
the two environment variables that decide **which database it runs against**,
because they do not name the same database and nothing in a passing run used to
say so.

## The two variables

| variable | who reads it | what it means |
|---|---|---|
| `DATABASE_URL` | the product, `api/services/db.py`, the CLI, the SDK | the database the application connects to |
| `MEMORY_TEST_DATABASE_URL` | `tests/test_memory_core.py::memory_db` and a few benchmark helpers | the database those tests may create a throwaway PostgreSQL **schema** in |

## What the session hook does to them

`tests/conftest.py::pytest_sessionstart` creates a database called
`mindpalace_test_<pid>_<counter>`, runs `alembic upgrade head` and
`scripts/db/runtime_roles.sql` against it, and **repoints `DATABASE_URL` at it**.
At session end it drops it with `FORCE`.

**`MEMORY_TEST_DATABASE_URL` is not rewritten.** That is deliberate and is not
an oversight:

* What the variable means is "the database these tests may create schemas in".
  Quietly redirecting it would change the meaning of a variable, and the point
  of the session database is that a suite never depends on a database a human
  also uses.
* Because it is *not* rewritten, on CI -- where both variables are set -- part
  of the suite runs against the session database and part runs against the
  shared `mindpalace` database. That is a real split and it has caused a real
  confusion.

So the session prints, **once**, which database each variable names:

```
[tests] DATABASE_URL was            mindpalace on localhost:5432
[tests] DATABASE_URL is now         mindpalace_test_1234_0 on localhost:5432  (built and dropped here)
[tests] MEMORY_TEST_DATABASE_URL is  mindpalace on localhost:5432
[tests] NOTE: MEMORY_TEST_DATABASE_URL names a DIFFERENT database from the session one.
[tests]       Tests that read it create their schemas there, not here.
```

Set `MIND_PALACE_TEST_DB=off` to skip the session database and run against
`DATABASE_URL` as-is. The suite prints that it is doing so. Use it only when the
contents of that database are the thing under examination.

## Two ways this has already gone wrong

**A stale session database.** `mindpalace_test_<pid>_<counter>` is dropped at
teardown, so a killed run leaves one behind. `create_session_database` drops any
name it is about to use before creating it, but a *different* pid's leftovers
stay, and a run that collides with them fails in ways that look like product
defects. Measured this milestone: one six-failure run, from a database left by a
killed session.

**A skip that lied.** Six tests in `tests/test_ingestion_statistics.py` skipped
on every CI runner for four sessions with the reason `no reachable DATABASE_URL`.
The database was reachable. SQLAlchemy 2.1 resolves a bare `postgresql://` to
psycopg (v3); this project installs `psycopg2-binary`; and a bare
`except Exception: return False` turned the `ModuleNotFoundError` into "no
database". Reproduced 2026-10-13 against one reachable database, changing only
the version: SQLAlchemy 2.0.46 -> 6 passed, 2.1.4 -> 6 skipped.

`scripts/check_skips.py` now fails the build on any skip outside an allowlist
whose entries name their reason, so that class of silent skip cannot come back.

## What is allowed to skip

Exactly two tests, both opt-in for a substantive reason, both run deliberately
by CI in their own steps:

| test | variable | why |
|---|---|---|
| `test_migrations_apply_downgrade_and_reapply` | `MIND_PALACE_MIGRATION_ROUNDTRIP=1` | creates and drops a database |
| `test_a_built_wheel_contains_every_declared_module` | `MIND_PALACE_TEST_BUILD_WHEEL=1` | builds a wheel, which takes minutes |

Adding a third entry means writing down why. Widening the allowlist to make a
build green is not that.

## Models

The suite loads `sentence-transformers/all-MiniLM-L6-v2` and
`cross-encoder/ms-marco-MiniLM-L-6-v2` from the local HuggingFace cache. CI
provisions both at pinned revisions before running anything
(`scripts/ci/provision_models.py`) and exports `HF_HUB_OFFLINE=1` for the run
itself. A machine without them will fail roughly 41 tests with
`OSError: We couldn't connect to ...`; that is a missing model, not a defect.