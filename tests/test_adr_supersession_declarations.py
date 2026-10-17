"""A directory of decision records that supersede each other without anybody copying an id.

The ergonomic problem: retiring a claim in another file needs that claim's 64-character
hex id, and the only place the id exists is the output of a `recall` the author has
to run first. For an ADR numbered `0001`, `0002`, `0003` that is three round trips
to express something the documents already say about themselves.

So a document may declare it: `Supersedes: 0001-use-postgres15.md` in frontmatter, or
the same line in the body. This is the 3-file case -- `0001` replaced by `0002`, `0003`
unrelated -- with no flag passed to `ingest-repo`.

Every migration is applied and the transaction rolls back, so this runs against the
schema the product ships, including the `011` trigger that makes a cross-document edge
legal at all.
"""

import hashlib
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.services import ingestion as ingestion_service
from api.services import remember as remember_service
from api.services.memory import (
    _active_claims,
    _load,
    _query_result,
    _resolve_claim_id,
    memory_document_id,
)
from api.services.markdown_claims import supersession_references

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ADR_0001 = """\
# ADR 0001: Use PostgreSQL 15 for the primary datastore

## Decision

- The primary datastore is PostgreSQL 15 with three replicas.
"""

ADR_0002 = """\
---
title: "ADR 0002: Use PostgreSQL 16 for the primary datastore"
supersedes: 0001-use-postgres15.md
---

# ADR 0002: Use PostgreSQL 16 for the primary datastore

## Decision

- The primary datastore is PostgreSQL 16 with three replicas.
"""

ADR_0003 = """\
# ADR 0003: Keep the nightly batch window at 02:00 UTC

## Decision

- The nightly batch job runs at 02:00 UTC.
"""

ADR_DIRECTORY = (
    ("0001-use-postgres15.md", ADR_0001),
    ("0002-use-postgres16.md", ADR_0002),
    ("0003-batch-window.md", ADR_0003),
)


@pytest.fixture
async def store(monkeypatch):
    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("set MEMORY_TEST_DATABASE_URL or DATABASE_URL for PostgreSQL tests")
    url = (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_async_engine(url, poolclass=NullPool)
    schema = "adrx_" + uuid4().hex
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
                db = AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                )

                @asynccontextmanager
                async def sessions():
                    yield db

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


def service():
    return ingestion_service.IngestionService(memory_enabled=True, require_embeddings=False)


async def ingest_one(store, body, path, **kwargs):
    return await service().ingest_file(content=body, path=path, corpus_id=store.corpus, **kwargs)


async def ingest_repo(store):
    """Three files, in order, with no flag passed. This is what `ingest-repo` does."""
    for path, body in ADR_DIRECTORY:
        await ingest_one(store, body, path)


async def live_rows(store):
    """Every live claim, then release the read.

    The fixture's session is shared with the service, which opens its own
    transaction per write. A read left open on that session is a transaction the
    next write refuses to start inside.
    """
    rows = await _active_claims(store.db, store.corpus, limit=None)
    await store.db.rollback()
    return rows


async def live_texts(store):
    return [c["claim"] for c in await live_rows(store)]


# --- the deliverable --------------------------------------------------------


async def test_0002_supersedes_0001_with_no_flag_passed(store):
    """`Supersedes:` in frontmatter links the two documents on ingest.

    Before this, ingesting three ADRs left 0001 and 0002 both CURRENT and recall
    reported a conflict about a decision that was formally replaced.
    """
    await ingest_repo(store)
    texts = await live_texts(store)
    assert "The primary datastore is PostgreSQL 16 with three replicas." in texts
    assert "The primary datastore is PostgreSQL 15 with three replicas." not in texts
    # 0003 is unrelated and must survive untouched.
    assert "The nightly batch job runs at 02:00 UTC." in texts


async def test_history_shows_0001_to_0002(store):
    """The edge, not merely the absence of a conflict. Both claims stay readable."""
    await ingest_repo(store)
    rows = (
        await store.db.execute(
            text("""
                SELECT prior.claim AS from_text, current.claim AS to_text,
                       current.supersession_basis
                FROM memory_claims current
                JOIN memory_claims prior ON prior.id = current.supersedes_id
                WHERE current.corpus_id = :c
                ORDER BY current.claim
                """),
            {"c": store.corpus},
        )
    ).all()
    assert len(rows) == 1, [tuple(r) for r in rows]
    prior_text, current_text, basis = rows[0]
    assert "PostgreSQL 15" in prior_text
    assert "PostgreSQL 16" in current_text
    assert basis == "explicit_claim_id", basis


async def test_the_unrelated_adr_gets_no_edge(store):
    """A supersession must not leak onto a document that declared nothing."""
    await ingest_repo(store)
    rows = (
        await store.db.execute(
            text(
                "SELECT count(*) FROM memory_claims "
                "WHERE corpus_id = :c AND supersedes_id IS NOT NULL"
            ),
            {"c": store.corpus},
        )
    ).scalar_one()
    assert rows == 1, "exactly one supersession in the directory"


async def test_recall_answers_from_0002_only(store):
    """The product-level half of the deliverable: 0002 is the current truth."""
    from api.models.memory import MemoryRequest
    from api.services.embedder import Embedder
    from api.services.memory_public import State, project
    from api.services.memory_query import interpret, select

    await ingest_repo(store)
    request = MemoryRequest(corpus=store.name, query="What is the primary datastore?")
    versions = await _load(store.db, store.corpus)
    # The projection is unfiltered, exactly as the CLI builds it; selection is what
    # filters, and both halves of this test are about selection.
    full = project(
        versions,
        request.model_copy(update={"query": "", "path": None}),
        "pack",
        State(as_of=None, valid_at=None),
        None,
    )
    answer = select(full, request, interpret(request), Embedder())
    live = [c.claim for c in answer.current_memories]
    assert "The primary datastore is PostgreSQL 16 with three replicas." in live
    assert "The primary datastore is PostgreSQL 15 with three replicas." not in live


async def test_history_shows_0001_as_replaced_by_0002(store):
    """The two ADRs carry different positional keys, so history has to follow the edge.

    `0001` is `md:adr-0001-...:0` and `0002` is `md:adr-0002-...:0`. Nothing keys them
    together but the supersession edge, so a history that matched on each claim's own
    text reported that `adr-001` was never replaced -- the opposite of what happened.

    Exercised through `_query_result`, which is what `mindpalace history` reads. The
    query is the bare subject, not a sentence: that operation requires every query
    token to appear in the claim, so "What is the primary datastore?" matches nothing
    at all, because "what" is in no claim. `recall` is unaffected -- it ranks by
    embedding, not by token. Two surfaces, two behaviours, both pre-existing; this
    test pins the one it is about rather than papering over the other.
    """
    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)

    result = _query_result(versions, "primary datastore", None)
    historical = [c["claim"] for c in result["historical_memories"]]
    current = [c["claim"] for c in result["current_memories"]]
    assert (
        "The primary datastore is PostgreSQL 15 with three replicas." in historical
    ), f"history did not surface the replaced claim; got {historical}"
    assert "The primary datastore is PostgreSQL 16 with three replicas." in current
    assert "The primary datastore is PostgreSQL 15 with three replicas." not in current


async def test_a_question_and_a_subject_phrase_return_the_same_timeline(store):
    """What an MCP agent sends has to reach what a person types.

    `mindpalace history` matched on raw query tokens, so "what", "is" and "the"
    had to appear in a claim and a natural-language question matched nothing at
    all. The two forms below now return byte-identical timelines.
    """
    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)

    def timeline(query):
        result = _query_result(versions, query, None)
        return (
            [c["claim"] for c in result["historical_memories"]],
            [c["claim"] for c in result["current_memories"]],
        )

    phrase = timeline("primary datastore")
    question = timeline("What is the primary datastore?")
    assert phrase == question
    assert phrase[0] == ["The primary datastore is PostgreSQL 15 with three replicas."]
    assert phrase[1] == ["The primary datastore is PostgreSQL 16 with three replicas."]


@pytest.mark.parametrize(
    "question",
    [
        "What is the primary datastore?",
        "which datastore is primary",
        "How did the datastore decision change?",
        "why is the primary datastore what it is",
        "tell me about the primary datastore",
    ],
)
async def test_any_natural_language_question_about_a_subject_resolves(store, question):
    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)
    result = _query_result(versions, question, None)
    historical = [c["claim"] for c in result["historical_memories"]]
    current = [c["claim"] for c in result["current_memories"]]
    assert "The primary datastore is PostgreSQL 15 with three replicas." in historical, historical
    assert "The primary datastore is PostgreSQL 16 with three replicas." in current, current


async def test_a_question_about_the_other_adr_does_not_pull_in_the_first(store):
    """The relaxed pass must name one subject, not the whole corpus."""
    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)
    result = _query_result(versions, "What is the nightly batch window?", None)
    everything = [c["claim"] for c in result["current_memories"] + result["historical_memories"]]
    assert everything == ["The nightly batch job runs at 02:00 UTC."]


async def test_a_query_matching_nothing_still_matches_nothing(store):
    """The fallback is a fallback. Nonsense must not return the nearest subject."""
    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)
    result = _query_result(versions, "what is the quantum flux capacitor rating", None)
    assert result["current_memories"] == []
    assert result["historical_memories"] == []


async def test_an_empty_query_still_returns_everything(store):
    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)
    result = _query_result(versions, "", None)
    assert len(result["current_memories"]) + len(result["historical_memories"]) >= 3


async def test_recall_is_unaffected_by_the_history_fallback(store):
    """`recall` ranks by embedding and must not inherit the lexical fallback.

    The query path projects with an empty query, so `matches` is true for
    everything and the fallback never runs. Asserted, because a relaxed `matches`
    that leaked into `recall` would quietly widen every answer.
    """
    from api.models.memory import MemoryRequest
    from api.services.embedder import Embedder
    from api.services.memory_public import State, project
    from api.services.memory_query import interpret, select

    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)
    request = MemoryRequest(corpus=store.name, query="What is the primary datastore?")
    full = project(
        versions,
        request.model_copy(update={"query": "", "path": None}),
        "pack",
        State(as_of=None, valid_at=None),
        None,
    )
    assert len(full.current_memories) == 2, "both ADRs are visible to the query path"
    answer = select(full, request, interpret(request), Embedder())
    live = [c.claim for c in answer.current_memories]
    assert "The primary datastore is PostgreSQL 16 with three replicas." in live
    assert "The nightly batch job runs at 02:00 UTC." not in live


async def test_a_replaced_claim_is_history_even_though_its_own_text_does_not_match(store):
    """The edge, not the wording, is what makes a claim history for a given query.

    "PostgreSQL 16" matches on its own; "PostgreSQL 15" carries none of those tokens.
    Requiring the older claim's own text to match hid the replacement from `history`.
    """
    await ingest_repo(store)
    versions = await _load(store.db, store.corpus)

    narrow = _query_result(versions, "PostgreSQL 16", None)
    assert any(
        "PostgreSQL 15" in c["claim"] for c in narrow["historical_memories"]
    ), "the newer claim's text matches, so the claim it replaced belongs with it"


# --- how the reference was read --------------------------------------------


def test_a_frontmatter_reference_is_read_as_written():
    backward, forward = supersession_references({"supersedes": "0001-use-postgres15.md"}, "")
    assert backward == ["0001-use-postgres15.md"]
    assert forward == []


def test_a_body_line_is_read_too():
    """Frontmatter or body: plenty of ADRs are plain Markdown."""
    body = (
        "# ADR 9\n\nSupersedes: [0004](adr/0004-thing.md)\n\n## Decision\n\n- We ship on Friday.\n"
    )
    backward, _forward = supersession_references({}, body)
    assert backward == ["adr/0004-thing.md"], "the link target, not the link text"


def test_several_references_are_all_read():
    backward, _forward = supersession_references({"supersedes": ["0001-a.md", "0001-b.md"]}, "")
    assert backward == ["0001-a.md", "0001-b.md"]


def test_placeholder_values_are_not_references():
    """`Superseded-by: none` is on nearly every ADR template."""
    _backward, forward = supersession_references({"superseded_by": "none"}, "")
    assert forward == []


def test_a_reference_is_not_inferred_from_a_bare_number():
    """`Supersedes: 0002` means the literal string `0002`, never a guess at a filename.

    Inferring that it names `adr-0002-something.md` would retire whichever decision
    sorted first, and the archive would report that as history forever.
    """
    backward, _forward = supersession_references({"supersedes": "0002"}, "")
    assert backward == ["0002"]


def test_an_unrelated_frontmatter_key_is_not_a_declaration():
    """`title:` canonicalises to nothing. Reading it as a reference broke every write."""
    backward, forward = supersession_references({"title": "ADR 0002", "status": "Accepted"}, "")
    assert backward == [] and forward == []


# --- the refusals -----------------------------------------------------------


async def test_a_reference_to_a_document_never_ingested_is_refused(store):
    """Supersession cannot retire a claim this archive has never seen."""
    with pytest.raises(Exception, match="matches no document"):
        await ingest_one(store, ADR_0002, "0002-use-postgres16.md")


async def test_an_explicit_flag_outranks_the_declaration(store):
    """A caller who names a claim is not overruled by a line in the file."""
    await ingest_one(store, ADR_0001, "0001-use-postgres15.md")
    target = next(c for c in await live_rows(store) if "PostgreSQL 15" in c["claim"])
    await ingest_one(
        store,
        ADR_0003,
        "0003-batch-window.md",
        supersedes_claim_id=target["id"],
    )
    texts = await live_texts(store)
    assert "The primary datastore is PostgreSQL 15 with three replicas." not in texts
    assert "The nightly batch job runs at 02:00 UTC." in texts


# --- ergonomics: `--supersedes` accepts a phrase ----------------------------


async def test_a_phrase_retires_the_claim_it_describes(store):
    """The caller has the phrase in front of them and the id on another screen."""
    await ingest_one(store, ADR_0001, "0001.md")
    live = await live_rows(store)
    identity = memory_document_id(store.corpus, "0002.md")
    resolved = await _resolve_claim_id(store.db, store.corpus, identity, "primary datastore")
    assert resolved == live[0]["id"]


async def test_a_phrase_matching_several_claims_is_refused(store):
    """Two claims, one phrase: the caller is given the ids, not a coin flip."""
    await ingest_one(
        store,
        "# Notes\n\n## Datastore\n\n"
        "- The primary datastore is PostgreSQL 15 with three replicas.\n"
        "- The primary datastore also keeps a warm read replica.\n",
        "0001.md",
    )
    identity = memory_document_id(store.corpus, "0002.md")
    with pytest.raises(Exception, match="matches 2 active claims"):
        await _resolve_claim_id(store.db, store.corpus, identity, "primary datastore")


async def test_a_phrase_matching_nothing_is_refused(store):
    await ingest_one(store, ADR_0001, "0001.md")
    identity = memory_document_id(store.corpus, "0002.md")
    with pytest.raises(Exception, match="matches no active claim"):
        await _resolve_claim_id(store.db, store.corpus, identity, "quantum flux capacitor")


async def test_an_id_prefix_still_resolves(store):
    """The documented form keeps working. A phrase is an addition, not a replacement."""
    await ingest_one(store, ADR_0001, "0001.md")
    live = await live_rows(store)
    identity = memory_document_id(store.corpus, "0002.md")
    resolved = await _resolve_claim_id(store.db, store.corpus, identity, live[0]["id"][:12])
    assert resolved == live[0]["id"]


async def test_a_full_id_is_taken_as_written_without_a_lookup(store):
    """64 hex characters is an id, whatever the archive currently holds."""
    identity = memory_document_id(store.corpus, "0002.md")
    resolved = await _resolve_claim_id(store.db, store.corpus, identity, "a" * 64)
    assert resolved == "a" * 64
