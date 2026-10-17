"""Supersession that crosses a document boundary, and the near-miss hint on write.

Two products fail here and both are silent:

* A decision recorded in `adr-002.md` could not retire what `adr-001.md` said. Both
  sat CURRENT under related keys, and the archive reported a conflict about a
  decision that was formally replaced weeks earlier.
* A write that contradicted something already recorded reported nothing. The
  archive cannot tell a supersession from a contradiction — "roughly the same" is
  not a rule it can apply — so it names the near-misses and lets the caller decide.

Every migration is applied and the transaction rolls back, so this exercises the
schema the product actually ships with, including the `011` trigger that makes a
cross-document edge legal at all.
"""

import hashlib
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.models.memory import MemoryRequest
from api.services import ingestion as ingestion_service
from api.services import remember as remember_service
from api.services.memory import _active_claims
from api.services.memory_public import execute_in_session
from api.services.remember import related_active, remember

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
async def store(monkeypatch):
    """A real corpus on the real schema, in a rolled-back transaction.

    Same shape as the golden-path fixture: the service keeps its real transaction
    ownership and only the connection belongs to the test.
    """
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
                    from api.services import db as db_module

                    for module in (ingestion_service, remember_service, db_module):
                        if hasattr(module, "session_scope"):
                            monkeypatch.setattr(module, "session_scope", sessions)

                    async def resolve(name, corpus_id=None):
                        return corpus

                    monkeypatch.setattr(remember_service, "ensure_corpus", resolve)
                    yield SimpleNamespace(db=db, corpus=corpus, name=schema)
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


async def live(store):
    """Every claim the corpus currently holds as fact.

    On the fixture's own session, which already sits in a savepoint under the
    rolled-back outer transaction.
    """
    return await _active_claims(store.db, store.corpus, limit=None)


async def ingest(store, statement, path, key):
    """Archive one statement as its own document, bypassing `remember`'s key logic."""
    from api.services.remember import render_statement

    await ingestion_service.IngestionService(
        memory_enabled=True, require_embeddings=False
    ).ingest_file(content=render_statement(statement, key), path=path, corpus_id=store.corpus)


async def test_a_new_document_can_retire_a_claim_in_an_older_one(store):
    """The case the whole change exists for.

    Two files, two documents, one fact. Before `011` the database rejected the
    edge, so both claims stayed CURRENT and recall reported a conflict about a
    decision that had been formally replaced.
    """
    await ingest(store, "The primary datastore is PostgreSQL 15.", "adr-001.md", "adr.one")
    await ingest(store, "The primary datastore is PostgreSQL 16.", "adr-002.md", "adr.two")
    assert len({c["key"] for c in await live(store)}) == 2, "both start live"

    target = next(c for c in await live(store) if "PostgreSQL 15" in c["claim"])
    await remember(
        "The primary datastore is PostgreSQL 16, cluster-wide.",
        corpus=store.name,
        supersedes_claim_id=target["id"],
    )

    texts = [c["claim"] for c in await live(store)]
    assert not any("PostgreSQL 15" in text for text in texts)
    assert any("PostgreSQL 16, cluster-wide." in text for text in texts)


async def test_the_superseded_claim_is_readable_as_history(store):
    """Retiring a claim moves it to history. It must not disappear.

    Read through `pack`, which is the complete scoped state before relevance
    selection. A `current` query answers with the current claims and is right to
    withhold the history; this is about the history being *present at all*, and
    about the superseded claim still carrying its evidence.
    """
    await ingest(store, "The primary datastore is PostgreSQL 15.", "adr-001.md", "adr.one")
    target = next(c for c in await live(store) if "PostgreSQL 15" in c["claim"])
    await remember(
        "The primary datastore is PostgreSQL 16.",
        corpus=store.name,
        supersedes_claim_id=target["id"],
    )

    full = await execute_in_session(
        store.db, "pack", MemoryRequest(corpus=store.name, query="", budget=128000)
    )
    statuses = {c.claim: c.status for c in full.current_memories + full.historical_memories}
    assert statuses.get("The primary datastore is PostgreSQL 15.") == "SUPERSEDED"
    assert statuses.get("The primary datastore is PostgreSQL 16.") == "CURRENT"
    quoted = {e.text for e in full.evidence}
    assert "The primary datastore is PostgreSQL 15." in quoted


async def test_topic_key_retires_whatever_is_live_under_it(store):
    await ingest(store, "The datastore is PostgreSQL.", "adr-001.md", "architecture.db")
    await remember(
        "The datastore is CockroachDB.",
        corpus=store.name,
        key="architecture.db",
        topic_key="architecture.db",
    )
    assert [c["claim"] for c in await live(store)] == ["The datastore is CockroachDB."]


async def test_topic_key_refuses_when_two_claims_are_live(store):
    """Retiring the newest of two would leave one disagreeing and look resolved."""
    await ingest(store, "The datastore is PostgreSQL.", "adr-001.md", "architecture.db")
    await ingest(store, "The datastore is Vitess.", "adr-002.md", "architecture.db")
    assert len(await live(store)) == 2

    with pytest.raises(ValueError, match="across documents"):
        await remember(
            "The datastore is Spanner.",
            corpus=store.name,
            key="architecture.db",
            topic_key="architecture.db",
        )
    assert len(await live(store)) == 2, "a refused supersession must change nothing"


async def test_an_unknown_supersedes_id_is_refused_not_ignored(store):
    with pytest.raises(ValueError, match="not an active claim"):
        await remember("Something else entirely.", corpus=store.name, supersedes_claim_id="a" * 64)


async def test_superseding_an_already_superseded_claim_is_refused(store):
    """Superseding twice would make the chain ambiguous and is not a no-op."""
    await ingest(store, "The datastore is PostgreSQL.", "adr-001.md", "adr.one")
    target = next(c for c in await live(store) if "PostgreSQL" in c["claim"])
    await remember(
        "The datastore is CockroachDB.", corpus=store.name, supersedes_claim_id=target["id"]
    )
    with pytest.raises(ValueError, match="not an active claim"):
        await remember(
            "The datastore is Spanner.", corpus=store.name, supersedes_claim_id=target["id"]
        )


async def test_a_write_near_an_existing_claim_says_so(store):
    await ingest(store, "The primary datastore is PostgreSQL 15.", "adr-001.md", "adr.one")
    written = await remember("The primary datastore is PostgreSQL 16.", corpus=store.name)
    related = written.get("related_active_claims") or []
    assert related, "a near-duplicate write must be reported"
    assert related[0]["hint"].startswith("Possible supersession or conflict with claim")
    assert related[0]["claim_id"]


async def test_the_hint_omits_the_claim_the_caller_already_named(store):
    """Telling them to do the thing they just did is a hint about nothing."""
    await ingest(store, "The primary datastore is PostgreSQL 15.", "adr-001.md", "adr.one")
    target = next(c for c in await live(store) if "PostgreSQL 15" in c["claim"])
    written = await remember(
        "The primary datastore is PostgreSQL 16.",
        corpus=store.name,
        supersedes_claim_id=target["id"],
    )
    named = {c["claim_id"] for c in (written.get("related_active_claims") or [])}
    assert target["id"] not in named


async def test_an_unrelated_write_reports_no_near_misses(store):
    await ingest(store, "The primary datastore is PostgreSQL 15.", "adr-001.md", "adr.one")
    written = await remember("On-call rotation changes weekly on Mondays.", corpus=store.name)
    assert not written.get("related_active_claims")


async def test_the_hint_survives_a_read_it_cannot_complete(store):
    """The write it annotates has already succeeded; a hint must not fail it."""
    assert await related_active("Anything at all.", "no-such-corpus") == []
