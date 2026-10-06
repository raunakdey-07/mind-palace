"""M013.1: where does projection time actually go, and is it superlinear?

Read-only profiling. Builds corpora at several sizes, calls the real
``project`` on each, and reports cumulative time per function plus a log-log
exponent fitted over the sizes.

Two things this does that a raw profile does not:

* it profiles the SAME function at several corpus sizes, so a term that grows
  faster than the corpus is visible as a widening share rather than a big number;
* it times ``_query_result`` separately from the Pydantic construction in
  ``project``, because the two have different shapes: one is dict work, the other
  is validation.

Usage:
    python scripts/benchmark/m0131_profile_projection.py --sizes 5000,10000,25000
    python scripts/benchmark/m0131_profile_projection.py --sizes 10000 --profile
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import hashlib
import importlib.util
import io
import json
import math
import os
import pstats
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


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def build(conn, claims: int) -> tuple[str, str]:
    """Ingest a corpus and return (schema, corpus_id). Caller owns the transaction."""
    from m0117_scale import Offline, build_documents

    from api.services.ingestion import IngestionService

    schema = "prof_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
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
    return schema, corpus_id


async def measure(claims: int, profile: bool, repeats: int) -> dict:
    from api.models.memory import MemoryRequest
    from api.services import memory as memory_service
    from api.services.memory_public import State, project

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    out: dict = {"claims": claims}
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema, corpus_id = await build(conn, claims)
            async with _session(conn) as db:
                versions = await memory_service._load(
                    db, corpus_id, chunk_text=False, version_text=False
                )
            out["versions"] = len(versions)
            out["claims"] = sum(len(v["claims"]) for v in versions)
            request = MemoryRequest(
                corpus=schema, query="", path=None, valid_at=VALID_AT, budget=128000
            )
            state = State(as_of=None, valid_at=VALID_AT)

            # Warm once so the timed call is not dominated by first-touch imports.
            project(versions, request, "pack", state, None)

            t0 = time.perf_counter()
            for _ in range(repeats):
                resolved = memory_service._query_result(versions, "", VALID_AT)
            out["query_result_ms"] = (time.perf_counter() - t0) * 1000 / repeats

            t0 = time.perf_counter()
            for _ in range(repeats):
                full = project(versions, request, "pack", state, None)
            out["project_ms"] = (time.perf_counter() - t0) * 1000 / repeats

            # The gap between the two is the Pydantic/materialisation layer.
            out["model_layer_ms"] = out["project_ms"] - out["query_result_ms"]
            out["conflicts"] = len(resolved["conflicts"])
            out["active_claims"] = len(resolved["current_memories"])
            out["pack_claims"] = sum(
                len(getattr(full, f))
                for f in ("current_memories", "historical_memories", "uncertain_memories")
            )

            if profile:
                profiler = cProfile.Profile()
                profiler.enable()
                project(versions, request, "pack", state, None)
                profiler.disable()
                buffer = io.StringIO()
                stats = pstats.Stats(profiler, stream=buffer)
                stats.sort_stats("cumulative").print_stats(25)
                out["profile_top25"] = buffer.getvalue()
                by_call = pstats.Stats(profiler).sort_stats("tottime")
                buf2 = io.StringIO()
                by_call.stream = buf2
                by_call.print_stats(15)
                out["profile_tottime15"] = buf2.getvalue()
        finally:
            await outer.rollback()
    await engine.dispose()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="5000,10000,25000")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--profile", action="store_true", help="attach a cProfile dump")
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0131-projection.json"))
    args = parser.parse_args()

    sizes = [int(s) for s in args.sizes.split(",")]
    points = []
    for size in sizes:
        print(f"building {size} claims...", flush=True)
        point = asyncio.run(measure(size, args.profile, args.repeats))
        points.append(point)
        print(
            f"  claims={point['claims']:>6} project={point['project_ms']:>10.1f}ms "
            f"query_result={point['query_result_ms']:>9.1f}ms "
            f"model_layer={point['model_layer_ms']:>9.1f}ms "
            f"active={point['active_claims']:>6} conflicts={point['conflicts']:>5}",
            flush=True,
        )

    fits = {}
    for field in ("project_ms", "query_result_ms", "model_layer_ms"):
        pairs = [(p["claims"], p[field]) for p in points if p["claims"] > 1 and p[field] > 0]
        if len(pairs) >= 2:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            lx = [math.log(x) for x in xs]
            ly = [math.log(y) for y in ys]
            n = len(lx)
            mean_x = sum(lx) / n
            mean_y = sum(ly) / n
            num = sum((lx[i] - mean_x) * (ly[i] - mean_y) for i in range(n))
            den = sum((lx[i] - mean_x) ** 2 for i in range(n))
            fits[field] = {
                "exponent": round(num / den, 3) if den else None,
                "points": [[x, round(y, 1)] for x, y in pairs],
            }

    report = {
        "environment": {
            "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
            "loadavg_1m": round(os.getloadavg()[0], 2),
            "cores": os.cpu_count(),
            "repeats": args.repeats,
            "note": "project() is timed on the real projection path with an empty query",
        },
        "fits": fits,
        "points": points,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("\nlog-log exponents (1.0 = linear, 2.0 = quadratic):")
    for field, fit in fits.items():
        print(f"  {field:<18} {fit['exponent']}")
    for point in points:
        if "profile_top25" in point:
            print(f"\n--- cProfile cumulative, {point['claims']} claims ---")
            print(point["profile_top25"])
            print(f"--- cProfile tottime, {point['claims']} claims ---")
            print(point["profile_tottime15"])
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
