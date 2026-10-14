"""`mindpalace ingest-repo` must work in-process.

It was an HTTP client to a hard-coded ``http://localhost:8000``, so the one
command the README documents for getting a document in was also the only one
needing a server running. Following the README literally, it failed with a bare
``[Errno 111] Connection refused``.

These tests pin the two properties that matter: it works with nothing listening
on 8000, and the ingest-root check the HTTP route performs still applies, so
dropping the HTTP hop did not quietly widen what may be read.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

#: Its own corpus, not `default`.
#:
#: `tests/test_ingestion_lifecycle.py::test_metadata_only_change_is_detected`
#: documents that it only holds when the default corpus has no memory history;
#: once something has written there, ingestion reroutes to the memory path and a
#: frontmatter-only edit is correctly detected, so that test fails on its
#: premise. Ingesting into `default` from a new test file breaks it.
CORPUS = "ingest-repo-in-process"

DOC = """---
title: In-process ingest
claims:
  - key: checkout.datastore
    value: PostgreSQL
    claim: The checkout service uses PostgreSQL.
    evidence: The checkout service uses PostgreSQL.
---

# In-process ingest

The checkout service uses PostgreSQL. We moved it in March.
"""


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "checkout.md").write_text(DOC)
    return d


def _invoke(args, roots: str | None):
    """Run the CLI with a controlled ingest-root environment."""
    import os

    from cli.main import app

    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    if roots is None:
        env.pop("MIND_PALACE_INGEST_ROOTS", None)
    else:
        env["MIND_PALACE_INGEST_ROOTS"] = roots
    return CliRunner().invoke(app, args, env=env)


def test_ingest_repo_needs_no_server(repo):
    """The regression: the command ingests without any HTTP hop at all.

    `httpx.post` is patched to raise, so this passes whether or not something
    happens to be listening on :8000 -- it asserts the code path, not the
    state of the machine.
    """
    with patch("httpx.post", side_effect=AssertionError("must not use HTTP")):
        result = _invoke(["ingest-repo", str(repo), "--corpus", CORPUS], roots=str(repo))

    assert result.exit_code == 0, result.output
    assert "[OK] sync:" in result.output
    assert "chunks" in result.output


def test_ingest_repo_refuses_a_path_outside_the_ingest_roots(repo, tmp_path):
    """The control the HTTP route applies must still apply without the HTTP hop.

    Before this change the 403 came from the route. Dropping the hop would have
    removed it; it is applied in-process instead, and the message does not name
    the allowed roots.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    result = _invoke(["ingest-repo", str(repo), "--corpus", CORPUS], roots=str(allowed))

    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert str(repo) not in result.output


def test_ingest_repo_reports_a_missing_path_instead_of_a_keyerror(tmp_path):
    """A regression this change could have introduced.

    The HTTP route answered `400 Path not found`. In-process, `sync_repo`
    returns a short result dict with no count keys for the same condition, and
    the SDK reads `result["added"]` -- so without an explicit check the user
    would get `Why: 'added'`, which says nothing.
    """
    result = _invoke(
        ["ingest-repo", str(tmp_path / "nope"), "--corpus", CORPUS], roots=str(tmp_path)
    )

    assert result.exit_code == 1
    assert "Path not found" in result.output
    assert "KeyError" not in result.output
