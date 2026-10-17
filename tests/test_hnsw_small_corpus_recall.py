"""A small tenant keeps its neighbours when a large one shares the table.

`idx_claim_embeddings_hnsw` is one global index over `memory_claim_embeddings`, so
its candidates are drawn from every corpus in the database and the corpus filter is
applied afterwards. On `pgvector 0.5.1` -- which has no `iterative_scan` -- that
starves a small corpus. Measured on 50 claims in A against 10,000 in B:

    Index Scan using idx_claim_embeddings_hnsw on memory_claim_embeddings
    RLS-filtered ANN returned 1 of 5 requested
    exact top-5 present in ANN: 0 of 5

Below `SMALL_CORPUS_EXACT` vectors, `_nearest_active_claims` takes the graph out of
reach and sorts the corpus's own rows. It stays exact, and it stays cheap:
`enable_indexscan = off` does not disable a *bitmap* index scan, so the plan is

    Bitmap Heap Scan on memory_claim_embeddings (actual rows=50)
      ->  Bitmap Index Scan on idx_claim_embeddings_lookup (actual rows=50)

which reads corpus A and never touches corpus B.

Claims and evidence are written by the product, which owns the deferred triggers,
the id derivation and the `004` CHECKs. Only the cached vectors are injected, with
the same `CAST(:vector AS vector)` the product uses, because a real encoder would
cost about 250 seconds per 1,000 strings and the geometry is what this file is
about.

## What this test proves, and what it does not

It proves two things. That `SMALL_CORPUS_EXACT` is set where the fixture straddles
it, and that below the threshold the neighbour search produces
`Bitmap Index Scan on idx_claim_embeddings_lookup` on a plan that differs from the
graph-backed one -- so the fallback runs, reads only the corpus's own rows, and
returns the same five neighbours an exact scan does.

It does **not** reproduce the starvation. Two reasons, both measured. With
`corpus_id = :c` in the predicate -- which is what `NEIGHBOUR_SQL` sends -- the
planner picks a corpus-scoped B-tree at this scale whether or not the fallback is
on, so 50 against 2,000 is served correctly either way. Starvation appeared only
when the corpus filter arrived from RLS *after* the ANN, and this fixture connects
as the migration role, which bypasses RLS. Exercising the RLS shape needs the
session's runtime role switched in, which this file does not do.

So: set `SMALL_CORPUS_EXACT = 0` and the threshold guard fails while the recall
assertion still passes. That is the honest result, and it is why the guard is a
test of its own rather than a comment on the recall one.
"""

import hashlib
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.services import claim_embeddings
from api.services.memory import SMALL_CORPUS_EXACT, _nearest_active_claims

ROOT = Path(__file__).resolve().parent.parent

SMALL = 50
LARGE = 2_000
DIM = 384
MODEL = "hnsw-test"


def _unit_vector(bucket: int) -> str:
    """A 384-d pgvector literal dominated by coordinate `bucket + 1`.

    Bracketed, because `CAST($n AS vector)` wants pgvector's own text form and a
    bare comma list is rejected as a malformed vector literal.

    Both corpora draw from the same ten directions, so they genuinely overlap in
    vector space. A has `SMALL / 10` claims per direction and B has
    `LARGE / 10`, which is the imbalance that starves the ANN walk.
    """
    return "[" + ",".join("0.95" if i == bucket + 1 else "0.03" for i in range(1, DIM + 1)) + "]"


def _query_vector(bucket: int) -> list[float]:
    """The same geometry as the list the service is handed."""
    return [0.95 if i == bucket else 0.03 for i in range(DIM)]


def _id(seed: str) -> str:
    """Every id column here is `character(64)`; a short one is a CHECK failure."""
    return hashlib.sha256(seed.encode()).hexdigest()


@pytest.fixture
async def store(monkeypatch):
    """A real corpus on the real schema, with the runtime role, rolled back.

    Every migration is applied, so `012`'s HNSW index is present -- without it
    there is nothing to starve and the test would pass for the wrong reason.
    """
    from api.services import ingestion as ingestion_service
    from api.services import remember as remember_service
    from api.services import db as db_module
    from tenant_harness import require_auth_mode

    require_auth_mode(monkeypatch)
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
    corpus = _id("corpus-small")
    other = _id("corpus-large")
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
                    {"a": corpus, "b": other},
                )
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:

                    class Savepoint:
                        def begin(self):
                            return db.begin_nested()

                        def __getattr__(self, name):
                            return getattr(db, name)

                    from contextlib import asynccontextmanager

                    @asynccontextmanager
                    async def sessions():
                        yield Savepoint()

                    for module in (ingestion_service, remember_service, db_module):
                        if hasattr(module, "session_scope"):
                            monkeypatch.setattr(module, "session_scope", sessions)

                    yield db, corpus, other
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def two_corpora(store):
    """50 claims in one corpus and 2,000 in another, each with a cached vector."""
    db, small, large = store
    from api.services.ingestion import IngestionService

    service = IngestionService(memory_enabled=True, require_embeddings=False)
    async with db.begin():
        for corpus, count in ((small, SMALL), (large, LARGE)):
            body = "\n".join(
                f"- Corpus {corpus[:6]} observation {j}, direction {j % 10}."
                for j in range(1, count + 1)
            )
            await service.ingest_file(
                content=f"# Observations\n\n{body}\n",
                path=f"obs-{corpus[:6]}.md",
                corpus_id=corpus,
            )
        rows = (
            await db.execute(
                sa.text(
                    "SELECT m.corpus_id, m.id FROM memory_claims m"
                    " WHERE m.corpus_id IN (:a, :b) ORDER BY m.corpus_id, m.key"
                ),
                {"a": small, "b": large},
            )
        ).all()
        buckets: dict[str, list[tuple[str, str]]] = {small: [], large: []}
        for j, (corpus_id, claim_id) in enumerate(rows):
            buckets[corpus_id].append((claim_id, _unit_vector(j % 10)))
        for corpus_id, entries in buckets.items():
            for claim_id, vector in entries:
                await db.execute(
                    sa.text(claim_embeddings.INSERT),
                    {
                        "corpus": corpus_id,
                        "claim": claim_id,
                        "hash": hashlib.md5(claim_id.encode()).hexdigest(),
                        "model": MODEL,
                        "dimension": DIM,
                        "version": "v1",
                        "vector": vector,
                    },
                )
        await db.execute(sa.text("ANALYZE memory_claim_embeddings"))
    assert len(buckets[small]) == SMALL and len(buckets[large]) == LARGE
    yield db, small, large


async def _neighbours(db, corpus: str, bucket: int) -> list[str]:
    rows = await _nearest_active_claims(
        db, corpus, _query_vector(bucket), model=MODEL, dimension=DIM, limit=5
    )
    return [r["id"] for r in rows]


async def _exact(db, corpus: str, bucket: int) -> list[str]:
    """The same top-5 with the graph index out of reach."""
    await db.execute(sa.text("SET LOCAL enable_indexscan = off"))
    await db.execute(sa.text("SET LOCAL enable_indexonlyscan = off"))
    return await _neighbours(db, corpus, bucket)


async def test_the_two_corpora_straddle_the_threshold(two_corpora):
    """The condition the fallback exists for, asserted rather than assumed."""
    _, small, large = two_corpora
    counts = dict(
        (
            await two_corpora[0].execute(
                sa.text(
                    "SELECT corpus_id, count(*) FROM memory_claim_embeddings"
                    " WHERE embedding_model = :m GROUP BY corpus_id"
                ),
                {"m": MODEL},
            )
        ).all()
    )
    assert counts[small] == SMALL and counts[large] == LARGE
    assert SMALL <= SMALL_CORPUS_EXACT < LARGE, "the fixture no longer straddles it"


async def test_the_small_tenant_keeps_its_whole_neighbourhood(two_corpora):
    """The regression: five of five, not the one of five the global graph returns."""
    db, small, _ = two_corpora
    served = await _neighbours(db, small, 1)
    exact = await _exact(db, small, 1)
    assert len(exact) == 5
    assert served == exact, f"served {served} against exact {exact}"


async def test_the_fallback_does_not_scan_the_other_corpus(two_corpora):
    """`enable_bitmapscan` survives `enable_indexscan = off`.

    Without that the fallback would be correct and useless: every row in the table
    read and filtered to find fifty.
    """
    db, small, _ = two_corpora
    query = (
        "EXPLAIN (COSTS OFF) SELECT claim_id FROM memory_claim_embeddings"
        " WHERE corpus_id = :c AND embedding_model = :m AND embedding_dimension = :dim"
        "   AND embedding IS NOT NULL"
        " ORDER BY embedding <=> (SELECT embedding FROM memory_claim_embeddings"
        " WHERE corpus_id = :c AND embedding_model = :m LIMIT 1)"
        " LIMIT 5"
    )
    params = {"c": small, "m": MODEL, "dim": DIM}
    normal = "\n".join(r[0] for r in (await db.execute(sa.text(query), params)).all())
    await db.execute(sa.text("SET LOCAL enable_indexscan = off"))
    await db.execute(sa.text("SET LOCAL enable_indexonlyscan = off"))
    fallback = "\n".join(r[0] for r in (await db.execute(sa.text(query), params)).all())
    assert "Seq Scan" not in fallback, fallback
    assert "idx_claim_embeddings_lookup" in fallback, fallback
    assert fallback != normal, "the fallback did not change the plan"
