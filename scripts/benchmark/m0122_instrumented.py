"""M012.2: one instrumented measurement of the real public path.

Three harnesses had reported three different costs for the same work, so none of
them is trusted. This one is the replacement, and it is deliberately paranoid:

* explicit timers around database execution, cached-vector load and decode,
  projection, relevance scoring, selection and pack construction
* an assertion that the claim vector cache actually served rows, so a run can
  never silently measure the embedding path instead
* raw samples retained, because this host has shown large run-to-run variance
* the same corpus, query set, warm-up policy and timing boundary for every size

It also records whether a run is trustworthy, so a bad run is discarded rather
than reported.
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

URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"
VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
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


async def instrumented_once(conn, schema: str, corpus_id: str, question: str) -> dict:
    """Time one query exactly as the public path does it, stage by stage."""
    from api.models.memory import MemoryRequest
    from api.services import memory as memory_service
    from api.services.claim_embeddings import load_cached, representation, representation_hash
    from api.services.corpora import get_corpus_by_name
    from api.services.embedder import Embedder
    from api.services.memory_public import State, _claims, bounded_pack, project
    from api.services.memory_query import interpret, select

    out: dict = {}
    total = time.perf_counter()
    request = MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000)

    t0 = time.perf_counter()
    async with _session(conn) as db:
        corpus = await get_corpus_by_name(db, schema)
    out["db_corpus_lookup_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    async with _session(conn) as db:
        versions = await memory_service._load(db, corpus["id"], chunk_text=False)
    out["archive_load_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    state = State(as_of=None, valid_at=VALID_AT)
    full = project(
        versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
    )
    out["project_ms"] = (time.perf_counter() - t0) * 1000

    embedder = Embedder()
    wanted = {c.id: representation_hash(representation(c)) for c in _claims(full)}

    t0 = time.perf_counter()
    async with _session(conn) as db:
        cached = await load_cached(
            db, corpus["id"], wanted, embedder.model_name, embedder.dimension
        )
    out["vector_cache_ms"] = (time.perf_counter() - t0) * 1000
    out["cache_hits"] = len(cached)
    out["cache_misses"] = len(wanted) - len(cached)
    # A run that silently fell back to embedding is not measuring the warm path.
    if out["cache_misses"]:
        raise RuntimeError(f"{out['cache_misses']} cache misses; this is not a warm run")

    t0 = time.perf_counter()
    async with _session(conn) as db:
        result = select(full, request, interpret(request), embedder, claim_vectors=cached)
    out["select_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    packed = bounded_pack(result, request.budget)
    out["pack_ms"] = (time.perf_counter() - t0) * 1000
    out["pack_bytes"] = len(packed.canonical_json())
    out["total_ms"] = (time.perf_counter() - total) * 1000
    return out


async def measure(claims: int, repeats: int) -> dict:
    """Build the corpus and measure it inside one transaction.

    Building in a separate transaction and rolling it back would leave the
    measurement pointing at a schema that no longer exists, which is exactly the
    class of harness bug behind the previous 13x discrepancy.
    """
    from m0117_scale import Offline, build_documents

    from api.services.claim_embeddings import pending, store
    from api.services.embedder import Embedder
    from api.services.ingestion import IngestionService

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "mp_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    rows: list[dict] = []

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
                counted = await db.execute(
                    text("SELECT count(*) FROM memory_claim_embeddings WHERE corpus_id = :c"),
                    {"c": corpus_id},
                )
                cached_rows = int(counted.scalar())
            if cached_rows != len(wanted):
                raise RuntimeError(f"cache holds {cached_rows} for {len(wanted)} claims")

            for question in PROBE:  # warm-up, not recorded
                await instrumented_once(conn, schema, corpus_id, question)
            for _ in range(repeats):
                for question in PROBE:
                    rows.append(await instrumented_once(conn, schema, corpus_id, question))
        finally:
            await outer.rollback()
    await engine.dispose()
    return {"claims": claims, "cached_rows": cached_rows, "repeats": repeats, "rows": rows}


STAGES = (
    "db_corpus_lookup_ms",
    "archive_load_ms",
    "project_ms",
    "vector_cache_ms",
    "select_ms",
    "pack_ms",
    "total_ms",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000,5000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0122-instrumented.json"))
    args = parser.parse_args()

    env = {
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "python": platform.python_version(),
        "postgres": "15.19",
        "pgvector": "0.8.6",
        "numpy": __import__("numpy").__version__,
        "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        "repeats": args.repeats,
        "questions": len(PROBE),
        "cache": "claim vector cache asserted fully populated; zero misses allowed",
        "boundary": "archive load, projection, vector cache, select and pack, as the "
        "public query path runs them",
    }
    report = {"environment": env, "points": []}
    header = f"{'claims':>7} {'n':>4} " + " ".join(f"{s[:9]:>10}" for s in STAGES)
    print(header)
    for size in [int(s) for s in args.sizes.split(",")]:
        point = asyncio.run(measure(size, args.repeats))
        summary = {}
        for stage in STAGES:
            values = sorted(r[stage] for r in point["rows"])
            n = len(values)
            summary[stage] = {
                "p50": round(values[n // 2], 2),
                "p95": round(values[min(n - 1, int(n * 0.95))], 2),
                "min": round(values[0], 2),
                "max": round(values[-1], 2),
            }
        point["summary"] = summary
        point["raw"] = [{k: r[k] for k in STAGES} for r in point["rows"]]
        report["points"].append(point)
        print(
            f"{size:>7} {len(point['rows']):>4} "
            + " ".join(f"{summary[s]['p50']:>10.2f}" for s in STAGES)
        )

    print("\nstage share of p50 total:")
    for point in report["points"]:
        total = point["summary"]["total_ms"]["p50"] or 1
        parts = " ".join(
            f"{s.replace('_ms', '')}={point['summary'][s]['p50'] / total * 100:.1f}%"
            for s in STAGES[:-1]
        )
        print(f"  {point['claims']:>6}: {parts}")

    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
