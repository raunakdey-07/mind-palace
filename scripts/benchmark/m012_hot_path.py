"""M012 hot path: what does a warm query actually build, and what does it throw away?

The scale work found that at 10,000 claims the archive load is about 14% of a
warm query, so the database is no longer the bottleneck. This measures the
remaining stages separately and counts what each one materialises, so the next
change is aimed at a measured term rather than an assumed one.

Counted per query: SQL statements, version rows, claims, evidence and chunks
read from the database, Pydantic models constructed, relevance candidates
scored, and how many of those survive into the answer.
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
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0117_scale import PROBE, Offline, build_documents  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


async def one_warm(conn, schema: str, question: str, sql_count: list) -> dict:
    """One public query, with each stage timed and each stage's output counted."""
    from api.models.memory import MemoryRequest
    from api.services import memory
    from api.services.corpora import get_corpus_by_name
    from api.services.memory_public import State, project
    from api.services.memory_query import query as run_query

    out: dict = {}
    request = MemoryRequest(corpus=schema, query=question, budget=128000)

    t0 = time.perf_counter()
    async with AsyncSession(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as db:
        corpus = await get_corpus_by_name(db, schema)
        versions = await memory._load(db, corpus["id"], chunk_text=False)
    out["load"] = (time.perf_counter() - t0) * 1000
    out["versions"] = len(versions)
    out["claims_read"] = sum(len(v["claims"]) for v in versions)
    out["evidence_read"] = sum(len(v["evidence"]) for v in versions)
    out["chunks_read"] = sum(len(v["chunks"]) for v in versions)

    t0 = time.perf_counter()
    state = State(as_of=None, valid_at=None)
    full = project(
        versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
    )
    out["project"] = (time.perf_counter() - t0) * 1000
    out["claims_projected"] = (
        len(full.current_memories) + len(full.historical_memories) + len(full.uncertain_memories)
    )
    out["claims_total_in_pack"] = len(full.claims) if hasattr(full, "claims") else 0
    out["evidence_projected"] = len(full.evidence)
    out["sources_projected"] = len(full.sources)
    out["changes_projected"] = len(full.changes)
    out["conflicts_projected"] = len(full.conflicts)

    t0 = time.perf_counter()
    async with AsyncSession(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as db:
        answer = await run_query(full, request, db=db, corpus_id=corpus["id"])
    out["resolve"] = (time.perf_counter() - t0) * 1000
    out["answer_claims"] = (
        len(answer.current_memories)
        + len(answer.historical_memories)
        + len(answer.uncertain_memories)
    )
    out["answer_evidence"] = len(answer.evidence)
    out["answer_changes"] = len(answer.changes)
    out["answer_conflicts"] = len(answer.conflicts)
    out["sql_statements"] = len(sql_count)
    out["total"] = out["load"] + out["project"] + out["resolve"]
    return out


async def run(url: str, claims: int, repeats: int) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(url, poolclass=NullPool)
    schema = "hot_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    results: list[dict] = []

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
            from api.services.rehydrate import backfill_claim_embeddings

            async with AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db:
                async with db.begin():
                    await backfill_claim_embeddings(db, corpus_id)

            sql_count: list = []
            listener = lambda *a, **k: sql_count.append(1)  # noqa: E731
            event.listen(conn.sync_engine, "before_cursor_execute", listener)

            await one_warm(conn, schema, PROBE[0], sql_count)  # warm
            sql_count.clear()
            for _ in range(repeats):
                for question in PROBE:
                    results.append(await one_warm(conn, schema, question, sql_count))
            event.remove(conn.sync_engine, "before_cursor_execute", listener)
        finally:
            await outer.rollback()
    await engine.dispose()

    keys = ["load", "project", "resolve", "total"]
    counted = [
        "versions",
        "claims_read",
        "evidence_read",
        "chunks_read",
        "claims_projected",
        "evidence_projected",
        "sources_projected",
        "changes_projected",
        "conflicts_projected",
        "answer_claims",
        "answer_evidence",
        "answer_changes",
        "answer_conflicts",
        "sql_statements",
    ]
    out = {
        "claims_target": claims,
        "repeats": repeats,
        "counts": {k: results[0][k] for k in counted},
        "median_ms": {k: round(statistics.median([r[k] for r in results]), 2) for k in keys},
        "raw": results,
    }
    total = out["median_ms"]["total"] or 1
    out["share_pct"] = {
        k: round(out["median_ms"][k] / total * 100, 1) for k in ("load", "project", "resolve")
    }
    out["materialisation_ratio"] = {
        "claims_projected_per_answer_claim": round(
            out["counts"]["claims_projected"] / max(1, out["counts"]["answer_claims"]), 1
        ),
        "evidence_projected_per_answer_evidence": round(
            out["counts"]["evidence_projected"] / max(1, out["counts"]["answer_evidence"]), 1
        ),
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claims", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m012-hot-path.json"))
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: set DATABASE_URL", file=sys.stderr)
        return 1
    url = url.replace("postgresql://", "postgresql+asyncpg://")

    result = asyncio.run(run(url, args.claims, args.repeats))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")

    projected = result["counts"]["claims_projected"]
    print(f"corpus claims            : {projected} claims projected")
    print("\nper warm query:")
    for k in (
        "versions",
        "claims_read",
        "evidence_read",
        "chunks_read",
        "claims_projected",
        "evidence_projected",
        "sources_projected",
        "changes_projected",
        "conflicts_projected",
        "answer_claims",
        "answer_evidence",
        "answer_changes",
        "answer_conflicts",
        "sql_statements",
    ):
        print(f"  {k:<32} {result['counts'][k]}")
    print("\nmedian ms:")
    for k, v in result["median_ms"].items():
        share = result["share_pct"].get(k)
        suffix = f"  ({share}%)" if share is not None else ""
        print(f"  {k:<32} {v:>9.2f}{suffix}")
    print("\nmaterialisation ratio:")
    for k, v in result["materialisation_ratio"].items():
        print(f"  {k:<36} {v}")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
