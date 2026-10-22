"""One connection for a whole repository sync.

`sync_repo` used to open two sessions per document, and every session was a new
PostgreSQL connection because the engine is `NullPool`. A 78-document
repository paid for 156 connections: 4.5 s of a 34 s ingest against a
container-local database, and 11.8 s against a remote one.

Three promises, and the third is the one that is easy to lose:

* **The run opens one connection.** The cost of a connection is what scales with
  the distance to the database, and a document count is not something a caller
  should pay for twice over.
* **A document that fails is rolled back on its own.** The run carries on, the
  connection stays usable, and the failed document leaves nothing behind.
* **The tenant scope is written per transaction.** The scope is transaction
  local, so a reused connection cannot carry one document's scope into the
  next.

The last two are the ones a pool would quietly break, so they are asserted
rather than assumed.

The database tests run against the schema the product ships, so a failure here
is a failure of the product and not of a fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

#: A document with claim-shaped structure: a bullet under a heading.
CLAIM_BEARING = """---
title: "Connection Reuse"
---

# Decisions

- The production datastore is PostgreSQL.
"""

#: A principal that reaches exactly one corpus, so its scope is a name rather
#: than the unrestricted wildcard.
TENANT = "conn-reuse-tenant"


@pytest.fixture
def repo(tmp_path):
    """A directory of three documents, each of which produces a claim."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (docs / f"{name}.md").write_text(CLAIM_BEARING.replace("Connection Reuse", name))
    return docs


async def _corpus(name: str) -> str:
    """A corpus of this test's own, and its id."""
    from api.services.corpora import get_or_create_corpus
    from api.services.db import session_scope

    async with session_scope() as db:
        corpus = await get_or_create_corpus(db, name)
    return corpus["id"]


async def _paths(corpus_id: str) -> list[str]:
    """The paths the corpus holds, in order."""
    from api.services.db import session_scope

    async with session_scope() as db:
        rows = (
            await db.execute(
                text("SELECT path FROM documents WHERE corpus_id = :c ORDER BY path"),
                {"c": corpus_id},
            )
        ).fetchall()
    return [row[0] for row in rows]


def _count_connections():
    """Count the connections the engine opens, and time nothing else.

    The pool is where a `NullPool` engine opens its DB-API connection, so this
    is the count that matters. It is patched onto the instance and put back
    afterwards.
    """
    from api.services.db import async_engine

    pool = async_engine.sync_engine.pool
    original = pool.connect
    opened: list[int] = []

    def counting(*args, **kwargs):
        opened.append(1)
        return original(*args, **kwargs)

    pool.connect = counting
    return pool, original, opened


async def test_a_sync_opens_one_connection_for_the_whole_run(repo):
    """Three documents, one connection.

    The old code opened two per document. The count is asserted exactly,
    because "fewer" would not catch a regression back to one per document.
    """
    from api.services.ingestion import IngestionService

    corpus_id = await _corpus("sync-reuse-connections")
    pool, original, opened = _count_connections()
    try:
        summary = await IngestionService().sync_repo(str(repo), corpus_id)
    finally:
        pool.connect = original

    assert summary["added"] == 3, summary
    assert len(opened) == 1, f"the sync opened {len(opened)} connections for three documents"


async def test_a_failing_document_is_rolled_back_and_the_run_carries_on(repo, monkeypatch):
    """A document that fails leaves nothing behind, and the others still land.

    The failure is raised after the document row and its chunks have been
    written, so the rollback has something to roll back. A run that kept going
    but left the half-written document behind would be worse than one that
    stopped.
    """
    from api.services import memory as memory_service
    from api.services.ingestion import IngestionService

    corpus_id = await _corpus("sync-reuse-rollback")
    original = memory_service.record_version

    async def failing(db, corpus_id_, path, *args, **kwargs):
        if path == "beta.md":
            raise RuntimeError("this document is broken")
        return await original(db, corpus_id_, path, *args, **kwargs)

    monkeypatch.setattr(memory_service, "record_version", failing)
    summary = await IngestionService().sync_repo(str(repo), corpus_id)

    assert summary["failed"] == 1, summary
    assert summary["added"] == 2, summary
    landed = await _paths(corpus_id)
    assert landed == ["alpha.md", "gamma.md"], landed


async def test_the_tenant_scope_is_written_for_every_document(repo, monkeypatch):
    """A reused connection does not carry one document's scope into the next.

    The scope is written by `set_config(..., is_local := true)`, so it belongs
    to the transaction that wrote it and is discarded when that transaction
    ends. Each document here runs as a different principal, and each one must
    see its own scope: a connection that carried a scope across documents would
    show the previous document's value here.
    """
    from api.services import memory as memory_service
    from api.services.authz import Principal
    from api.services.db import principal_scope
    from api.services.ingestion import IngestionService

    corpus_id = await _corpus("sync-reuse-scope")
    principals = [
        Principal(
            id=f"tenant-{index}",
            name=f"tenant-{index}",
            role="read_write",
            corpora=frozenset({TENANT}),
            corpus_ids=frozenset({f"corpus-id-{index}"}),
        )
        for index in range(3)
    ]
    seen: list[str] = []
    original_lock = memory_service.lock_corpus

    async def recording_lock(db, corpus_id_):
        await original_lock(db, corpus_id_)
        seen.append(
            (
                await db.execute(text("SELECT current_setting('mindpalace.tenant_scope', true)"))
            ).scalar()
        )

    monkeypatch.setattr(memory_service, "lock_corpus", recording_lock)

    original_ingest = IngestionService._ingest_content
    pending = iter(principals)

    async def switching(self, db, content, path, corpus_id_, **kwargs):
        with principal_scope(next(pending)):
            return await original_ingest(self, db, content, path, corpus_id_, **kwargs)

    monkeypatch.setattr(IngestionService, "_ingest_content", switching)
    await IngestionService().sync_repo(str(repo), corpus_id)

    # The scope is read more than once per document, because the corpus lock is
    # taken more than once. What matters is that it never changes within a
    # document and never shows a different document's value. The erasure pass
    # that runs after the documents is not one of them, so it is left out.
    during = [scope for scope in seen if scope != "{*}"]
    distinct = [during[0]] + [after for before, after in zip(during, during[1:]) if before != after]
    assert distinct == ["corpus-id-0", "corpus-id-1", "corpus-id-2"], seen


def test_a_corpus_label_becomes_a_disposable_corpus_name():
    """The benchmark writes into a corpus it can delete.

    The name on the command line is a report label. The corpus it writes into
    carries the disposable prefix, so a benchmark run cannot erase the corpus
    somebody is using.
    """
    from scripts.benchmark.ingest_profile import DISPOSABLE_PREFIX, _disposable_name

    assert _disposable_name("mind-palace") == "_profile_mind_palace"
    assert _disposable_name("cv-embed") == "_profile_cv_embed"
    assert _disposable_name("cv embed") == "_profile_cv_embed"
    # A name that already carries the prefix is used as it stands, so a caller
    # who wants `_profile_mp` can say so.
    assert _disposable_name("_profile_mp") == "_profile_mp"
    assert _disposable_name("_profile_mp").startswith(DISPOSABLE_PREFIX)


async def test_the_benchmark_deletes_the_corpus_it_wrote_into(tmp_path, monkeypatch):
    """The disposable corpus is gone when the run ends, and the label's is not.

    The live corpus is the one a reader is using. The benchmark used to erase
    it outright under `--reset`.
    """
    from api.services import ingestion as ingestion_service
    from api.services.corpora import get_corpus_by_name
    from api.services.db import session_scope
    from api.services.repository import upsert_document
    from scripts.benchmark import ingest_profile

    live = "sync-reuse-live"
    live_id = await _corpus(live)
    async with session_scope() as db:
        await upsert_document(db, "Live", "live.md", "The live corpus.", {}, corpus_id=live_id)
        await db.commit()

    async def fake_sync_repo(self, path, corpus_id):
        return {
            "added": 1,
            "changed": 0,
            "unchanged": 0,
            "failed": 0,
            "chunk_count": 1,
            "claim_count": 1,
        }

    monkeypatch.setattr(ingestion_service.IngestionService, "sync_repo", fake_sync_repo)

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "one.md").write_text(CLAIM_BEARING)

    args = ingest_profile.argparse.Namespace(corpus=[f"{live}={docs}"], reset=True, keep=False)
    report = await ingest_profile._run(args)

    assert report["per_corpus"][live]["corpus"] == "_profile_sync_reuse_live"

    async with session_scope() as db:
        assert await get_corpus_by_name(db, live) is not None, "the live corpus was erased"
        assert (
            await get_corpus_by_name(db, "_profile_sync_reuse_live") is None
        ), "the disposable corpus was left behind"

    # And the live corpus still holds what it held.
    assert await _paths(live_id) == ["live.md"]
