"""M012.2: does the cached-vector load cross a plan cliff under repetition?

Two harnesses measured the same work and disagreed by 13x. The difference
between them is the number of executions: the profiler ran two, the end-to-end
harness ran twenty-four. asyncpg prepares a statement after five executions and
PostgreSQL may then choose a generic plan.

That is not a benchmark curiosity. A running server holds one pooled
connection and issues this query for every warm request, so if the plan changes
after five calls, every caller after the fifth pays the new plan. This measures
per-execution latency in sequence, under each plan mode, and captures both plans.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))

URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def build(claims: int):
    from m0117_scale import Offline, build_documents

    from api.services.claim_embeddings import pending
    from api.services.ingestion import IngestionService

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "pc_" + uuid4().hex[:10]
    corpus_id = __import__("hashlib").sha256(uuid4().bytes).hexdigest()
    async with engine.connect() as conn:
        outer = await conn.begin()
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        await conn.run_sync(_migrations)
        await conn.execute(
            text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
            {"id": corpus_id, "name": schema},
        )
        service = IngestionService()
        service.embedder = Offline()
        for path, content in build_documents(claims):
            async with _session(conn) as db:
                await service._ingest_content(db, content, path, corpus_id)
        # Fill the cache with the real embedder, as production does.
        from api.services.claim_embeddings import store
        from api.services.embedder import Embedder

        embedder = Embedder()
        async with _session(conn) as db:
            wanted, texts = await pending(db, corpus_id)
            vectors = dict(zip(wanted, embedder.embed(texts)))
        async with _session(conn) as db:
            async with db.begin():
                await store(
                    db,
                    corpus_id,
                    wanted,
                    vectors,
                    embedder.model_name,
                    embedder.dimension,
                    "bench",
                    commit=False,
                )
        async with _session(conn) as db:
            wanted, _ = await pending(db, corpus_id)
        await outer.rollback()
    await engine.dispose()
    return schema, corpus_id, wanted


async def main(claims: int, calls: int):
    from api.services.claim_embeddings import load_cached
    from api.services.embedder import Embedder

    schema, corpus_id, wanted = await build(claims)
    embedder = Embedder()
    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    report: dict = {"claims": claims, "wanted": len(wanted), "calls": calls, "modes": {}}

    async with engine.connect() as conn:
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        await conn.commit()
        for mode in ("auto", "force_custom_plan", "force_generic_plan"):
            await conn.execute(text(f"SET plan_cache_mode = {mode}"))
            samples = []
            for _ in range(calls):
                t0 = time.perf_counter()
                async with _session(conn) as db:
                    await load_cached(
                        db, corpus_id, wanted, embedder.model_name, embedder.dimension
                    )
                samples.append((time.perf_counter() - t0) * 1000)
            rows = await conn.execute(
                text(
                    "EXPLAIN "
                    + (
                        "SELECT claim_id, embedding::text FROM memory_claim_embeddings "
                        "WHERE corpus_id = :corpus AND claim_id = ANY(CAST(:ids AS CHAR(64)[])) "
                        "AND embedding_model = :model AND embedding_dimension = :dimension "
                        "AND representation_hash = ANY(CAST(:hashes AS CHAR(64)[]))"
                    )
                ),
                {
                    "corpus": corpus_id,
                    "ids": list(wanted),
                    "hashes": list(wanted.values()),
                    "model": embedder.model_name,
                    "dimension": embedder.dimension,
                },
            )
            plan = "\n".join(r[0] for r in rows.fetchall())
            report["modes"][mode] = {
                "samples_ms": [round(s, 1) for s in samples],
                "p50": round(statistics.median(samples), 1),
                "first": round(samples[0], 1),
                "last": round(samples[-1], 1),
                "plan": plan,
            }
        await conn.rollback()
    await engine.dispose()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--claims", type=int, default=5000)
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0122-plan-cliff.json"))
    args = parser.parse_args()
    result = asyncio.run(main(args.claims, args.calls))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"claims={result['claims']} wanted={result['wanted']} calls={result['calls']}\n")
    for mode, m in result["modes"].items():
        print(
            f"  {mode:<20} p50={m['p50']:>9.1f} ms  "
            f"first={m['first']:>8.1f}  last={m['last']:>9.1f}"
        )
        print(f"    samples: {m['samples_ms']}")
    print(f"\nwritten: {args.out}")
