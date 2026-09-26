# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Degradation probe: which surfaces still answer when the embedding model is gone?

The embedder keeps one resident model per process, so renaming the environment
variable mid-process does not evict it. This script therefore runs in two phases
against a real committed schema:

    setup   ingest the corpus with the working model, commit, print the schema name
    probe   a fresh process with a model name that cannot load, measure every
            public memory surface, then drop the schema

The question is whether the product degrades the way it claims:

    full -> semantic+lexical+authoritative -> lexical+authoritative -> authoritative only

Usage:
    DATABASE_URL=... venvmp/bin/python scripts/benchmark/degradation_probe.py setup
    DATABASE_URL=... venvmp/bin/python scripts/benchmark/degradation_probe.py probe
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
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
BROKEN_MODEL = "no-such-model/mini-nonexistent"


def _url() -> str:
    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL")
    return url


def _connect():
    from memory_bakeoff import _async_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    return create_async_engine(_async_url(_url()), poolclass=NullPool)


async def setup() -> int:
    from memory_bakeoff import _migrate
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from api.services.ingestion import IngestionService

    engine = _connect()
    schema = "degrade_" + uuid4().hex[:12]
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


async def probe(schema: str) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from api.models.memory import MemoryRequest
    from api.services.context_packer import pack_context
    from api.services.context_service import build_context
    from api.services.corpora import get_corpus_by_name
    from api.services.embedder import SEMANTIC_DEPENDENCY_ERRORS, Embedder
    from api.services.memory_public import execute_in_session
    from api.services.retrieval import RetrievalService

    question = CASES[0]["question"]
    engine = _connect()
    results = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'SET search_path TO "{schema}", public'))
            await conn.commit()

            async def run(name, factory):
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    try:
                        out = await factory(db)
                    except Exception as exc:  # noqa: BLE001 - this is the measurement
                        kind = (
                            "SEMANTIC"
                            if isinstance(exc, SEMANTIC_DEPENDENCY_ERRORS)
                            else type(exc).__name__
                        )
                        results[name] = False
                        print(f"  {name:<26} FAIL  [{kind}] {str(exc).splitlines()[0][:70]}")
                        return
                results[name] = True
                print(f"  {name:<26} OK    {out}")

            def summarize(r):
                return (
                    f"{len(r.current_memories)} current, "
                    f"{len(r.historical_memories)} historical, "
                    f"{len(r.changes)} change, "
                    f"{len(r.conflicts)} conflict, "
                    f"{len(r.evidence)} evidence"
                )

            async def current(db):
                return summarize(
                    await execute_in_session(db, "current", MemoryRequest(corpus=schema, query=""))
                )

            async def history(db):
                return summarize(
                    await execute_in_session(db, "history", MemoryRequest(corpus=schema, query=""))
                )

            async def changes(db):
                return summarize(
                    await execute_in_session(db, "changes", MemoryRequest(corpus=schema, query=""))
                )

            async def authoritative_query(db):
                return summarize(
                    await execute_in_session(
                        db, "query", MemoryRequest(corpus=schema, query=question)
                    )
                )

            async def product_context(db):
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
                return f"{len(pack.chunks)} chunks, {pack.token_estimate} tokens"

            async def unified_context(db):
                pack, _ = await build_context(db, schema, question, budget_tokens=4096)
                return (
                    f"status={pack.status}, {len(pack.memories)} memories, "
                    f"{len(pack.conflicts)} conflicts, {len(pack.chunks)} chunks"
                )

            print(f"model requested: {os.getenv('EMBEDDING_MODEL')}")
            print(f"question       : {question}\n")
            print("authoritative projections (no model expected):")
            for name, factory in (
                ("current", current),
                ("history", history),
                ("changes", changes),
            ):
                await run(name, factory)
            print("\nquestion-answering surfaces:")
            await run("authoritative query", authoritative_query)
            await run("context() [old product]", product_context)
            await run("context() [unified]", unified_context)

        survived = sum(results.values())
        print(f"\n{survived}/{len(results)} surfaces still answered without a model")
    finally:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.commit()
        await engine.dispose()
    return 0


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if phase == "setup":
        return asyncio.run(setup())
    if phase == "probe":
        schema = os.environ["DEGRADE_SCHEMA"]
        os.environ["EMBEDDING_MODEL"] = BROKEN_MODEL
        os.environ["HF_HUB_OFFLINE"] = "1"
        return asyncio.run(probe(schema))
    raise SystemExit(f"unknown phase '{phase}'")


if __name__ == "__main__":
    raise SystemExit(main())
