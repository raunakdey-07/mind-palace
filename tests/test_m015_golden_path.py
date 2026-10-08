"""M015: the five-minute path, proven on a real database.

The product claim is that a developer can install, remember, recall, explain,
inspect history and verify memory without learning the internals first. These
tests defend that claim at the level it is made: through the public CLI, SDK, REST
and MCP, against real PostgreSQL, with no test-only shortcut in the product code.

Two properties are permanent rather than incidental:

* A statement becomes authoritative memory with exact-substring evidence. If this
  regresses to a search-index entry, a newcomer can "remember" a fact and still be
  unable to recall it -- the defect this milestone exists to fix.
* Submitting the same statement twice is not duplication. Identity derives from
  content, so a retry after a lost response converges instead of duplicating.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.models.memory import MemoryRequest, MemoryResponse
from api.services import ingestion, memory_public
from api.services import remember as remember_service
from api.services.remember import RememberError, remember
from api.services.memory_public import execute_in_session

ROOT = Path(__file__).resolve().parents[1]

#: Pinned so every surface resolves the same temporal state instead of sampling
#: four different clocks.
WHEN = datetime(2026, 1, 15, tzinfo=timezone.utc)


@pytest.fixture
async def store(monkeypatch):
    """A real corpus on the real schema, in a rolled-back transaction.

    Every migration is applied, because the product write path touches the live
    index as well as the archive and a test that skipped a column would not be
    testing the write path a developer actually uses. The transaction is rolled
    back, so nothing survives the test.

    `session_scope` is redirected to a savepoint on that transaction, the same way
    the existing ingestion fixture does it: the service keeps its real
    transaction ownership, and only the connection belongs to the test.
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
    schema = "m015_" + uuid4().hex
    corpus = hashlib.sha256(uuid4().bytes).hexdigest()
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))

                def migrate(sync_conn):
                    for path in sorted((ROOT / "migrations" / "versions").glob("*.py")):
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

                    # The service owns its transaction; a savepoint keeps that
                    # ownership rule intact while the connection stays the test's.
                    class SavepointSession:
                        def begin(self):
                            return db.begin_nested()

                        def __getattr__(self, name):
                            return getattr(db, name)

                    @asynccontextmanager
                    async def sessions():
                        yield SavepointSession()

                    for module in (ingestion, memory_public):
                        monkeypatch.setattr(module, "session_scope", sessions)

                    async def resolve(name: str, corpus_id: str | None = None) -> str:
                        return corpus

                    monkeypatch.setattr(remember_service, "ensure_corpus", resolve)
                    yield SimpleNamespace(db=db, corpus=corpus, name=schema)
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def surfaces(store, monkeypatch):
    """REST, SDK and MCP driven the way a developer drives them, over one store.

    The same construction the existing cross-surface contract uses: ASGI for REST,
    the SDK's own client, and the MCP server's tools, all inside the store's
    transaction via savepoints.
    """
    import httpx
    import mcp_server
    from typer.testing import CliRunner

    from api.main import app
    from cli.main import app as cli_app
    from mindpalace_sdk import MindPalace

    loop = asyncio.get_running_loop()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://m015"
    ) as rest:

        def post(url, **kwargs):
            # The SDK and CLI run in worker threads; ASGI and asyncpg stay here.
            return asyncio.run_coroutine_threadsafe(rest.post(url, **kwargs), loop).result(
                timeout=30
            )

        monkeypatch.setattr(httpx, "post", post)
        sdk = MindPalace(base_url="http://m015").memory

        async def sdk_call(operation: str, request):
            fields = request.model_dump(mode="json", exclude_unset=True)
            method = {"as-of": "as_of", "replay": "replay_snapshot"}.get(operation, operation)
            return await asyncio.to_thread(partial(getattr(sdk, method), **fields))

        async def cli_call(*args: str):
            """Drive the real CLI, over the same service, as a developer would."""
            return await asyncio.to_thread(CliRunner().invoke, cli_app, list(args))

        yield SimpleNamespace(
            rest=rest,
            sdk=sdk_call,
            cli=cli_call,
            base_url="http://m015",
            mcp=mcp_server.mcp,
        )


async def read_all(store) -> MemoryResponse:
    """Everything currently recorded in this corpus.

    Read with `current` and no selector rather than a question, so a test that
    writes about an unrelated subject still sees its own memory.
    """
    return await execute_in_session(store.db, "current", MemoryRequest(corpus=store.name))


# --------------------------------------------------------------------------
# 1. A statement becomes authoritative memory
# --------------------------------------------------------------------------


async def test_a_statement_becomes_memory_with_exact_evidence(store):
    """The core fix: `remember` writes authoritative memory, not a search entry.

    Before this milestone, ingesting a plain sentence returned success and produced
    no memory at all. Now it produces a CURRENT claim, and the evidence is an exact
    character span of the archived chunk.
    """

    result = await remember("Production uses PostgreSQL.")
    assert result["event"] == "NEW", result

    current = (await read_all(store)).current_memories
    assert [c.claim for c in current] == ["Production uses PostgreSQL."]
    claim = current[0]
    assert claim.status == "CURRENT"
    assert claim.evidence_ids, "a remembered claim must carry evidence"

    response = await read_all(store)
    evidence = [e for e in response.evidence if e.id in claim.evidence_ids]
    assert len(evidence) == 1
    row = evidence[0]
    # The offsets must actually span the quote in the archived chunk. This is the
    # invariant the database trigger enforces, checked again on the way out.
    assert row.end_offset - row.start_offset == len(row.text)
    assert row.text == "Production uses PostgreSQL."


async def test_recall_answers_the_question_a_newcomer_would_ask(store):
    """`recall` is the shortest path to value, and it abstains rather than guessing."""

    await remember("The production datastore changed from SQLite to PostgreSQL.")
    response = await read_all(store)

    recalled = await execute_in_session(
        store.db, "query", MemoryRequest(corpus=store.name, query="What datastore is production?")
    )
    assert [c.claim for c in recalled.current_memories] == [
        "The production datastore changed from SQLite to PostgreSQL."
    ]
    assert recalled.constraints == [], "a covered question must not report a constraint"

    stranger = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(
            corpus=store.name, query="What is the airspeed velocity of an unladen swallow?"
        ),
    )
    assert stranger.current_memories == []
    assert "NO_RELEVANT_MEMORY" in stranger.constraints
    assert response.corpus == store.name


async def test_a_write_succeeds_without_an_embedding_model(store, monkeypatch):
    """A first memory must not depend on a model download.

    The read path already degrades to lexical ranking when the model is absent. A
    write has to work in the same conditions, or "install and remember" is really
    "install, download a model, and remember".
    """
    from api.services.embedder import SEMANTIC_DEPENDENCY_ERRORS, Embedder

    def unavailable(self, texts):
        raise OSError("model weights are not available offline")

    monkeypatch.setattr(Embedder, "embed", unavailable)
    monkeypatch.setattr(
        Embedder, "dimension", property(lambda self: (_ for _ in ()).throw(OSError("no model")))
    )

    result = await remember("Backups run nightly at 02:00 UTC.")
    assert result["event"] == "NEW"
    assert result["chunk_count"] >= 1
    assert [c.claim for c in (await read_all(store)).current_memories] == [
        "Backups run nightly at 02:00 UTC."
    ]
    # The degradation contract is importable, so a caller can tell "no vectors"
    # apart from "the write failed".
    assert issubclass(OSError, SEMANTIC_DEPENDENCY_ERRORS)


# --------------------------------------------------------------------------
# 2. Idempotency: the same write is the same memory
# --------------------------------------------------------------------------


async def test_remembering_the_same_statement_twice_is_not_duplication(store):
    """Same statement twice resolves to one identity and one authoritative memory."""

    first = await remember("Backups run nightly at 02:00 UTC.")
    second = await remember("Backups run nightly at 02:00 UTC.")

    assert first["event"] == "NEW"
    assert second["event"] == "UNCHANGED"
    assert second["version_id"] == first["version_id"]
    assert second["document_id"] == first["document_id"]
    assert len((await read_all(store)).current_memories) == 1


@pytest.fixture
async def live_schema(monkeypatch):
    """A dedicated schema with its own connections, dropped when the test ends.

    Concurrency cannot be tested on a single connection, so this fixture is
    deliberately not rollback-scoped: each writer gets a real session, and the
    schema is dropped afterwards instead.
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
    schema = "m015live_" + uuid4().hex
    corpus = hashlib.sha256(uuid4().bytes).hexdigest()
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.commit()
        async with engine.connect() as conn:
            transaction = await conn.begin()
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))

            def migrate(sync_conn):
                for path in sorted((ROOT / "migrations" / "versions").glob("*.py")):
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
            await transaction.commit()

        # Every writer now opens its own real session. Search_path is per
        # connection, so a dedicated engine pins each one to the test schema.
        scoped = create_async_engine(
            url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
        )

        base = ingestion.IngestionService

        class SchemaScoped(base):
            """Real ingestion, with every session pinned to the test schema."""

            def __init__(self, **kwargs):
                super().__init__(**kwargs)

            async def ingest_file(self, content, path=None, corpus_id=None):
                # Entering the session owns the transaction, exactly as the real
                # `session_scope` + `db.begin()` pair does.
                async with AsyncSession(scoped, expire_on_commit=False) as session:
                    return await self._ingest_content(session, content, path, corpus_id)

        monkeypatch.setattr(remember_service, "IngestionService", SchemaScoped)
        yield SimpleNamespace(engine=scoped, corpus=corpus, schema=schema)
    finally:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.commit()
        await engine.dispose()


async def test_concurrent_identical_writes_converge_on_one_version(live_schema):
    """Simultaneous identical writes must not both insert a version.

    The corpus advisory lock is taken before the fingerprint comparison, so the
    check-then-write window is closed by PostgreSQL itself rather than by a queue
    or a distributed lock. Each writer below has its own connection, so these are
    genuinely concurrent transactions.
    """
    results = await asyncio.gather(
        *(
            remember("Rate limits are 100 requests per minute.", corpus_id=live_schema.corpus)
            for _ in range(4)
        )
    )
    events = [result["event"] for result in results]
    assert events.count("NEW") == 1, events
    assert events.count("UNCHANGED") == 3, events
    assert len({result["version_id"] for result in results}) == 1

    async with AsyncSession(live_schema.engine) as db:
        await db.execute(text(f'SET LOCAL search_path TO "{live_schema.schema}", public'))
        versions = (
            await db.execute(
                text("SELECT count(*) FROM memory_versions WHERE corpus_id = :c"),
                {"c": live_schema.corpus},
            )
        ).scalar_one()
        claims = (
            await db.execute(
                text("SELECT count(*) FROM memory_claims WHERE corpus_id = :c"),
                {"c": live_schema.corpus},
            )
        ).scalar_one()
    assert versions == 1, "no duplicate authoritative version may exist"
    assert claims == 1, "no duplicate authoritative claim may exist"


async def test_a_retry_after_a_lost_response_reaches_the_same_memory(store):
    """The developer-visible property of idempotency, stated as a retry.

    A client that sent a write, lost the response and retried must find the memory
    it already recorded -- not a duplicate, and not a supersession of itself.
    """

    sent = await remember("Deploys require two approvals.")
    lost_response = sent  # the write committed; the client never saw the reply
    retried = await remember("Deploys require two approvals.")

    assert retried["event"] == "UNCHANGED"
    assert retried["version_id"] == lost_response["version_id"]
    assert [c.claim for c in (await read_all(store)).current_memories] == [
        "Deploys require two approvals."
    ]


async def test_the_same_key_with_new_text_supersedes_instead_of_accumulating(store):
    """Memory records change. A rewritten fact supersedes; it does not pile up."""

    await remember("Production uses SQLite.", key="architecture.datastore")
    first = (await read_all(store)).current_memories[0]
    await remember("Production uses PostgreSQL.", key="architecture.datastore")

    history = await execute_in_session(
        store.db, "history", MemoryRequest(corpus=store.name, query="datastore")
    )
    supersessions = [c for c in history.changes if c.memory_changed]
    assert supersessions, "a rewritten key must produce a supersession"
    assert any(c.relationship == "SUPERSEDES" for c in supersessions)

    current = [c for c in history.current_memories if c.key == "architecture.datastore"]
    assert len(current) == 1, "exactly one current value: the old one is superseded"
    assert current[0].claim == "Production uses PostgreSQL."

    historical = [c for c in history.historical_memories if c.key == "architecture.datastore"]
    assert [c.claim for c in historical] == [
        "Production uses SQLite."
    ], "the previous value must still be readable, with its identity intact"
    assert first.id != current[0].id


async def test_time_travel_answers_with_what_was_true_then(store):
    """Memory is not only what is true now. It is what was true when."""

    await remember("Production uses SQLite.", key="architecture.datastore")
    # `as_of` is an inclusive observation cutoff, so the first version's own
    # observed_at is exactly the instant at which only the old value was recorded.
    moment = (
        await store.db.execute(
            text(
                "SELECT observed_at FROM memory_versions "
                "WHERE corpus_id = :c ORDER BY observed_at LIMIT 1"
            ),
            {"c": store.corpus},
        )
    ).scalar_one()
    await remember("Production uses PostgreSQL.", key="architecture.datastore")

    then = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="What is the datastore?", as_of=moment),
    )
    assert [c.claim for c in then.current_memories] == [
        "Production uses SQLite."
    ], "the answer before the supersession must be the value that was true then"
    assert then.state.as_of is not None, "the response must say which instant it reconstructed"


async def test_recall_still_answers_after_a_second_claim_shares_the_subject(store):
    """The one retrieval change, stated as the property it was made for.

    Two claims under one key share their subject. The acceptance gate used to
    compute overlap against terms reduced by the set common to every candidate,
    which deletes exactly the subject the question asks about -- so remembering a
    revised fact silently made the subject unanswerable. Ranking still uses the
    reduced set, because shared terms cannot discriminate between candidates.
    """
    await remember("Production uses SQLite.", key="architecture.datastore")
    await remember("Production uses PostgreSQL.", key="architecture.datastore")

    recalled = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="What is the datastore?"),
    )
    assert [c.claim for c in recalled.current_memories] == [
        "Production uses PostgreSQL."
    ], "the current value must still answer a question about its own subject"

    # Ranking is unchanged: only the current value is returned, not the superseded one.
    assert all(c.status == "CURRENT" for c in recalled.current_memories)
    assert recalled.constraints == []


async def test_a_multi_line_statement_becomes_memory(store):
    """A statement is not a document, but it is still a statement.

    The archive requires the evidence quote to be an exact substring of an archived
    chunk, and a YAML scalar cannot carry line breaks verbatim. If the frontmatter
    and the body disagree about the text, every multi-line statement is rejected --
    which silently made `remember --file` useless.
    """
    statement = "Production uses PostgreSQL.\nIt runs in three availability zones."

    result = await remember(statement)
    assert result["event"] == "NEW"
    assert result["key"], "the write must report the identity it recorded"

    everything = await read_all(store)
    assert len(everything.current_memories) == 1
    claim = everything.current_memories[0]
    evidence = [e for e in everything.evidence if e.id in claim.evidence_ids]
    assert len(evidence) == 1
    # The offsets must span exactly the stored quote, which must be the claim.
    assert evidence[0].text == claim.claim
    assert evidence[0].end_offset - evidence[0].start_offset == len(evidence[0].text)

    # The same text again is the same memory, whitespace notwithstanding.
    again = await remember(statement)
    assert again["event"] == "UNCHANGED"
    assert again["version_id"] == result["version_id"]


async def test_a_statement_that_opens_with_a_markdown_heading_is_remembered(store):
    """A leading `#` must not turn the statement into a chunk heading.

    The document body starts with its own heading. A statement opening with `#`
    would be chunked as a second heading, and the text the archive was told to
    quote would land in a different chunk than the claim.
    """
    result = await remember("# Release policy\nWe ship on Thursdays.")
    assert result["event"] == "NEW"
    assert (await read_all(store)).current_memories[0].claim == (
        "Release policy We ship on Thursdays."
    )


async def test_remembering_a_file_keeps_its_authored_claims(store, tmp_path):
    """A file with authored `claims:` keeps them, verbatim and under one identity."""
    source = tmp_path / "decision.md"
    source.write_text(
        "---\n"
        "title: Deployment architecture\n"
        "date: 2026-02-01\n"
        "claims:\n"
        "  - key: architecture.datastore\n"
        "    value: PostgreSQL\n"
        "    claim: The primary datastore is PostgreSQL.\n"
        "    evidence: The primary datastore is PostgreSQL.\n"
        "---\n"
        "\n"
        "# Deployment architecture\n"
        "\n"
        "The primary datastore is PostgreSQL. It runs in three zones.\n",
        encoding="utf-8",
    )

    result = await remember(file=str(source))
    assert result["event"] == "NEW"
    current = (await read_all(store)).current_memories
    assert [c.claim for c in current] == ["The primary datastore is PostgreSQL."]
    assert current[0].key == "architecture.datastore", "the author's key must survive"
    # An absolute path must not become part of the stored identity.
    assert not result["path"].startswith("/"), result["path"]


async def test_the_write_reports_the_identity_the_archive_holds(store, surfaces):
    """One place computes the key and the path, and every surface reports it.

    Recomputing identity per call site is how the REST response came to name a key
    the archive did not hold -- and a client retrying with that key would write a
    second memory instead of recognising the first.
    """
    statement = "The API version is v1."
    service = await remember(statement, key="api.version")

    async with store.db.begin_nested():
        rest = await surfaces.rest.post(
            "/api/memory/remember",
            json={"corpus": store.name, "statement": statement, "key": "api.version"},
        )
        padded = await surfaces.rest.post(
            "/api/memory/remember",
            json={"corpus": store.name, "statement": f"  {statement}  ", "key": "api.version"},
        )
    assert rest.status_code == 200, rest.text
    assert rest.json()["key"] == service["key"]
    assert rest.json()["path"] == service["path"]
    # Whitespace must not fork a memory: the padded write is the same statement.
    assert padded.json()["key"] == service["key"]
    assert padded.json()["version_id"] == service["version_id"]


async def test_rest_refuses_to_read_a_file(store, surfaces):
    """An unauthenticated endpoint must not publish the server's filesystem.

    `remember --file` reads a local file, which is a decision for a trusted
    operator. Accepting a path over HTTP would let any caller read a file relative
    to the server's working directory and recall its contents.
    """
    async with store.db.begin_nested():
        response = await surfaces.rest.post(
            "/api/memory/remember",
            json={"corpus": store.name, "statement": "x", "file": ".env"},
        )
    assert response.status_code == 422
    assert "file" in response.text


async def test_a_write_never_loads_the_embedding_model(store, monkeypatch):
    """`remember` must not touch the model at all, not merely survive its absence.

    Catching a failed embed would still pay for a first-run model download, and
    merely reaching for one that is cached would pay `import sentence_transformers`,
    measured at 10.8 s. A first memory must not depend on either.
    """
    from api.services.embedder import Embedder

    def refuse(self, texts):
        raise AssertionError("remember loaded an embedding model")

    monkeypatch.setattr(Embedder, "embed", refuse)
    monkeypatch.setattr(
        Embedder, "dimension", property(lambda self: (_ for _ in ()).throw(AssertionError))
    )

    result = await remember("Backups run nightly at 02:00 UTC.")
    assert result["event"] == "NEW"
    assert [c.claim for c in (await read_all(store)).current_memories] == [
        "Backups run nightly at 02:00 UTC."
    ]


async def test_a_read_warms_its_own_cache_and_the_next_read_is_served_from_it(store):
    """The encoder's work must not be thrown away.

    `remember` does not embed, so a corpus authored through it starts with a cold
    read cache. If a query also threw away the vectors it just computed, every read
    would re-encode the whole corpus: 3.2 s per recall at 200 statements. This is
    the fix -- the first read pays, the second does not.
    """
    from api.services import claim_embeddings
    from api.services.embedder import Embedder

    for index in range(6):
        await remember(f"Statement number {index} concerns topic {index % 3}.", corpus=store.name)

    # The store's own corpus id: `corpus_id_for_name` resolves through a
    # connection outside this test's schema, so it cannot see these claims.
    corpus_id = store.corpus
    embedder = Embedder()

    first = await execute_in_session(
        store.db, "query", MemoryRequest(corpus=store.name, query="topic 1")
    )
    wanted, _ = await claim_embeddings.pending(store.db, corpus_id)
    cached = await claim_embeddings.load_cached(
        store.db, corpus_id, wanted, embedder.model_name, embedder.dimension
    )
    assert first.current_memories, "the question must be answered"
    assert cached, "the read that encoded the corpus must have persisted its vectors"

    # And the cache is usable: a second identical read is served from it, unchanged.
    second = await execute_in_session(
        store.db, "query", MemoryRequest(corpus=store.name, query="topic 1")
    )
    assert [c.claim for c in second.current_memories] == [
        c.claim for c in first.current_memories
    ], "a warm cache must not change the answer"


# --------------------------------------------------------------------------
# 3. Cross-surface equivalence
# --------------------------------------------------------------------------


async def test_rest_sdk_mcp_and_the_service_agree_on_one_memory(store, surfaces):
    """Every door returns the same authoritative answer.

    Mind Palace is one product with four doors. If any surface can produce a
    different answer to the same question, the product claim cannot be falsified,
    so this is a permanent contract rather than a convenience.

    `valid_at` is pinned so the temporal state is identical across surfaces rather
    than four separately sampled clocks.
    """
    await remember("The datastore is PostgreSQL.", key="architecture.datastore")
    request = MemoryRequest(
        corpus=store.name,
        query="datastore",
        include_receipt=True,
        valid_at=WHEN,
    )
    fields = request.model_dump(mode="json", exclude_unset=True, exclude={"corpus"})

    direct = await execute_in_session(store.db, "query", MemoryRequest(corpus=store.name, **fields))
    assert direct.current_memories, "the question must retrieve, or nothing below is proven"

    rest = await surfaces.rest.post("/api/memory/query", json=request.model_dump(mode="json"))
    assert rest.status_code == 200, rest.text
    sdk_result = await surfaces.sdk("query", request)
    tool = await surfaces.mcp.call_tool(
        "memory_recall", {"request": request.model_dump(mode="json")}
    )
    assert not tool.is_error, tool

    expected = direct.model_dump()
    assert expected == MemoryResponse.model_validate(rest.json()).model_dump()
    assert expected == sdk_result.model_dump()
    assert expected == MemoryResponse.model_validate(tool.structured_content).model_dump()
    assert direct.receipt, "an explained answer must carry its receipt"


async def test_the_write_path_agrees_across_rest_and_the_service(store, surfaces):
    """`remember` has one notion of identity, whichever door it comes through."""

    async with store.db.begin_nested():
        first = await surfaces.rest.post(
            "/api/memory/remember",
            json={"corpus": store.name, "statement": "The datastore is PostgreSQL."},
        )
        again = await surfaces.rest.post(
            "/api/memory/remember",
            json={"corpus": store.name, "statement": "The datastore is PostgreSQL."},
        )
    assert first.status_code == 200, first.text
    assert first.json()["event"] == "NEW"
    assert first.json()["changed"] is True

    assert again.status_code == 200, again.text
    assert again.json()["changed"] is False, "the same statement must not be written twice"
    assert again.json()["version_id"] == first.json()["version_id"]

    # The service and REST agree on identity, not merely on success.
    direct = await remember("The datastore is PostgreSQL.")
    assert direct["version_id"] == first.json()["version_id"]


async def test_a_write_with_nothing_to_say_is_rejected_with_guidance(surfaces, store):
    """A bad request names what to do, rather than returning a schema error."""
    response = await surfaces.rest.post("/api/memory/remember", json={"corpus": store.name})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert "statement" in detail["message"]

    blank = await surfaces.rest.post(
        "/api/memory/remember", json={"corpus": store.name, "statement": "   "}
    )
    assert blank.status_code == 422


# --------------------------------------------------------------------------
# 4. Error UX
# --------------------------------------------------------------------------


def test_a_dead_database_says_what_to_do(monkeypatch):
    """A failure must answer what happened, why, and what to run.

    `OperationalError` is a truth about the driver, not an instruction to a
    developer. The guidance has to name the actual port and the actual command.
    """
    from sqlalchemy.exc import OperationalError

    from cli.errors import explain, render

    monkeypatch.setenv("DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")
    failure = OperationalError("SELECT 1", {}, Exception("Connection refused"))
    failure.code = "database_unavailable"
    failure.status_code = 503
    text_out = render(explain(failure), failure)

    assert "memory database is unavailable" in text_out.lower()
    assert "Why:" in text_out, "the reason must be stated"
    assert "localhost:5432" in text_out, "the address must be the real one"
    assert "docker compose up -d postgresql" in text_out, "the fix must be runnable"
    assert "OperationalError" not in text_out, "the driver class is not the answer"


def test_verbose_mode_keeps_the_technical_detail(monkeypatch):
    """Advanced users keep the detail; the default output does not bury them in it."""
    from cli.errors import explain, render

    monkeypatch.setenv("DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")
    failure = RuntimeError("could not translate host name")
    failure.code = "database_unavailable"
    failure.status_code = 503

    assert "could not translate host name" not in render(explain(failure), failure)
    assert "could not translate host name" in render(explain(failure), failure, verbose=True)


def test_error_text_survives_an_unreadable_database_url(monkeypatch):
    """Advice must never itself raise, whatever DATABASE_URL contains."""
    from api.errors import storage_unavailable
    from cli.errors import explain, render

    monkeypatch.setenv("DATABASE_URL", "not a url at all :::")
    assert "PostgreSQL" in storage_unavailable()

    class Hostile(Exception):
        code = "database_unavailable"
        status_code = 503

    text_out = render(explain(Hostile()), Hostile())
    assert "What to do:" in text_out


async def test_the_cli_recall_renders_an_abstention_as_an_abstention(store, surfaces):
    """A developer who asks something unrecorded must be told so, not shown nothing."""
    result = await surfaces.cli(
        "recall",
        "What is the airspeed of a swallow?",
        "--corpus",
        store.name,
        "--base-url",
        surfaces.base_url,
    )
    assert result.exit_code == 0, result.output
    assert "No memory matched that question." in result.output
    assert "NO_RELEVANT_MEMORY" in result.output


async def test_the_cli_recall_renders_answer_source_and_time(store, surfaces):
    """The default view is answer, source, time. Nothing internal leaks in."""
    written = await surfaces.cli(
        "remember",
        "Support responds within one business day.",
        "--corpus",
        store.name,
        "--base-url",
        surfaces.base_url,
    )
    assert written.exit_code == 0, written.output
    assert "Remembered." in written.output

    recalled = await surfaces.cli(
        "recall",
        "How fast does support respond?",
        "--corpus",
        store.name,
        "--base-url",
        surfaces.base_url,
    )
    assert recalled.exit_code == 0, recalled.output
    assert "Support responds within one business day." in recalled.output
    assert "source:" in recalled.output and "time:" in recalled.output
    # The default view must not dump the internals a developer did not ask for.
    for leak in ("embedding", "sqlstate", "score", "cosine", "vector"):
        assert leak not in recalled.output.lower(), leak


async def test_the_cli_explain_shows_evidence_and_history(store, surfaces):
    """`explain` is Level 2: the basis is one command away, and it is readable."""
    for version in ("v1", "v2"):
        result = await surfaces.cli(
            "remember",
            f"The API version is {version}.",
            "--key",
            "api.version",
            "--corpus",
            store.name,
            "--base-url",
            surfaces.base_url,
        )
        assert result.exit_code == 0, result.output

    explained = await surfaces.cli(
        "explain", "api version", "--corpus", store.name, "--base-url", surfaces.base_url
    )
    assert explained.exit_code == 0, explained.output
    for section in ("ANSWER", "TIME", "EVIDENCE", "HISTORY", "VERIFICATION"):
        assert section in explained.output, section
    assert "The API version is v2." in explained.output
    assert "characters" in explained.output, "evidence must carry real offsets"
    assert "superseded" in explained.output, "history must name the change"
    # The trust boundary is part of the output, not a footnote.
    assert "does not establish that the original source was factually correct" in (explained.output)


async def test_the_cli_history_shows_the_supersession_chain(store, surfaces):
    """`history` walks the real chain, oldest first, ending at what is true now."""
    await surfaces.cli(
        "remember",
        "The datastore is SQLite.",
        "--key",
        "datastore",
        "--corpus",
        store.name,
        "--base-url",
        surfaces.base_url,
    )
    await surfaces.cli(
        "remember",
        "The datastore is PostgreSQL.",
        "--key",
        "datastore",
        "--corpus",
        store.name,
        "--base-url",
        surfaces.base_url,
    )

    history = await surfaces.cli(
        "history", "datastore", "--corpus", store.name, "--base-url", surfaces.base_url
    )
    assert history.exit_code == 0, history.output
    assert "The datastore is SQLite." in history.output
    assert "superseded by" in history.output
    assert history.output.rstrip().endswith(
        "current now: The datastore is PostgreSQL."
    ), history.output


# --------------------------------------------------------------------------
# 5. Offline verification and the trust boundary
# --------------------------------------------------------------------------


#: Verify a receipt using only the shipped dependency-free verifier, with no
#: database, no model and no network -- the property the CLI must not weaken.
VERIFY_SNIPPET = """
import sys
from memory_receipt import verify_response_receipt, verify_trust
import json

payload = json.load(open(sys.argv[1]))
result = verify_response_receipt(payload["receipt"], payload["response"])
if not result["verified"]:
    print("REJECTED")
    raise SystemExit(1)
trust = verify_trust({}, "", payload["receipt"]["memory_pack_digest"])
print("VERIFIED")
print("authenticity:", "established" if trust["authenticated"] else "not established")
"""


def _verify_offline(receipt: Path, *extra: str) -> subprocess.CompletedProcess:
    """Verify with no database at all, which is what verification must allow."""
    return subprocess.run(
        [sys.executable, "-m", "cli.main", "verify", str(receipt), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            # No DATABASE_URL, and a deliberately unreachable one.
            "DATABASE_URL": "postgresql://nobody@127.0.0.1:1/nothing",
            "PYTHONPATH": str(ROOT),
        },
        timeout=120,
    )


async def test_verify_reports_each_property_and_stops_short_of_truth(store, surfaces, tmp_path):
    """`VERIFIED` must never read as "this fact is correct"."""
    from memory_receipt import verify_response_receipt

    await remember("The API version is v1.", key="api.version")
    result = await read_all(store)

    explained = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="api version", include_receipt=True),
    )
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(
            {"response": explained.model_dump(mode="json"), "receipt": explained.receipt},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    verified = _verify_offline(path)
    assert verified.returncode == 0, verified.stderr
    output = verified.stdout
    assert "VERIFIED" in output
    for property_name in ("Integrity:", "Provenance:", "Temporal state:", "Supersession:"):
        assert property_name in output, property_name
    assert "not established" in output, "authenticity must stay explicitly unestablished"
    assert "does not mean the original claim was factually true" in output

    # The offline verifier agrees with the library, so the CLI is not a second
    # implementation of the same check.
    library = verify_response_receipt(explained.receipt, explained.model_dump(mode="json"))
    assert library["verified"] is True
    assert result.corpus == store.name


async def test_a_tampered_receipt_is_rejected(store, surfaces, tmp_path):
    """Tampering must be caught, named, and say how to recover."""

    await remember("The API version is v1.", key="api.version")
    explained = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="api version", include_receipt=True),
    )
    honest = {"response": explained.model_dump(mode="json"), "receipt": explained.receipt}
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(honest, sort_keys=True), encoding="utf-8")
    assert _verify_offline(path).returncode == 0, "the untampered receipt must verify"

    # The digest is rewritten to claim an artifact that does not exist.
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["receipt"]["memory_pack_digest"] = "0" * 64
    path.write_text(json.dumps(tampered), encoding="utf-8")
    rejected = _verify_offline(path)
    assert rejected.returncode == 1, rejected.stdout
    assert "REJECTED" in rejected.stdout
    assert "digest" in rejected.stdout
    assert "mindpalace receipt" in rejected.stdout, "a rejection must say what to do"

    # The answer itself is rewritten, with the digest left alone. The digest covers
    # the content, so this is caught too -- which is the whole point of the digest.
    forged = json.loads(path.read_text(encoding="utf-8"))
    forged["receipt"]["memory_pack_digest"] = honest["receipt"]["memory_pack_digest"]
    forged["response"]["current_memories"][0]["claim"] = "The API version is v99."
    path.write_text(json.dumps(forged), encoding="utf-8")
    content = _verify_offline(path)
    assert content.returncode == 1
    assert "REJECTED" in content.stdout

    # A forged trust anchor is rejected on its own terms, with the reason that
    # matters: anyone can recompute a digest, so only a pinned one says who wrote it.
    anchored = _verify_offline(path, "--trusted-digest", "1" * 64)
    assert anchored.returncode == 1
    assert "trust anchor" in anchored.stdout


def test_verify_refuses_a_file_that_is_not_a_receipt(tmp_path):
    """A wrong input says what to run instead, rather than failing obscurely."""
    path = tmp_path / "notes.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    rejected = _verify_offline(path)
    assert rejected.returncode == 2
    assert "mindpalace receipt" in rejected.stdout + rejected.stderr


async def test_a_self_contained_receipt_verifies_with_only_the_shipped_package(
    store, surfaces, tmp_path
):
    """The portability claim, tested as a claim.

    Someone handed a receipt file must be able to check it with `pip install
    mindpalace-os` and nothing else: no CLI extra, no server, no database, no
    embedding model, no network. `mindpalace-proof` is the tool for that, and it
    must read the file `mindpalace receipt` writes.
    """
    from memory_proof_cli import main as proof_main

    await remember("The API version is v1.", key="api.version")
    explained = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="api version", include_receipt=True),
    )
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(
            {"response": explained.model_dump(mode="json"), "receipt": explained.receipt},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    def run(*args: str) -> tuple[int, str]:
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = proof_main(["verify", str(path), *args])
        return code, buffer.getvalue()

    code, output = run()
    assert code == 0, output
    assert "VERIFIED" in output
    for property_name in ("Integrity:", "Provenance:", "Temporal state:", "Trust:"):
        assert property_name in output, property_name
    assert "NOT ESTABLISHED" in output
    assert "does not establish that the original source was factually correct" in output

    # Tampering is caught by the same dependency-free tool.
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["response"]["current_memories"][0]["claim"] = "The API version is v99."
    path.write_text(json.dumps(tampered), encoding="utf-8")
    code, output = run()
    assert code == 1, output
    assert "REJECTED" in output
    assert "digest" in output

    # A pack-based artifact still needs its pack, and the refusal says so.
    receipt_only = tmp_path / "receipt-only.json"
    receipt_only.write_text(json.dumps(dict(explained.receipt["receipts"][0])), encoding="utf-8")
    import io
    from contextlib import redirect_stderr

    errors = io.StringIO()
    with redirect_stderr(errors):
        code = proof_main(["verify", str(receipt_only)])
    assert code == 2, errors.getvalue()
    assert "--pack" in errors.getvalue(), "the refusal must name the option it needs"


async def test_verify_needs_no_model_or_network(store, surfaces, tmp_path):
    """No embedding model, no database, no server: verification is portable."""

    await remember("The API version is v1.", key="api.version")
    explained = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="api version", include_receipt=True),
    )
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(
            {"response": explained.model_dump(mode="json"), "receipt": explained.receipt},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    offline = subprocess.run(
        [sys.executable, "-c", VERIFY_SNIPPET, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        # Offline and network-free: verification must need neither.
        env={
            "PATH": "/usr/bin:/bin",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONPATH": str(ROOT),
        },
        timeout=120,
    )
    assert offline.returncode == 0, offline.stderr
    assert "VERIFIED" in offline.stdout


# --------------------------------------------------------------------------
# 6. Defaults
# --------------------------------------------------------------------------


async def test_no_public_option_is_required_to_remember_and_recall(store):
    """Good defaults produce a valid, conservative result.

    No embedding model, dimension, metric, threshold, candidate K, schema choice
    or projection mode. A statement in, an answer out.
    """

    result = await remember("The API version is v1.")
    assert result["success"] is True
    assert result["chunk_count"] >= 1

    recalled = await execute_in_session(
        store.db, "query", MemoryRequest(corpus=store.name, query="What is the API version?")
    )
    assert [c.claim for c in recalled.current_memories] == ["The API version is v1."]
    assert recalled.constraints == []


async def test_an_empty_statement_is_refused_with_guidance():
    """A bad default fails loudly and says how to fix it."""

    with pytest.raises(RememberError) as caught:
        await remember("   ")
    assert "mindpalace remember" in str(caught.value), "the message must show a real command"

    with pytest.raises(RememberError) as too_long:
        await remember("x" * 5000)
    assert "--file" in str(too_long.value), "it must offer the supported alternative"


# --------------------------------------------------------------------------
# 7. The receipt contract is unchanged
# --------------------------------------------------------------------------


async def test_receipts_from_before_this_milestone_still_verify(store):
    """Receipt schema v1 and Memory Pack v1 are frozen; this adds reach, not design.

    A receipt produced by the existing library must verify exactly as before, and
    the new response-level bundle must not alter the pack digest the receipt covers.
    """
    from memory_pack import MemoryPack
    from memory_receipt import RECEIPT_SCHEMA_VERSION, verify_response_receipt

    assert RECEIPT_SCHEMA_VERSION == 1

    await remember("The API version is v1.", key="api.version")
    explained = await execute_in_session(
        store.db,
        "query",
        MemoryRequest(corpus=store.name, query="api version", include_receipt=True),
    )
    payload = explained.model_dump(mode="json")

    # The v1 rule, unchanged: the authoritative digest covers the response with its
    # receipt removed, so the receipt can never be folded into what it attests to.
    # Verification strips it on the artifact side for exactly this reason.
    without_receipt = {k: v for k, v in payload.items() if k != "receipt"}
    assert MemoryPack.from_dict(without_receipt).digest() == explained.receipt["memory_pack_digest"]
    assert verify_response_receipt(explained.receipt, payload)["verified"] is True

    # An existing v0.8.0-shaped receipt verifies with the same entry points.
    from memory_receipt import build_receipt

    pack = MemoryPack.from_dict(payload)
    legacy = build_receipt(pack, "api.version", query="api version")
    assert legacy["receipt_version"] == 1
    from memory_receipt import verify_receipt

    assert verify_receipt(legacy, pack)["verified"] is True


async def test_the_wheel_ships_what_verification_needs():
    """A clean install must be able to verify, so the verifier must ship."""
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    modules = pyproject["tool"]["setuptools"]["py-modules"]
    for required in ("memory_receipt", "memory_proof", "memory_pack", "mindpalace_sdk"):
        assert required in modules, f"{required} must ship in the wheel"


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL for the live CLI subprocess"
)
def test_the_cli_works_from_a_clean_environment(tmp_path):
    """The documented command sequence, run exactly as written.

    No test-only flags, no internal imports, no direct database manipulation: the
    five commands a newcomer reads about, in the order they appear in the README.

    The corpus is unique per run, because this writes to a real database outside the
    rolled-back fixtures. A fixed name would make a second run in the same
    environment start from the first run's memory, and the supersession assertions
    would be asserting on leftovers rather than on this run.
    """
    url = os.environ["DATABASE_URL"]
    corpus = f"m015-cli-{uuid4().hex[:12]}"

    def cli(*args: str, expect: int = 0) -> str:
        completed = subprocess.run(
            [sys.executable, "-m", "cli.main", *args, "--corpus", corpus],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "DATABASE_URL": url,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONPATH": str(ROOT),
            },
            timeout=180,
        )
        assert completed.returncode == expect, completed.stderr
        return completed.stdout

    init = subprocess.run(
        [sys.executable, "-m", "cli.main", "init"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "DATABASE_URL": url,
            "HF_HUB_OFFLINE": "1",
            "PYTHONPATH": str(ROOT),
        },
        timeout=180,
    )
    assert init.returncode == 0, init.stderr
    assert "Mind Palace is ready." in init.stdout
    assert "mindpalace remember" in init.stdout, "init must name the next command"

    assert "Remembered." in cli("remember", "The datastore is PostgreSQL.", "--key", "datastore")
    assert "Already remembered" in cli(
        "remember", "The datastore is PostgreSQL.", "--key", "datastore"
    )
    recalled = cli("recall", "What is the datastore?")
    assert "The datastore is PostgreSQL." in recalled

    explained = cli("explain", "What is the datastore?")
    assert "EVIDENCE" in explained and "VERIFICATION" in explained

    receipt_path = tmp_path / "receipt.json"
    cli("receipt", "What is the datastore?", "-o", str(receipt_path))
    assert receipt_path.exists()
    verified = _verify_offline(receipt_path)
    assert verified.returncode == 0, verified.stderr
    assert "VERIFIED" in verified.stdout

    # A real supersession, visible through the documented history command.
    cli("remember", "The datastore is MySQL.", "--key", "datastore")
    history = cli("history", "datastore")
    assert "The datastore is MySQL." in history
    assert "superseded by" in history
    assert history.rstrip().endswith("current now: The datastore is MySQL."), history
