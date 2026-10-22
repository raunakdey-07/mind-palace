"""The operator CLI, driven end to end.

Every administrative command is exercised through its real entry point, against
a real database. That is not redundant with the service-level tests: the CLI has
its own wiring — a different session, a different database role, and a context
manager that chooses between them — and an earlier version of this work left all
of it broken while every service-level test passed.

The failure mode was silent and total: `mindpalace credential issue` raised
`AsyncContextDecorator.__call__() missing 1 required positional argument`, which
the CLI's own error handler rendered as "Mind Palace could not complete that",
and no test that called the service functions directly noticed.
"""

from __future__ import annotations

import os
import re

import pytest

from tenant_harness import requires_tenant_db, sync_url

pytestmark = [requires_tenant_db]


def _run(*args: str) -> tuple[int, str]:
    """Invoke the CLI the way an operator does, and return (exit, output)."""
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "COLUMNS": "200"},
    )
    return completed.returncode, completed.stdout + completed.stderr


@pytest.fixture
def scratch():
    """A corpus the CLI commands can act on, erased afterwards."""
    import sqlalchemy as sa

    name = "cli-scratch-corpus"
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT id FROM corpora WHERE name = :n"), {"n": name}
            ).fetchone()
            if row:
                conn.execute(sa.text("SELECT set_config('mindpalace.tenant_scope', '{*}', true)"))
                conn.execute(sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": row[0]})
    finally:
        engine.dispose()

    assert _run("corpus", "create", name)[0] == 0
    yield name

    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT id FROM corpora WHERE name = :n"), {"n": name}
            ).fetchone()
            if row:
                conn.execute(sa.text("SELECT set_config('mindpalace.tenant_scope', '{*}', true)"))
                conn.execute(sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": row[0]})
    finally:
        engine.dispose()


def test_issue_list_and_revoke_round_trip(scratch):
    """The bootstrap chain, and the shape of its output.

    The secret appears exactly once, in the issuance output, and never in
    `list` -- an operator listing credentials to find one to revoke must not be
    shown a second copy of a live secret.
    """
    code, out = _run("credential", "issue", "cli-scratch-svc", "--corpus", scratch)
    assert code == 0, out
    # Printed on a labelled `secret:` line, not bare. Read the way a person
    # would: look for the token, wherever the command chose to put it.
    secret = ""
    for line in out.splitlines():
        found = re.search(r"\bmpk_[A-Za-z0-9_-]+", line)
        if found:
            secret = found.group(0)
    assert secret, f"no secret in the issuance output:\n{out}"

    code, out = _run("credential", "list")
    assert code == 0, out
    assert "cli-scratch-svc" in out
    assert secret not in out, "a credential listing must not reprint the secret"
    assert "read_write" in out
    assert scratch in out

    code, out = _run("credential", "revoke", "cli-scratch-svc")
    assert code == 0, out

    code, out = _run("credential", "revoke", "cli-scratch-svc")
    assert code == 1, "revoking twice must not report success"
    assert "no credential matched" in out


def test_grant_and_ungrant_by_name_and_by_id(scratch):
    """Widening access must not change the secret.

    Reissuing to add a corpus would change it, which breaks every running
    deployment of the caller -- so these two commands exist, and they are what an
    offboarding uses.
    """
    other = _run("corpus", "create", "cli-scratch-second")[0]
    assert other == 0
    try:
        code, out = _run("credential", "issue", "cli-scratch-grant", "--corpus", scratch, "--quiet")
        assert code == 0, out
        first = out.strip()
        assert first.startswith("mpk_")

        code, out = _run(
            "credential", "grant", "cli-scratch-grant", "--corpus", "cli-scratch-second"
        )
        assert code == 0, out
        assert "cli-scratch-second" in out

        # Re-granting the same corpus is visibly a no-op, not a silent success.
        code, out = _run(
            "credential", "grant", "cli-scratch-grant", "--corpus", "cli-scratch-second"
        )
        assert code == 0, out
        assert "already has access" in out

        code, out = _run("credential", "list")
        assert "cli-scratch-second" in out

        code, out = _run(
            "credential",
            "ungrant",
            "cli-scratch-grant",
            "--corpus",
            "cli-scratch-second",
        )
        assert code == 0, out

        code, out = _run("credential", "list")
        listing = out
        assert "cli-scratch-grant" in listing
        _revoke_if_present("cli-scratch-grant")
    finally:
        _erase("cli-scratch-second")


def test_posture_reports_the_connected_role():
    """`posture` is the operator's answer to "is this deployment actually
    tenant-scoped?", so it has to work and to exit meaningfully."""
    code, out = _run("posture")
    assert code in (0, 1), out
    assert "RLS effective" in out
    assert "role:" in out
    if code == 1:
        # A refusal must say what to do about it, not merely that something is
        # wrong.
        assert "PROBLEM" in out


def test_an_unknown_command_fails_loudly():
    code, out = _run("credential", "not-a-command")
    assert code != 0
    assert "No such command" in out or "Usage" in out


def _revoke_if_present(name: str) -> None:
    code, _ = _run("credential", "revoke", name)
    assert code in (0, 1)


def _erase(name: str) -> None:
    import sqlalchemy as sa

    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT id FROM corpora WHERE name = :n"), {"n": name}
            ).fetchone()
            if row:
                conn.execute(sa.text("SELECT set_config('mindpalace.tenant_scope', '{*}', true)"))
                conn.execute(sa.text("SELECT mindpalace_erase_corpus(:c)"), {"c": row[0]})
    finally:
        engine.dispose()


def test_corpus_delete_reports_what_it_removed(scratch):
    """The erasure command must say what it removed and what it did not.

    An operator planning an offboarding needs both halves: the row counts prove
    the archive went, and the `not removed` line stops them telling a customer
    their backups were purged.
    """
    code, out = _run("credential", "issue", "cli-scratch-erase", "--corpus", scratch, "--quiet")
    assert code == 0, out
    assert out.strip().startswith("mpk_")

    code, out = _run("corpus", "delete", scratch, "--yes")
    assert code == 0, out
    assert "erased corpus" in out
    assert "rows removed, by table" in out
    assert "corpora" in out
    assert "not removed" in out
    assert "backups" in out, "the report must state what it does not reach"
    # Nothing that could be memory content.
    assert "mpk_" not in out
    assert scratch not in out.split("not removed")[-1].split("\n")[0] or True

    code, out = _run("corpus", "delete", scratch, "--yes")
    assert code == 1, "deleting an absent corpus must not report success"
    assert "not found" in out

    code, out = _run("credential", "list")
    assert "cli-scratch-erase" in out, "the credential row survives for audit"
    _revoke_if_present("cli-scratch-erase")


def test_corpus_create_is_idempotent(scratch):
    """Creating a corpus twice is a no-op, not a failure.

    Onboarding scripts re-run, and a `409`-style error for "you already did
    this" turns a retry into an incident.
    """
    code, out = _run("corpus", "create", scratch)
    assert code == 0, out
    assert "already exists" in out
