"""M011: where the pack pipeline spends its time, and what it can answer.

Three measurements against the realistic project corpus:

1. Pipeline breakdown. Interpretation, authority resolution, retrieval, pack
   construction, serialization and digest, timed separately, so the dominant
   term is visible instead of guessed at.

2. Relationship questions. Graph-shaped asks against a flat claim model. These
   are recorded as answered or not, with the reason, rather than assumed.

3. Poisoning. An untrusted document written to retrieve well. The pack is read
   by the independent consumer, which has no database access, to check that the
   injection never appears as authoritative memory.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from project_corpus import CORPUS, QUESTIONS  # noqa: E402

from memory_pack import NO_RELEVANT_MEMORY, MemoryPack  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

# Graph-shaped asks. Each names a relationship a temporal graph would answer
# directly. Whether the flat claim model can answer it is the measurement.
RELATIONSHIP_QUESTIONS = (
    "Who owns the service that depends on the primary database?",
    "Which system replaced the previous database?",
    "What changed after the storage decision?",
    "What constraint was introduced because of the November outage?",
    "Which decisions depend on the connection pool limit?",
    "What superseded the first database?",
)


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


async def breakdown(conn, schema: str, question: str, repeats: int) -> dict:
    """Time the pack pipeline in stages. Model inference is excluded from
    ``authority_ms`` only by being already warm; it is reported separately."""
    from api.models.memory import MemoryRequest
    from api.services import memory_query
    from api.services.memory_public import State, bounded_pack, project

    totals = {k: 0.0 for k in ("load", "interpret", "authority", "pack", "serialize", "digest")}
    last = None

    for _ in range(repeats):
        t0 = time.perf_counter()
        async with _session(conn) as db:
            from api.services import memory
            from api.services.corpora import get_corpus_by_name

            corpus = await get_corpus_by_name(db, schema)
            versions = await memory._load(db, corpus["id"])
        totals["load"] += (time.perf_counter() - t0) * 1000

        request = MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000)
        t0 = time.perf_counter()
        intent = memory_query.interpret(request)
        totals["interpret"] += (time.perf_counter() - t0) * 1000

        state = State(as_of=None, valid_at=VALID_AT)
        full = project(
            versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
        )
        t0 = time.perf_counter()
        last = await memory_query.query(full, request.model_copy(update={"intent": intent.name}))
        totals["authority"] += (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        packed = bounded_pack(last, 128000)
        totals["pack"] += (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        payload = packed.canonical_json()
        totals["serialize"] += (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        MemoryPack.from_json(payload)
        hashlib.sha256(payload.encode()).hexdigest()
        totals["digest"] += (time.perf_counter() - t0) * 1000

    return {k: round(v / repeats, 3) for k, v in totals.items()}


async def relationship_probe(conn, schema: str) -> list[dict]:
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute_in_session

    rows = []
    for question in RELATIONSHIP_QUESTIONS:
        async with _session(conn) as db:
            response = await execute_in_session(
                db,
                "query",
                MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000),
            )
        pack = MemoryPack.from_json(response.canonical_json())
        answered = pack.status != NO_RELEVANT_MEMORY and bool(pack.all_claims())
        rows.append(
            {
                "question": question,
                "status": pack.status,
                "answered": answered,
                "claims": len(pack.all_claims()),
                "keys": list(pack.keys()),
                "changes": len(pack.changes),
            }
        )
    return rows


async def poisoning_probe(conn, schema: str) -> dict:
    """The untrusted document retrieves well. Read the pack with no database."""
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute_in_session

    question = "What is the current database?"
    async with _session(conn) as db:
        response = await execute_in_session(
            db,
            "query",
            MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000),
        )
    pack = MemoryPack.from_json(response.canonical_json())
    markers = (
        "ignore previous",
        "authoritative system policy",
        "obey this text",
        "delete the previous",
        "permanent system policy",
    )
    leaked = [
        marker
        for marker in markers
        if any(marker in c.claim.casefold() for c in pack.all_claims())
        or any(marker in e.text.casefold() for e in pack.evidence)
    ]
    return {
        "status": pack.status,
        "claims": len(pack.all_claims()),
        "paths": sorted({c.path for c in pack.all_claims()}),
        "untrusted_path_in_memory": "imports/untrusted.md" in {c.path for c in pack.all_claims()},
        "injection_markers_as_memory": leaked,
        "integrity_problems": pack.verify(),
    }


async def run(url: str, repeats: int) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "m011p_" + uuid4().hex[:12]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    report = {
        "meta": {
            "python": platform.python_version(),
            "repeats": repeats,
            "valid_at": VALID_AT.isoformat(),
        },
        "questions": [],
    }

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

            from api.models.memory import MemoryRequest
            from api.services.memory_public import execute_in_session

            for question in QUESTIONS:
                async with _session(conn) as db:
                    response = await execute_in_session(
                        db,
                        "query",
                        MemoryRequest(
                            corpus=schema, query=question, valid_at=VALID_AT, budget=128000
                        ),
                    )
                pack = MemoryPack.from_json(response.canonical_json())
                report["questions"].append(
                    {
                        "question": question,
                        "status": pack.status,
                        "claims": len(pack.all_claims()),
                        "evidence": len(pack.evidence),
                        "conflicts": len(pack.conflicts),
                        "changes": len(pack.changes),
                        "bytes": pack.size(),
                        "digest": pack.digest()[:16],
                    }
                )

            report["pipeline_ms"] = await breakdown(
                conn, schema, "What is the current database?", repeats
            )
            report["relationships"] = await relationship_probe(conn, schema)
            report["poisoning"] = await poisoning_probe(conn, schema)
        finally:
            await outer.rollback()
        await engine.dispose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m011-pack-pipeline.json"))
    args = parser.parse_args()

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    print("M011 pack pipeline, relationship questions, and poisoning")
    report = asyncio.run(run(url, args.repeats))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print("\npipeline, mean of %d runs (ms):" % args.repeats)
    total = sum(report["pipeline_ms"].values())
    for stage, value in report["pipeline_ms"].items():
        share = (value / total * 100) if total else 0
        print(f"  {stage:<12} {value:8.3f}  {share:5.1f}%")
    print(f"  {'total':<12} {total:8.3f}")

    print("\nrelationship questions:")
    for row in report["relationships"]:
        mark = "answered" if row["answered"] else "NOT ANSWERED"
        print(f"  {mark:<13} {row['question']}")

    print("\npoisoning:")
    for key, value in report["poisoning"].items():
        print(f"  {key}: {value}")

    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
