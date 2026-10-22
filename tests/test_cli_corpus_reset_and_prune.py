"""`ingest-repo --delete-removed` and `corpus reset`.

Two ways a scratch corpus goes stale, and one way out of each.

**A file removed from a curated directory stayed active in the corpus
forever.** Its claims kept answering questions and nothing said the source was
gone. `ingest-repo` hardcoded `delete_removed=False`, so the service could
retire a document and the CLI never asked for it.

**A corpus that had collected test claims needed a clean slate**, and the only
way to get one was to drop the whole PostgreSQL database, which took every
other corpus with it. `corpus reset` wipes one corpus and re-initialises it.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

DOC = """---
title: Checkout
claims:
  - key: checkout.datastore
    value: PostgreSQL
    claim: The checkout service uses PostgreSQL.
    evidence: The checkout service uses PostgreSQL.
---

# Checkout

The checkout service uses PostgreSQL. We moved it in March.
"""

OTHER_DOC = """---
title: Billing
claims:
  - key: billing.datastore
    value: MySQL
    claim: The billing service uses MySQL.
    evidence: The billing service uses MySQL.
---

# Billing

The billing service uses MySQL.
"""


@pytest.fixture
def corpus(request) -> str:
    """A corpus of this test's own.

    Not `default`: `tests/test_ingestion_lifecycle.py::test_metadata_only_change_is_detected`
    documents that `default` only stays empty while nothing has written there,
    and ingesting into it from a new test file breaks that test.
    """
    return "corpus-reset-" + request.node.name.replace("_", "-")[:40]


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "checkout.md").write_text(DOC)
    return d


def _invoke(args, roots: str | None = None):
    """Run the CLI with a controlled ingest-root environment."""
    from cli.main import app

    env = dict(os.environ)
    if roots is None:
        env.pop("MIND_PALACE_INGEST_ROOTS", None)
    else:
        env["MIND_PALACE_INGEST_ROOTS"] = roots
    return CliRunner().invoke(app, args, env=env)


def _claims(corpus: str) -> list[str]:
    """The claim texts a corpus currently answers with.

    The archive is append-only, so a retired document keeps its version and
    gains a DELETED tombstone. What the reader sees is the public projection,
    and that is what the assertions are about.
    """
    import asyncio

    from api.services import memory
    from api.services.corpora import get_corpus_by_name
    from api.services.db import session_scope

    async def read() -> list[str]:
        async with session_scope() as db:
            row = await get_corpus_by_name(db, corpus)
            if row is None:
                return []
            result = await memory.query(db, row["id"])
            return [c["claim"] for c in result["current_memories"]]

    return asyncio.run(read())


def test_ingest_repo_retires_the_documents_that_left_the_directory(repo, corpus):
    """The document is gone from the source, so it is gone from the corpus.

    Without this, a file the author deleted keeps answering questions for as
    long as the corpus lives.
    """
    first = _invoke(["ingest-repo", str(repo), "--corpus", corpus], roots=str(repo))
    assert first.exit_code == 0, first.output
    assert "The checkout service uses PostgreSQL." in _claims(corpus)

    (repo / "checkout.md").unlink()

    second = _invoke(
        ["ingest-repo", str(repo), "--corpus", corpus, "--delete-removed"], roots=str(repo)
    )
    assert second.exit_code == 0, second.output
    assert "-1" in second.output, f"the sync did not report the retirement: {second.output}"
    assert "The checkout service uses PostgreSQL." not in _claims(corpus)


def test_ingest_repo_without_the_flag_keeps_them(repo, corpus):
    """The control: a sync that retires documents is a decision.

    The flag is how the caller makes it, and the default is unchanged.
    """
    first = _invoke(["ingest-repo", str(repo), "--corpus", corpus], roots=str(repo))
    assert first.exit_code == 0, first.output

    (repo / "checkout.md").unlink()

    second = _invoke(["ingest-repo", str(repo), "--corpus", corpus], roots=str(repo))
    assert second.exit_code == 0, second.output
    assert "The checkout service uses PostgreSQL." in _claims(corpus)


def test_a_reset_wipes_one_corpus_and_leaves_the_others_alone(repo, corpus, tmp_path):
    """The scratch corpus starts again, and the rest of the database does not.

    The way out of a polluted corpus used to be dropping the whole database
    through `podman exec`, which took every other corpus with it.
    """
    other = corpus + "-other"
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "billing.md").write_text(OTHER_DOC)

    assert _invoke(["ingest-repo", str(repo), "--corpus", corpus], roots=str(repo)).exit_code == 0
    assert (
        _invoke(["ingest-repo", str(other_dir), "--corpus", other], roots=str(other_dir)).exit_code
        == 0
    )
    assert "The checkout service uses PostgreSQL." in _claims(corpus)
    assert "The billing service uses MySQL." in _claims(other)

    reset = _invoke(["corpus", "reset", corpus, "--yes"])
    assert reset.exit_code == 0, reset.output

    assert _claims(corpus) == [], "the reset corpus still holds its claims"
    assert "The billing service uses MySQL." in _claims(
        other
    ), "the reset took another corpus with it"


def test_a_reset_leaves_the_corpus_there(repo, corpus):
    """A reset is not a delete.

    The corpus comes back, empty, under the same name, so the commands that
    name it keep working.
    """
    assert _invoke(["ingest-repo", str(repo), "--corpus", corpus], roots=str(repo)).exit_code == 0

    reset = _invoke(["corpus", "reset", corpus, "--yes"])
    assert reset.exit_code == 0, reset.output

    listed = _invoke(["corpora"])
    assert corpus in listed.output, f"the corpus is gone after a reset: {listed.output}"


def test_a_reset_asks_before_it_erases(repo, corpus):
    """Erasure cannot be undone, so the operator says so."""
    assert _invoke(["ingest-repo", str(repo), "--corpus", corpus], roots=str(repo)).exit_code == 0

    refused = _invoke(["corpus", "reset", corpus])
    assert refused.exit_code != 0
    assert "The checkout service uses PostgreSQL." in _claims(
        corpus
    ), "the corpus was erased without being asked to"


def test_ingest_repo_still_needs_no_server(repo, corpus):
    """The flag did not bring the HTTP hop back."""
    with patch("httpx.post", side_effect=AssertionError("must not use HTTP")):
        result = _invoke(
            ["ingest-repo", str(repo), "--corpus", corpus, "--delete-removed"], roots=str(repo)
        )

    assert result.exit_code == 0, result.output
