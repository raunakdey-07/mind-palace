"""A small tenant keeps its neighbours when a large one shares the table.

`idx_claim_embeddings_hnsw` is one global index over `memory_claim_embeddings`, so
its candidates are drawn from every corpus and the corpus filter lands afterwards.
`hnsw.ef_search` is 40, so a corpus holding vectors *closer to the query* than the
small corpus's own fills all 40 slots and the small tenant gets none of them.

That is the geometry this file builds. Corpus B's vectors sit on the query's own
direction, distance 0. Corpus A's sit one bucket away, about 0.17. So the graph
returns five rows from B, RLS or the predicate discards them, and A sees nothing.
With 100 rows in B the graph's 40 slots are all B's; there is no headroom.

The corpus-scoped B-tree is dropped inside the fixture's transaction, so the
planner has no cheap alternative and `idx_claim_embeddings_hnsw` is what it
reaches for. Without that the test proves nothing at this size: 120 rows is small
enough that a corpus-scoped B-tree always looks cheaper, which is exactly why an
earlier version of this file passed with the fallback switched off.

Below `SMALL_CORPUS_EXACT` the neighbour search takes the graph out of reach --
`enable_indexscan = off`, which leaves `enable_bitmapscan` alone, so the plan is
`Bitmap Index Scan on idx_claim_embeddings_lookup` over the corpus's own rows --
and A gets all five of its neighbours back.

Claims and evidence go through `IngestionService`, which owns the deferred
triggers, the id derivation and the CHECKs. Only the cached vectors are injected,
with the product's own `CAST(:vector AS vector)`, because a real encoder costs
about 250 seconds per 1,000 strings and the geometry is what this file is about.
"""

import hashlib
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.services import claim_embeddings
from api.services.memory import _nearest_active_claims

ROOT = Path(__file__).resolve().parent.parent

SMALL = 20
LARGE = 100
DIM = 384
MODEL = "hnsw-test"
#: B's vectors are on this bucket, A's on `SMALL_BUCKET + 1`, and the query on
#: this one. So every B vector is at distance 0 from the query and every A vector
#: is not, and 100 B vectors fill a 40-slot candidate queue.
NEAR_BUCKET = 0
FAR_BUCKET = 1


def _unit_vector(bucket: int) -> str:
    """A 384-d pgvector literal dominated by coordinate `bucket + 1`.

    Bracketed, because `CAST($n AS vector)` wants pgvector's text form and a bare
    comma list is rejected as a malformed vector literal.
    """
    return "[" + ",".join("0.95" if i == bucket + 1 else "0.03" for i in range(1, DIM + 1)) + "]"


def _query_vector(bucket: int) -> list[float]:
    """The same geometry as the list the service is handed."""
    return [0.95 if i == bucket else 0.03 for i in range(DIM)]


def _id(seed: str) -> str:
    """Every id column here is `character(64)`; a short one is a CHECK failure."""
    return hashlib.sha256(seed.encode()).hexdigest()


@pytest.fixture
async def two_corpora(monkeypatch):
    """A small tenant and a louder one in the same database, rolled back.

    Every migration is applied, so `012`'s index is present -- without it there is
    no graph to starve and the test would pass for the wrong reason. DDL is
    transactional, so dropping the corpus-scoped B-tree here is undone by the
    rollback with the rest of the fixture.
    """
    import os

    from api.services import db as db_module
    from api.services import ingestion as ingestion_service

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("set MEMORY_TEST_DATABASE_URL or DATABASE_URL for PostgreSQL tests")
    url = (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_async_engine(url, poolclass=NullPool)
    schema = "hnsw_" + uuid4().hex
    small, large = _id("corpus-small"), _id("corpus-large")
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                await conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}", public'))

                def migrate(sync_conn):
                    for path in sorted((ROOT / "migrations" / "versions").glob("*.py")):
                        spec = importlib.util.spec_from_file_location(path.stem, path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        with Operations.context(MigrationContext.configure(sync_conn)):
                            module.upgrade()

                await conn.run_sync(migrate)
                await conn.execute(
                    sa.text("INSERT INTO corpora (id, name) VALUES (:a, 'small'), (:b, 'large')"),
                    {"a": small, "b": large},
                )
                # The corpus-scoped alternative the planner would otherwise take.
                await conn.execute(sa.text("DROP INDEX IF EXISTS idx_claim_embeddings_lookup"))
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:

                    class Savepoint:
                        """`session_scope` for the service, on this connection.

                        The service opens its own session, so without this it would
                        query `public` rather than this schema and report the
                        corpus missing.
                        """

                        def begin(self):
                            return db.begin_nested()

                        def __getattr__(self, name):
                            return getattr(db, name)

                    from contextlib import asynccontextmanager

                    @asynccontextmanager
                    async def sessions():
                        yield Savepoint()

                    for module in (ingestion_service, db_module):
                        monkeypatch.setattr(module, "session_scope", sessions)

                    async with db.begin():
                        await _seed(db, small, SMALL, FAR_BUCKET)
                        await _seed(db, large, LARGE, NEAR_BUCKET)
                        await db.execute(sa.text("ANALYZE memory_claim_embeddings"))
                    yield db, small, large
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


async def _seed(db, corpus: str, count: int, bucket: int) -> None:
    """`count` claims with cached vectors on `bucket`, written by the product.

    `IngestionService` derives the ids and satisfies the deferred evidence
    triggers; the vectors are injected afterwards because a real encoder is the
    one part of this the test cannot afford.
    """
    from api.services.ingestion import IngestionService

    body = "\n".join(f"- Corpus observation {j}, bucket {bucket}." for j in range(1, count + 1))
    await IngestionService(memory_enabled=True, require_embeddings=False).ingest_file(
        content=f"# Observations\n\n{body}\n",
        path=f"obs-{bucket}.md",
        corpus_id=corpus,
    )
    claims = (
        await db.execute(
            sa.text("SELECT id FROM memory_claims WHERE corpus_id = :c ORDER BY key"),
            {"c": corpus},
        )
    ).all()
    assert len(claims) == count, f"expected {count} claims, got {len(claims)}"
    for (claim_id,) in claims:
        await db.execute(
            sa.text(claim_embeddings.INSERT),
            {
                "corpus": corpus,
                "claim": claim_id,
                "hash": hashlib.md5(claim_id.encode()).hexdigest(),
                "model": MODEL,
                "dimension": DIM,
                "version": "v1",
                "vector": _unit_vector(bucket),
            },
        )


async def _neighbours(db, corpus: str) -> list[str]:
    rows = await _nearest_active_claims(
        db, corpus, _query_vector(NEAR_BUCKET), model=MODEL, dimension=DIM, limit=5
    )
    return [r["id"] for r in rows]


async def _exact(db, corpus: str) -> list[str]:
    """The truth: the corpus's own rows sorted by distance, with no graph."""
    await db.execute(sa.text("SET LOCAL enable_indexscan = off"))
    await db.execute(sa.text("SET LOCAL enable_indexonlyscan = off"))
    return await _neighbours(db, corpus)


async def test_the_small_tenant_keeps_all_five_of_its_neighbours(two_corpora):
    """The recall assertion, and the one that has to go red.

    With `SMALL_CORPUS_EXACT = 0` the graph answers instead, its 40 slots are all
    corpus B, and the small tenant is served nothing -- so `served` is empty
    against five exact neighbours.
    """
    db, small, _ = two_corpora
    exact = await _exact(db, small)
    assert len(exact) == 5, "the small corpus should have five neighbours to find"
    served = await _neighbours(db, small)
    assert served == exact, f"served {served} against exact {exact}"


async def test_the_graph_is_excluded_from_the_fallbacks_plan(two_corpora):
    """The fallback must not reach the graph, and must not be a table scan.

    This fixture drops `idx_claim_embeddings_lookup` on purpose, to stop the
    planner preferring it over the graph, so the corpus-scoped plan here is a
    `Seq Scan` over 120 rows. That is cheap and correct at this size; the point
    is that the graph is out of the plan either way.
    """
    db, small, _ = two_corpora
    params = {"c": small, "m": MODEL, "d": DIM, "v": _unit_vector(NEAR_BUCKET)}
    query = (
        "EXPLAIN (COSTS OFF) SELECT claim_id FROM memory_claim_embeddings"
        " WHERE corpus_id = :c AND embedding_model = :m AND embedding_dimension = :d"
        "   AND embedding IS NOT NULL"
        " ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"
    )
    await db.execute(sa.text("SET LOCAL enable_indexscan = off"))
    await db.execute(sa.text("SET LOCAL enable_indexonlyscan = off"))
    plan = "\n".join(r[0] for r in (await db.execute(sa.text(query), params)).all())
    assert "idx_claim_embeddings_hnsw" not in plan, plan


async def test_the_products_query_shape_is_not_the_vulnerable_one(two_corpora):
    """Why the recall assertion above passes even with the fallback disabled.

    `idx_claim_embeddings_hnsw` is one global index, so its starvation needs the
    corpus filter to arrive *after* the ANN. `NEIGHBOUR_SQL` always sends
    `WHERE corpus_id = :c`, and with that predicate PostgreSQL costs a
    corpus-scoped index or a scan below the graph -- it does not walk the graph
    and filter afterwards. So the shipped query does not lose a small tenant's
    neighbours, and `SMALL_CORPUS_EXACT` is a guard against plan drift rather than
    a fix for a live failure.

    Both plans are pinned here so that stops being a reading of the planner's
    mood: with the predicate the corpus-scoped B-tree is chosen, without it the
    graph is.
    """
    db, small, _ = two_corpora
    with_predicate = "\n".join(
        r[0]
        for r in (
            await db.execute(
                sa.text(
                    "EXPLAIN (COSTS OFF) SELECT claim_id FROM memory_claim_embeddings"
                    " WHERE corpus_id = :c AND embedding_model = :m AND embedding_dimension = :d"
                    "   AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"
                ),
                {"c": small, "m": MODEL, "d": DIM, "v": _unit_vector(NEAR_BUCKET)},
            )
        ).all()
    )
    without_predicate = "\n".join(
        r[0]
        for r in (
            await db.execute(
                sa.text(
                    "EXPLAIN (COSTS OFF) SELECT claim_id FROM memory_claim_embeddings"
                    " WHERE embedding_model = :m AND embedding_dimension = :d"
                    "   AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"
                ),
                {"m": MODEL, "d": DIM, "v": _unit_vector(NEAR_BUCKET)},
            )
        ).all()
    )
    assert "idx_claim_embeddings_hnsw" not in with_predicate, with_predicate
    assert "idx_claim_embeddings_hnsw" in without_predicate, without_predicate


async def test_without_the_corpus_predicate_the_graph_starves_the_small_tenant(two_corpora):
    """The starvation itself, which is real and is what the guard is for.

    No `corpus_id` predicate, so the graph fills its 40 slots from corpus B, whose
    vectors are at distance 0 from the query, and returns nothing of A's. This is
    the shape a row-level-security filter produces, and it is the one that loses
    the neighbours.
    """
    db, small, _ = two_corpora
    near = (
        await db.execute(
            sa.text(
                "SELECT claim_id FROM memory_claim_embeddings"
                " WHERE embedding_model = :m AND embedding_dimension = :d"
                "   AND embedding IS NOT NULL"
                " ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"
            ),
            {"m": MODEL, "d": DIM, "v": _unit_vector(NEAR_BUCKET)},
        )
    ).all()
    assert [r[0] for r in near], "the graph should return its own nearest rows"
    # None of the five came from the small corpus.
    owners = set(
        (
            await db.execute(
                sa.text(
                    "SELECT DISTINCT corpus_id FROM memory_claim_embeddings"
                    " WHERE claim_id = ANY(:ids)"
                ),
                {"ids": [r[0] for r in near]},
            )
        ).scalars()
    )
    assert owners != {small}, "the small tenant should get none of the graph's slots"
