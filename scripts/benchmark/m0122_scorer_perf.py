"""M012.2 Experiment A/E: what does the vectorised scorer do to the real path?

Times the public query path, not the scorer in isolation, because the scorer is
only interesting through the latency a developer waits for. Both orders of the
same question set, scalar and vectorised, on the same corpus, with the claim
representation cache warm in both cases so the comparison is of the scoring
arithmetic and nothing else.

Raw samples are kept. This host has shown run-to-run variance of about 2x, so a
median of four is not a measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
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

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"
PROBE = (
    "What is the current database?",
    "Who owns the Orders Service?",
    "What is the current job queue?",
    "What is the current search engine?",
)


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def run(claims: int, repeats: int) -> dict:
    from m0117_scale import Offline, build_documents

    from api.models.memory import MemoryRequest
    from api.services.corpora import get_corpus_by_name
    from api.services.ingestion import IngestionService
    from api.services.memory import _load
    from api.services.memory_public import State, project
    from api.services.memory_query import query as run_query
    from api.services.rehydrate import backfill_claim_embeddings

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "perf_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out: dict = {"claims": claims, "repeats": repeats, "scalar_ms": [], "vector_ms": []}

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
            service.embedder = Offline()
            for path, content in build_documents(claims):
                async with _session(conn) as db:
                    await service._ingest_content(db, content, path, corpus_id)
            async with _session(conn) as db:
                async with db.begin():
                    await backfill_claim_embeddings(db, corpus_id)
            async with _session(conn) as db:
                row = await get_corpus_by_name(db, schema)
                versions = await _load(db, row["id"], chunk_text=False)
            out["versions"] = len(versions)
            out["actual_claims"] = sum(len(v["claims"]) for v in versions)

            state = State(as_of=None, valid_at=VALID_AT)
            requests = {
                q: (
                    MemoryRequest(corpus=schema, query=q, valid_at=VALID_AT, budget=128000),
                    project(
                        versions,
                        MemoryRequest(
                            corpus=schema, query=q, valid_at=VALID_AT, budget=128000
                        ).model_copy(update={"query": "", "path": None}),
                        "pack",
                        state,
                        None,
                    ),
                )
                for q in PROBE
            }

            async with _session(conn) as db:  # warm the resident model
                request, full = requests[PROBE[0]]
                await run_query(full, request, db=db, corpus_id=corpus_id)

            for _ in range(repeats):
                for question in PROBE:
                    request, full = requests[question]
                    for vectorized, bucket in ((False, out["scalar_ms"]), (True, out["vector_ms"])):
                        t0 = time.perf_counter()
                        async with _session(conn) as db:
                            await run_query(
                                full, request, db=db, corpus_id=corpus_id, vectorized=vectorized
                            )
                        bucket.append((time.perf_counter() - t0) * 1000)
        finally:
            await outer.rollback()
    await engine.dispose()
    return out


def stats(values: list[float]) -> dict:
    ordered = sorted(values)
    n = len(ordered)
    return {
        "n": n,
        "p50": round(ordered[n // 2], 2),
        "p95": round(ordered[min(n - 1, int(n * 0.95))], 2),
        "mean": round(sum(ordered) / n, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000,5000")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0122-scorer-perf.json"))
    args = parser.parse_args()

    env = {
        "commit": os.popen(f"git -C {ROOT} rev-parse HEAD").read().strip(),
        "python": platform.python_version(),
        "postgres": "15.19",
        "pgvector": "0.8.6",
        "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        "repeats": args.repeats,
        "cache": "claim representation cache warm for both arms",
    }
    rows = []
    print(
        f"{'claims':>7} {'scalar p50':>11} {'vector p50':>11} {'speedup':>8} "
        f"{'scalar p95':>11} {'vector p95':>11}"
    )
    for size in [int(s) for s in args.sizes.split(",")]:
        row = asyncio.run(run(size, args.repeats))
        scalar, vector = stats(row["scalar_ms"]), stats(row["vector_ms"])
        row["scalar"] = scalar
        row["vector"] = vector
        row["speedup_p50"] = round(scalar["p50"] / vector["p50"], 2)
        rows.append(row)
        print(
            f"{row['actual_claims']:>7} {scalar['p50']:>11.2f} {vector['p50']:>11.2f} "
            f"{row['speedup_p50']:>7.2f}x {scalar['p95']:>11.2f} {vector['p95']:>11.2f}",
            flush=True,
        )

    Path(args.out).write_text(
        json.dumps({"environment": env, "rows": rows}, indent=2, sort_keys=True) + "\n"
    )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
