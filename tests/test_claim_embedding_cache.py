"""The claim representation cache is L2, and that has to be provable.

Three properties matter and none may be assumed:

    neutral    a cache hit returns exactly what a fresh embed would return
    honest     a row whose text, model or dimension no longer matches is ignored
    disposable dropping the table changes latency and nothing else

The fixture is the real ingestion schema with the real migrations applied, so a
claim can only exist by going through the same path production uses.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from api.models.memory import MemoryRequest
from api.services import claim_embeddings, memory_public
from api.services.memory_public import execute_in_session
from api.services.memory_query import interpret
from api.services.memory_relevance import RelevancePolicy, relevance

try:
    from tests import test_memory_ingestion as ing
except ModuleNotFoundError as exc:  # pragma: no cover - import shim
    if exc.name not in {"tests", "tests.test_memory_ingestion"}:
        raise
    import test_memory_ingestion as ing

memory_ingestion_db = ing.memory_ingestion_db

QUESTION = "What is the current database?"
# Pinned so two calls in the same test are byte-comparable.
VALID_AT = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
DATABASE = "The primary database is PostgreSQL."
REASON = "PostgreSQL was chosen for JSONB and advisory locks."


def source(*sentences: str, key: str = "architecture.database") -> str:
    claims = "".join(
        f"  - key: {key}\n"
        f"    value: {json.dumps(s)}\n"
        f"    claim: {json.dumps(s)}\n"
        f"    evidence: {json.dumps(s)}\n"
        for s in sentences
    )
    return (
        f'---\ntitle: "Storage"\ndocument_type: "design"\nclaims:\n{claims}---\n\n# Storage\n\n'
        + "\n".join(sentences)
        + "\n"
    )


async def corpus_name(store) -> str:
    """The public contract keys on the corpus name, the fixture inserts the id."""
    sessions, corpus = store
    async with sessions() as db:
        result = await db.execute(text("SELECT name FROM corpora WHERE id = :c"), {"c": corpus})
        return result.scalar()


async def ask(store, question=QUESTION):
    sessions, corpus = store
    name = await corpus_name(store)
    async with sessions() as db:
        return await execute_in_session(
            db,
            "query",
            MemoryRequest(corpus=name, query=question, budget=128000, valid_at=VALID_AT),
        )


async def cache_count(store) -> int:
    sessions, corpus = store
    async with sessions() as db:
        result = await db.execute(
            text("SELECT count(*) FROM memory_claim_embeddings WHERE corpus_id = :c"), {"c": corpus}
        )
        return int(result.scalar())


async def clear_cache(store) -> None:
    sessions, corpus = store
    async with sessions() as db:
        async with db.begin():
            await db.execute(
                text("DELETE FROM memory_claim_embeddings WHERE corpus_id = :c"), {"c": corpus}
            )


async def test_ingestion_populates_the_cache(memory_ingestion_db):
    sessions, corpus = memory_ingestion_db
    from api.services.ingestion import IngestionService

    service = IngestionService(memory_enabled=True)
    await service.ingest_file(source(DATABASE), "docs/storage.md", corpus)
    assert await cache_count(memory_ingestion_db) == 1


async def test_cache_hit_returns_exactly_the_uncached_answer(memory_ingestion_db):
    from api.services.ingestion import IngestionService

    service = IngestionService(memory_enabled=True)
    await service.ingest_file(source(DATABASE), "docs/storage.md", memory_ingestion_db[1])
    await service.ingest_file(
        source(REASON, key="decision.database.reason"), "docs/why.md", memory_ingestion_db[1]
    )

    warm = await ask(memory_ingestion_db)
    assert await cache_count(memory_ingestion_db) == 2
    await clear_cache(memory_ingestion_db)
    cold = await ask(memory_ingestion_db)

    assert warm.canonical_json() == cold.canonical_json()
    # Both calls must have scored the same candidates, or the comparison above
    # would be equal for the wrong reason.
    assert warm.evidence or warm.constraints, "the query must have run the gate"


async def test_dropping_the_table_answers_identically(memory_ingestion_db):
    from api.services.ingestion import IngestionService

    service = IngestionService(memory_enabled=True)
    await service.ingest_file(source(DATABASE), "docs/storage.md", memory_ingestion_db[1])
    before = await ask(memory_ingestion_db)

    await clear_cache(memory_ingestion_db)
    after = await ask(memory_ingestion_db)

    assert after.canonical_json() == before.canonical_json()
    assert await cache_count(memory_ingestion_db) == 0


async def test_a_row_whose_text_changed_is_ignored(memory_ingestion_db):
    from api.services.ingestion import IngestionService

    service = IngestionService(memory_enabled=True)
    await service.ingest_file(source(DATABASE), "docs/storage.md", memory_ingestion_db[1])
    baseline = await ask(memory_ingestion_db)

    sessions, corpus = memory_ingestion_db
    async with sessions() as db:
        await db.execute(
            text(
                "UPDATE memory_claim_embeddings SET representation_hash = :h WHERE corpus_id = :c"
            ),
            {"h": "f" * 64, "c": corpus},
        )
        await db.commit()

    assert (await ask(memory_ingestion_db)).canonical_json() == baseline.canonical_json()


@pytest.mark.parametrize(
    "column,value", [("embedding_model", "other-model"), ("embedding_dimension", 7)]
)
async def test_a_row_from_another_model_is_ignored(memory_ingestion_db, column, value):
    from api.services.ingestion import IngestionService

    service = IngestionService(memory_enabled=True)
    await service.ingest_file(source(DATABASE), "docs/storage.md", memory_ingestion_db[1])
    baseline = await ask(memory_ingestion_db)

    sessions, corpus = memory_ingestion_db
    async with sessions() as db:
        await db.execute(
            text(f"UPDATE memory_claim_embeddings SET {column} = :v WHERE corpus_id = :c"),
            {"v": value, "c": corpus},
        )
        await db.commit()

    assert (await ask(memory_ingestion_db)).canonical_json() == baseline.canonical_json()


async def test_cached_vectors_select_the_same_keys_and_embed_only_the_question(
    memory_ingestion_db,
):
    from api.services import memory
    from api.services.ingestion import IngestionService
    from api.services.memory_public import State

    # The fixture installs FixedEmbedder, so the cache rows carry its identity.
    # Reading with a different identity would be rejected, which is the point.
    embedder = ing.FixedEmbedder()

    service = IngestionService(memory_enabled=True)
    await service.ingest_file(source(DATABASE), "docs/storage.md", memory_ingestion_db[1])
    await service.ingest_file(
        source(REASON, key="decision.database.reason"), "docs/why.md", memory_ingestion_db[1]
    )
    sessions, corpus = memory_ingestion_db
    request = MemoryRequest(
        corpus=await corpus_name(memory_ingestion_db),
        query=QUESTION,
        budget=128000,
        valid_at=VALID_AT,
    )
    intent = interpret(request)
    policy = RelevancePolicy.configured()

    async with sessions() as db:
        versions = await memory.history(db, corpus)
        state = State(as_of=None, valid_at=VALID_AT)
        history = memory_public.project(
            versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
        )
        wanted, _texts = await claim_embeddings.pending(db, corpus)
        cached = await claim_embeddings.load_cached(
            db, corpus, wanted, embedder.model_name, embedder.dimension
        )
        fresh, _, work_fresh = relevance(history, request, intent.name, embedder, policy)
        warm, _, work_warm = relevance(
            history, request, intent.name, embedder, policy, claim_vectors=cached
        )

    assert cached, "ingestion should have filled the cache for this corpus"
    assert warm == fresh
    assert work_warm["claim_vectors_cached"] == len(cached)
    assert work_warm["embedding_texts"] == 1, "only the question should be embedded"
    assert work_fresh["embedding_texts"] == 1 + len(cached)


async def test_reindex_rebuilds_the_cache_from_the_archive(memory_ingestion_db):
    from api.services.ingestion import IngestionService
    from api.services.rehydrate import backfill_claim_embeddings

    service = IngestionService(memory_enabled=True)
    await service.ingest_file(source(DATABASE), "docs/storage.md", memory_ingestion_db[1])
    await clear_cache(memory_ingestion_db)
    assert await cache_count(memory_ingestion_db) == 0

    sessions, corpus = memory_ingestion_db
    async with sessions() as db:
        async with db.begin():
            written = await backfill_claim_embeddings(db, corpus)

    assert written == 1
    assert await cache_count(memory_ingestion_db) == 1


def test_the_cache_key_describes_the_embedded_text():
    one = claim_embeddings.representation_of("claim text", "a.key", "docs/x.md")
    assert one == "claim text a.key docs/x.md"
    assert claim_embeddings.representation_hash(one) == claim_embeddings.representation_hash(one)
    assert claim_embeddings.representation_hash(one) != claim_embeddings.representation_hash(
        "claim text a.key docs/y.md"
    )
