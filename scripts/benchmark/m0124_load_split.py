"""M012.4: separate database work from serialization and transfer.

The archive load was reported as non-monotonic: 1,000 versions in 811 ms and
5,000 versions in 311 ms. Two explanations were already ruled out, JIT and
run-to-run variance. This separates a third possibility.

PostgreSQL's `EXPLAIN (ANALYZE)` reports execution time for the plan but not
the cost of serialising results to the client. So for the same query:

    wall clock            = plan + scan + aggregate + sort + serialise + transfer
    EXPLAIN ANALYZE time  = plan + scan + aggregate + sort

The difference is the output path, and it scales with the width and count of
the rows returned. Each version row carries `v.*`, which includes the full
document content, plus three JSON aggregates. If the gap dominates, the load is
paying to ship the archive over the wire rather than to query it, and the
non-monotonicity is a property of row width, not of the plan.

This also records the sort method, spill and buffer activity so the sort node
can be characterised rather than guessed at.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import statistics
import sys
import time
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


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


def _flatten(node, depth=0, out=None):
    out = [] if out is None else out
    if not isinstance(node, dict):
        return out
    out.append(
        {
            "depth": depth,
            "type": node.get("Node Type"),
            "rows": node.get("Actual Rows"),
            "loops": node.get("Actual Loops"),
            "time_ms": node.get("Actual Total Time"),
            "width": node.get("Plan Width"),
            "sort_method": node.get("Sort Method"),
            "sort_space": node.get("Sort Space Used"),
            "sort_space_type": node.get("Sort Space Type"),
            "temp_blocks_written": node.get("Temp Blocks Written"),
            "hit_blocks": node.get("Shared Hit Blocks"),
        }
    )
    for child in node.get("Plans", []) or []:
        _flatten(child, depth + 1, out)
    return out


async def measure(claims: int, repeats: int) -> dict:
    from m0117_scale import Offline, build_documents

    from api.services.ingestion import IngestionService
    from api.services.memory import _load

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "al_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out: dict = {"claims": claims}

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

            # Wall clock of the real loader, as the product calls it.
            wall = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                async with _session(conn) as db:
                    rows = await _load(db, corpus_id, chunk_text=False)
                wall.append((time.perf_counter() - t0) * 1000)

            # Database-side only: EXPLAIN ANALYZE reports plan plus execution
            # and excludes serialising rows to the client.
            sql = text(
                "EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT JSON) SELECT v.*, d.path, "
                "COALESCE((SELECT jsonb_agg(jsonb_build_object('id', c.id, "
                "'heading_path', c.heading_path) ORDER BY c.order_index) FROM memory_chunks c "
                "WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id), '[]'::jsonb) AS chunks, "
                "COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id) "
                "FROM memory_claims c "
                "WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id), '[]'::jsonb) AS claims, "
                "COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM memory_evidence e "
                "WHERE e.corpus_id = v.corpus_id AND e.version_id = v.id), "
                "'[]'::jsonb) AS evidence "
                "FROM memory_versions v JOIN memory_documents d ON d.corpus_id = v.corpus_id "
                "AND d.id = v.memory_document_id WHERE v.corpus_id = :c "
                "ORDER BY v.observed_at, d.path, v.version_number"
            )
            explained = []
            for _ in range(3):
                plan = (await conn.execute(sql, {"c": corpus_id})).scalar()[0]
                explained.append(plan)

            out["rows_returned"] = len(rows)
            out["wall_p50_ms"] = round(statistics.median(wall), 1)
            out["wall_samples_ms"] = [round(w, 1) for w in wall]
            out["db_exec_p50_ms"] = round(
                statistics.median(p["Execution Time"] for p in explained), 1
            )
            out["db_planning_p50_ms"] = round(
                statistics.median(p["Planning Time"] for p in explained), 2
            )
            out["serialisation_p50_ms"] = round(out["wall_p50_ms"] - out["db_exec_p50_ms"], 1)
            out["serialisation_share_pct"] = round(
                out["serialisation_p50_ms"] / out["wall_p50_ms"] * 100, 1
            )
            out["plan_nodes"] = _flatten(explained[0]["Plan"])
        finally:
            await outer.rollback()
    await engine.dispose()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="1000,5000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0124-load-split.json"))
    args = parser.parse_args()

    report = {
        "note": "EXPLAIN ANALYZE excludes output serialisation and network "
        "transfer; the difference from wall clock is the output path",
        "points": [],
    }
    print(
        f"{'claims':>7} {'wall p50':>10} {'db exec':>10} {'planning':>9} "
        f"{'serialise':>10} {'share':>7} {'rows':>7}"
    )
    for size in [int(s) for s in args.sizes.split(",")]:
        point = asyncio.run(measure(size, args.repeats))
        report["points"].append(point)
        print(
            f"{point['claims']:>7} {point['wall_p50_ms']:>10.1f} {point['db_exec_p50_ms']:>10.1f} "
            f"{point['db_planning_p50_ms']:>9.2f} {point['serialisation_p50_ms']:>10.1f} "
            f"{point['serialisation_share_pct']:>6.1f}% {point['rows_returned']:>7}"
        )

    print("\nplan detail:")
    for point in report["points"]:
        print(f"  --- {point['claims']} claims")
        for n in point["plan_nodes"][:5]:
            extra = ""
            if n["sort_method"]:
                extra = (
                    f" sort={n['sort_method']} space={n['sort_space']} "
                    f"({n['sort_space_type']}) temp_written={n['temp_blocks_written']}"
                )
            print(
                f"    {'  ' * n['depth']}{str(n['type']):<18} rows={n['rows']} "
                f"loops={n['loops']} width={n['width']} time={n['time_ms']}{extra}"
            )

    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
