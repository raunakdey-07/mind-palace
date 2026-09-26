"""M011: is the authoritative Memory Pack independent of the derived layer?

Four stages against one corpus, one question set, one pinned validity clock:

    1. capture   authoritative packs with L2 present
    2. destroy   delete every L2 row and the embedding index
    3. compare   packs must be byte-identical, with no rebuild at all
    4. rebuild   rehydrate L2 from the archive, compare again

Stage 3 is the strong claim: authority does not read the live projection, so
removing it must change nothing. Stage 4 proves the projection can be restored
from the archive alone.

Every pack is compared as canonical JSON, which is the format a consumer would
transport. The validity clock is pinned so the wall clock cannot leak in.
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
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from project_corpus import CORPUS, QUESTIONS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# Fixed clock. Without it every capture would differ in valid_at and no byte
# comparison would be meaningful.
VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _migrate(sync_conn):
    for path in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(sync_conn)):
            module.upgrade()


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def capture(conn, schema: str, questions) -> dict[str, str]:
    """Canonical JSON per question. This is the artefact being compared."""
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute_in_session

    out: dict[str, str] = {}
    for question in questions:
        async with _session(conn) as db:
            response = await execute_in_session(
                db,
                "query",
                MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000),
            )
        out[question] = response.canonical_json()
    return out


def digest(packs: dict[str, str]) -> str:
    """One digest over the whole set, so a single value can be compared."""
    blob = "\n".join(f"{q}\t{packs[q]}" for q in sorted(packs))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def l2_counts(conn, corpus_id: str) -> dict[str, int]:
    """L2 is documents, chunks, ingestion_manifest and the embeddings they carry.

    ``chunks`` has no corpus_id of its own; it belongs to a corpus through its
    document, so every count joins.
    """
    async with _session(conn) as db:
        result = await db.execute(
            text(
                "SELECT (SELECT count(*) FROM documents WHERE corpus_id = :c) AS documents, "
                "(SELECT count(*) FROM chunks ch JOIN documents d ON d.id = ch.doc_id "
                "   WHERE d.corpus_id = :c) AS chunks, "
                "(SELECT count(*) FROM ingestion_manifest WHERE corpus_id = :c) AS manifest, "
                "(SELECT count(*) FROM chunks ch JOIN documents d ON d.id = ch.doc_id "
                "   WHERE d.corpus_id = :c AND ch.embedding IS NOT NULL) AS embedded, "
                "(SELECT count(*) FROM memory_versions WHERE corpus_id = :c) AS versions, "
                "(SELECT count(*) FROM memory_claims WHERE corpus_id = :c) AS claims, "
                "(SELECT count(*) FROM memory_evidence WHERE corpus_id = :c) AS evidence"
            ),
            {"c": corpus_id},
        )
        row = result.mappings().one()
    return {k: int(v) for k, v in row.items()}


async def destroy_l2(conn, corpus_id: str) -> dict[str, int]:
    """Remove the derived projection entirely. The archive must be untouched."""
    before = await l2_counts(conn, corpus_id)
    async with _session(conn) as db:
        # The session joins the outer transaction with a savepoint, so a bare
        # execute() would roll back on close. Begin explicitly to commit it.
        async with db.begin():
            await db.execute(
                text("DELETE FROM ingestion_manifest WHERE corpus_id = :c"), {"c": corpus_id}
            )
            # documents cascades to chunks; the manifest is not a foreign-key parent.
            await db.execute(text("DELETE FROM documents WHERE corpus_id = :c"), {"c": corpus_id})
    return before


async def rebuild(conn, corpus_id: str) -> dict:
    """Rebuild L2 from the archive. The service does not commit, so this does."""
    from api.services.rehydrate import rehydrate_corpus

    async with _session(conn) as db:
        async with db.begin():
            return await rehydrate_corpus(db, corpus_id)


async def run(url: str) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "m011_" + uuid4().hex[:12]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    report = {"meta": {"commit": _git(), "python": platform.python_version()}, "questions": []}

    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrate)
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": schema},
            )
            service = IngestionService()
            for path, content in CORPUS:
                async with _session(conn) as db:
                    await service._ingest_content(db, content, path, corpus_id)

            before = await l2_counts(conn, corpus_id)
            baseline = await capture(conn, schema, QUESTIONS)

            await destroy_l2(conn, corpus_id)
            after_delete = await l2_counts(conn, corpus_id)
            without_l2 = await capture(conn, schema, QUESTIONS)

            stats = await rebuild(conn, corpus_id)
            after_rebuild = await l2_counts(conn, corpus_id)
            after_l2 = await capture(conn, schema, QUESTIONS)

            report["counts"] = {
                "with_l2": before,
                "after_destroy": after_delete,
                "after_rebuild": after_rebuild,
                "rebuild_stats": stats,
            }
            report["digests"] = {
                "with_l2": digest(baseline),
                "l2_destroyed": digest(without_l2),
                "l2_rebuilt": digest(after_l2),
            }

            for question in QUESTIONS:
                base = baseline[question]
                report["questions"].append(
                    {
                        "question": question,
                        "identical_without_l2": without_l2[question] == base,
                        "identical_after_rebuild": after_l2[question] == base,
                        "bytes": len(base),
                    }
                )
        finally:
            await outer.rollback()
        await engine.dispose()
    return report


def _git() -> str | None:
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m011-rebuildability.json"))
    args = parser.parse_args()

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    print("M011 rebuildability: destroy L2, compare authoritative packs")
    report = asyncio.run(run(url))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    counts = report["counts"]
    print()
    print(f"L2 with:      {counts['with_l2']}")
    print(f"L2 destroyed: {counts['after_destroy']}")
    print(f"L2 rebuilt:   {counts['after_rebuild']}")
    print()
    d = report["digests"]
    print(f"pack digest with L2       {d['with_l2']}")
    print(f"pack digest L2 destroyed  {d['l2_destroyed']}")
    print(f"pack digest L2 rebuilt    {d['l2_rebuilt']}")
    print()
    no_l2 = sum(1 for q in report["questions"] if q["identical_without_l2"])
    rebuilt = sum(1 for q in report["questions"] if q["identical_after_rebuild"])
    total = len(report["questions"])
    print(f"identical with L2 destroyed: {no_l2}/{total}")
    print(f"identical after rebuild    : {rebuilt}/{total}")
    for q in report["questions"]:
        if not (q["identical_without_l2"] and q["identical_after_rebuild"]):
            print(f"  DIFFERS: {q['question']}")
    print(f"written: {args.out}")
    return 0 if no_l2 == total and rebuilt == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
