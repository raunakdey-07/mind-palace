"""Final product test: what does an agent actually receive?

Prints the rendered `context` text and the structured fields for a few
questions, so the pack can be read as a developer would read it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from memory_bakeoff import CACHE_ADR, DEPLOY, MIGRATION, STORAGE_V1, STORAGE_V2  # noqa: E402

CORPUS_FILES = (
    ("docs/storage.md", STORAGE_V1),
    ("docs/storage.md", STORAGE_V2),
    ("docs/migration.md", MIGRATION),
    ("adrs/adr-004-cache.md", CACHE_ADR),
    ("docs/deploy.md", DEPLOY),
)

QUESTIONS = [
    "What is the current database?",
    "When did the database change?",
    "What payroll provider does the company use?",
]


async def main() -> int:
    from memory_bakeoff import _async_url, _migrate
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from api.services.context_service import build_context
    from api.services.ingestion import IngestionService

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "product_" + uuid4().hex[:12]
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

        for question in QUESTIONS:
            async with AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db:
                pack, _ = await build_context(db, schema, question, budget_tokens=4096)
            print("=" * 78)
            print(f"Q: {question}")
            print(
                f"status={pack.status}  memories={len(pack.memories)} "
                f"conflicts={len(pack.conflicts)} changes={len(pack.changes)} "
                f"chunks={len(pack.chunks)} tokens~{pack.token_estimate}"
            )
            print("-" * 78)
            print(pack.context if pack.context else "(empty)")
            if pack.conflicts:
                for group in pack.conflicts:
                    print(f"  conflict[{group.key}]:")
                    for option in group.options:
                        quotes = [e.quote for e in option.evidence]
                        print(f"    - {option.value} [{option.status}] quotes={quotes}")
            print()

        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await conn.commit()
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
