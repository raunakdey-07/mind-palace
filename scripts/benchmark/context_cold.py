# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Cold-start cost of the three context surfaces, measured in fresh processes.

The bake-off warms the model before timing, which is the right way to compare
per-query cost but hides first-use cost. This runs each adapter once, in its own
process, with no warmup, and reports the single observation.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from memory_bakeoff import CACHE_ADR, CASES, DEPLOY, MIGRATION, STORAGE_V1, STORAGE_V2  # noqa: E402

CORPUS_FILES = (
    ("docs/storage.md", STORAGE_V1),
    ("docs/storage.md", STORAGE_V2),
    ("docs/migration.md", MIGRATION),
    ("adrs/adr-004-cache.md", CACHE_ADR),
    ("docs/deploy.md", DEPLOY),
)
QUESTION = "What is the current database?"


async def setup() -> int:
    """Ingest and commit, then exit. The measuring process must be fresh."""
    from memory_bakeoff import _async_url, _migrate
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from api.services.ingestion import IngestionService

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "cold_" + uuid4().hex[:12]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    async with engine.connect() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        await conn.run_sync(_migrate)
        await conn.execute(
            text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
            {"id": corpus_id, "name": schema},
        )
        await conn.commit()
        service = IngestionService()
        for path, content in CORPUS_FILES:
            async with AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db:
                await service._ingest_content(db, content, path, corpus_id)
        await conn.commit()
    await engine.dispose()
    print(schema)
    return 0


async def main(adapter: str) -> int:
    from memory_bakeoff import _async_url
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from api.models.memory import MemoryRequest
    from api.services.context_packer import pack_context
    from api.services.context_service import build_context
    from api.services.corpora import get_corpus_by_name
    from api.services.embedder import Embedder
    from api.services.memory_public import execute_in_session
    from api.services.retrieval import RetrievalService

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = os.environ["COLD_SCHEMA"]
    question = next(c["question"] for c in CASES if c["id"] == "current")

    async with engine.connect() as conn:
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        await conn.commit()

        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db:
            start = time.perf_counter()
            if adapter == "A":
                row = await get_corpus_by_name(db, schema)
                vector = Embedder().embed_single(question)
                found = await RetrievalService(db).search(
                    vector,
                    k=8,
                    hybrid=True,
                    rrf=True,
                    query_text=question,
                    corpus_id=row["id"],
                )
                pack = pack_context(question, found, 4096, "hybrid_rrf")
                detail = f"{len(pack.chunks)} chunks"
            elif adapter == "B":
                response = await execute_in_session(
                    db, "query", MemoryRequest(corpus=schema, query=question, intent="current")
                )
                detail = f"{len(response.current_memories)} memories"
            else:
                built, _ = await build_context(db, schema, question, budget_tokens=4096)
                detail = f"status={built.status}, {len(built.memories)} memories"
            elapsed = (time.perf_counter() - start) * 1000
            print(f"{adapter}\t{elapsed:.1f}\t{detail}")

        # The schema is dropped by the cleanup phase, so every adapter can be
        # measured against the same corpus.
    await engine.dispose()
    return 0


async def cleanup() -> int:
    from memory_bakeoff import _async_url
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{os.environ["COLD_SCHEMA"]}" CASCADE'))
        await conn.commit()
    await engine.dispose()
    return 0


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "C"
    if phase == "setup":
        raise SystemExit(asyncio.run(setup()))
    if phase == "cleanup":
        raise SystemExit(asyncio.run(cleanup()))
    raise SystemExit(asyncio.run(main(phase)))
