"""M012-A step 1: what does the archive load actually cost in the database?

The profile said roughly 90% of a warm query is the socket wait inside
`memory._load`. Before changing anything, this captures what PostgreSQL is
actually doing: the plan, the buffers, rows scanned, rows returned, and the
size of the payload on the wire.

Run against a corpus built by m0117_scale, at a size given by --claims.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
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

from m0117_scale import Offline, build_documents  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

LOAD_SQL = """
    SELECT v.*, d.path,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('id', c.id, 'heading_path', c.heading_path)
                                   ORDER BY c.order_index)
            FROM memory_chunks c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id),
            '[]'::jsonb) AS chunks,
        COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id)
            FROM memory_claims c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id),
            '[]'::jsonb) AS claims,
        COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)
            FROM memory_evidence e WHERE e.corpus_id = v.corpus_id AND e.version_id = v.id),
            '[]'::jsonb) AS evidence
    FROM memory_versions v JOIN memory_documents d
      ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
    WHERE v.corpus_id = :c
    ORDER BY v.observed_at, d.path, v.version_number
"""


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


async def measure(url: str, claims: int) -> dict:
    """Build and measure in one transaction, so the corpus is the one measured."""
    from api.services.ingestion import IngestionService

    engine = create_async_engine(url, poolclass=NullPool)
    schema = "plan_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out: dict = {"claims_target": claims, "schema": schema, "corpus": corpus_id}
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

            for table in ("memory_versions", "memory_claims", "memory_evidence", "memory_chunks"):
                out[f"rows_{table}"] = int(
                    (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
                )
            if out["rows_memory_claims"] == 0:
                raise RuntimeError("the scale corpus archived nothing; the measurement is void")

            params = {"c": corpus_id}
            # Warm the plan cache so the timing reflects steady state.
            for _ in range(2):
                await conn.execute(text(LOAD_SQL), params)

            plan = await conn.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + LOAD_SQL), params
            )
            out["plan"] = plan.scalar()

            t0 = time.perf_counter()
            rows = (await conn.execute(text(LOAD_SQL), params)).fetchall()
            out["load_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            out["rows_returned"] = len(rows)
            out["bytes_returned"] = sum(len(str(r)) for r in rows)
            out["claims_returned"] = sum(len(r._mapping["claims"]) for r in rows)
        finally:
            await outer.rollback()
    await engine.dispose()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claims", type=int, default=1000)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m012-load-plan.json"))
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: set DATABASE_URL", file=sys.stderr)
        return 1
    url = url.replace("postgresql://", "postgresql+asyncpg://")
    result = asyncio.run(measure(url, args.claims))

    plan = result.pop("plan")[0]
    text_plan = (
        plan["Plan"] if isinstance(plan["Plan"], str) else json.dumps(plan["Plan"], indent=1)
    )
    print(f"claims in corpus      : {result['claims_returned']}")
    print(
        f"versions / claims     : {result['rows_memory_versions']} / {result['rows_memory_claims']}"
    )
    print(f"rows returned         : {result['rows_returned']}")
    print(f"payload returned      : {result['bytes_returned'] / 1024:.1f} KB")
    print(f"load latency          : {result['load_ms']} ms")
    print(f"planning time         : {plan['Planning Time']:.3f} ms")
    print(f"execution time        : {plan['Execution Time']:.3f} ms")
    print("\nplan:")
    print(text_plan)

    result["plan_text"] = text_plan
    result["plan_head"] = plan
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
