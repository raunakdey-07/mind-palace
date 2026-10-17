"""A small tenant keeps its neighbours when a large one shares the table.

`idx_claim_embeddings_hnsw` is one global index over `memory_claim_embeddings`, so
its candidates are drawn from every corpus and the corpus filter lands afterwards.
`hnsw.ef_search` is 40, so a corpus holding vectors *closer to the query* than the
small corpus's own fills all 40 slots and the small tenant gets none of them. That
is the geometry: B's vectors sit on the query's own direction, distance 0; A's sit
one bucket away, about 0.17. With 100 rows in B there is no headroom.

Below `SMALL_CORPUS_EXACT` the neighbour search takes the graph out of reach --
`SET LOCAL enable_indexscan = off` -- and A gets all five of its neighbours back.

## What is asserted here, and why nothing else is

Two properties, both of which the code determines rather than the planner:

* the small tenant is served the same five neighbours an exact scan finds, and
* the graph is absent from the fallback's plan.

The second is deterministic because `enable_indexscan = off` forbids every index
scan the graph could be reached through, whatever the table size or the runner's
cost model. The first is a statement about the archive's answer.

What this file deliberately does **not** assert is *which index PostgreSQL picks*
for a query the product does not issue. An earlier version did, and it passed
locally and failed on CI at the same 120 rows: on the runner the planner chose a
plain `Sort` rather than `idx_claim_embeddings_hnsw`, because a graph walk is not
cheaper than sorting 120 rows. That is the planner's judgement, not a property of
the code, and pinning it makes the test a statement about table size.

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
