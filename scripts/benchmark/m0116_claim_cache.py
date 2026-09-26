"""M011.6: does caching claim representations flatten the cost curve?

Hypothesis: claim representations derive from immutable claim text, so encoding
them once and reusing them makes per-query cost independent of archive size.

Method: ingest corpus prefixes, then time the same questions three ways.

    cold        empty cache, first question
    warm        cache filled by a prior question
    uncached    cache explicitly emptied before every question

An empty cache must reproduce the uncached path exactly, or the cache is not
safe. That equality is asserted, not assumed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0115_dataset import build_corpus  # noqa: E402
from m0115_experiments import VALID_AT, _async_url, _session  # noqa: E402

from api.services.rehydrate import backfill_claim_embeddings  # noqa: E402
from memory_pack import MemoryPack  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

PROBE = (
    "What is the current primary datastore?",
    "Who owns the Orders Service?",
    "What is the current job queue?",
    "What is the current search engine?",
)


def _migrations(conn):
    for path in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


async def ask(conn, schema: str, question: str) -> tuple[float, str]:
    """One authoritative query through the real public path, timed."""
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute_in_session

    request = MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000)
    start = time.perf_counter()
    async with _session(conn) as db:
        response = await execute_in_session(db, "query", request)
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed, MemoryPack.from_json(response.canonical_json()).digest()


async def clear_cache(conn, corpus_id: str) -> None:
    async with _session(conn) as db:
        async with db.begin():
            await db.execute(
                text("DELETE FROM memory_claim_embeddings WHERE corpus_id = :c"), {"c": corpus_id}
            )


async def cache_rows(conn, corpus_id: str) -> int:
    async with _session(conn) as db:
        row = await db.execute(
            text("SELECT count(*) FROM memory_claim_embeddings WHERE corpus_id = :c"),
            {"c": corpus_id},
        )
    return int(row.scalar())


async def run(url: str, repeats: int) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    docs = build_corpus()
    report = {"repeats": repeats, "points": []}

    for prefix in (10, 25, 45, len(docs)):
        schema = "m116_" + uuid4().hex[:10]
        corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
                await conn.run_sync(_migrations)
                await conn.execute(
                    text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                    {"id": corpus_id, "name": schema},
                )
                service = IngestionService()
                for doc in docs[:prefix]:
                    async with _session(conn) as db:
                        await service._ingest_content(db, doc.content, doc.path, corpus_id)
                claims = int(
                    (
                        await conn.execute(
                            text("SELECT count(*) FROM memory_claims WHERE corpus_id = :c"),
                            {"c": corpus_id},
                        )
                    ).scalar()
                )

                # 1. Uncached: empty the cache before every question.
                uncached, uncached_digests = [], {}
                for _ in range(repeats):
                    for question in PROBE:
                        await clear_cache(conn, corpus_id)
                        ms, digest = await ask(conn, schema, question)
                        uncached.append(ms)
                        uncached_digests[question] = digest

                # 2. Warm: fill the cache the way the product does, through the
                # reindex backfill, then time with the cache in place. A query
                # never writes, so this is the only way a cache gets built.
                await clear_cache(conn, corpus_id)
                async with _session(conn) as db:
                    async with db.begin():
                        await backfill_claim_embeddings(db, corpus_id)
                rows = await cache_rows(conn, corpus_id)
                warm, warm_digests = [], {}
                for _ in range(repeats):
                    for question in PROBE:
                        ms, digest = await ask(conn, schema, question)
                        warm.append(ms)
                        warm_digests[question] = digest

                point = {
                    "documents": prefix,
                    "claims": claims,
                    "cache_rows": rows,
                    "uncached_p50": round(statistics.median(uncached), 2),
                    "warm_p50": round(statistics.median(warm), 2),
                    "speedup": round(statistics.median(uncached) / statistics.median(warm), 2),
                    "digests_identical": uncached_digests == warm_digests,
                }
                point["uncached_ms_per_claim"] = round(point["uncached_p50"] / max(1, claims), 2)
                point["warm_ms_per_claim"] = round(point["warm_p50"] / max(1, claims), 2)
                report["points"].append(point)
                print(
                    f"  claims={claims:>3} cache_rows={rows:>3}  "
                    f"uncached p50={point['uncached_p50']:>7.2f} ms  "
                    f"warm p50={point['warm_p50']:>7.2f} ms  "
                    f"x{point['speedup']:<5} identical={point['digests_identical']}",
                    flush=True,
                )
            finally:
                await outer.rollback()
    await engine.dispose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0116-claim-cache.json"))
    args = parser.parse_args()
    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    print("M011.6 claim representation cache")
    report = asyncio.run(run(url, args.repeats))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    ok = all(p["digests_identical"] for p in report["points"])
    print(f"\nall digests identical: {ok}")
    print(f"written: {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
