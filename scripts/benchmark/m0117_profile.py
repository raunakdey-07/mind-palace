"""Profile one warm query so the dominant term is measured, not guessed.

The scale benchmark showed warm latency growing from ~52 ms at 100 claims to
seconds at thousands. The breakdown named a stage but not the code inside it.
This profiles a single warm query at a chosen size and reports the top
functions by cumulative time.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import hashlib
import importlib.util
import io
import os
import pstats
import sys
import time
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0117_scale import PROBE, Offline, build_documents  # noqa: E402

from api.services.rehydrate import backfill_claim_embeddings  # noqa: E402


def _migrations(conn):
    for path in sorted((Path(__file__).resolve().parents[2] / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


async def run(url: str, claims: int) -> dict:
    from api.models.memory import MemoryRequest
    from api.services.ingestion import IngestionService

    engine = create_async_engine(url, poolclass=NullPool)
    schema = "prof_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out: dict = {"claims_target": claims}
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
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    await service._ingest_content(db, content, path, corpus_id)
            async with AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db:
                async with db.begin():
                    await backfill_claim_embeddings(db, corpus_id)

            question = PROBE[0]
            request = MemoryRequest(corpus=schema, query=question, budget=128000)

            # Warm the connection and the resident model outside the profile.
            await _one(conn, schema, request)

            profiler = cProfile.Profile()
            profiler.enable()
            for _ in range(3):
                await _one(conn, schema, request)
            profiler.disable()
            buffer = io.StringIO()
            stats = pstats.Stats(profiler, stream=buffer).sort_stats("cumulative")
            stats.print_stats(28)
            out["profile"] = buffer.getvalue()
        finally:
            await outer.rollback()
        await engine.dispose()
    return out


async def _one(conn, schema: str, request) -> float:
    from api.services import memory
    from api.services.corpora import get_corpus_by_name
    from api.services.memory_public import State, execute_in_session, project

    start = time.perf_counter()
    async with AsyncSession(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as db:
        corpus = await get_corpus_by_name(db, schema)
        versions = await memory._load(db, corpus["id"], chunk_text=False)
        state = State(as_of=None, valid_at=None)
        project(
            versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
        )
        await execute_in_session(db, "query", request)
    return (time.perf_counter() - start) * 1000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claims", type=int, default=1000)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[2] / "docs/performance/m0117-profile.txt"),
    )
    args = parser.parse_args()
    url = os.environ.get("MEMORY_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1
    url = url.replace("postgresql://", "postgresql+asyncpg://")
    print(f"profiling a warm query over ~{args.claims} claims", flush=True)
    result = asyncio.run(run(url, args.claims))
    Path(args.out).write_text(result["profile"])
    print(result["profile"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
