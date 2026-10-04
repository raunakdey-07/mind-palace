"""Step 3 -- does ANALYZE remove the archive-load cliff at every size?

For each N: fresh schema -> migrate -> ingest N claims with the Offline
embedder -> assert the fresh-install condition (reltuples = -1, never
analysed) -> run the PRODUCTION narrow _load query under EXPLAIN
(ANALYZE, BUFFERS, FORMAT JSON) -> ANALYZE the four tables -> run the SAME
query again -> assert the rows are byte-identical across the ANALYZE.

Nothing here outlives the script: every schema is created inside one
transaction that is rolled back. ANALYZE is the only catalog write, and it
dies with the rollback too.
"""

from __future__ import annotations

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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path("/mnt/Warehouse/projects/Mind Palace")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))

URL = os.environ.get("DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")
URL = URL.replace("postgresql://", "postgresql+asyncpg://")

SIZES = [250, 500, 750, 1000, 1500, 2000, 3000, 5000]
REPS = 3


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


class _Capture:
    def __init__(self):
        self.sql = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _Empty()


class _Empty:
    def mappings(self):
        return []


async def capture_production_sql():
    """The exact statement api.services.memory._load emits for the narrow projection."""
    from api.services import memory

    cap = _Capture()
    await memory._load(cap, "0" * 64, chunk_text=False, version_text=False)
    return cap.sql


def walk(node):
    yield node
    for child in node.get("Plans") or []:
        yield from walk(child)


def scan_info(plan, rel):
    fallback = None
    for nd in walk(plan):
        if nd.get("Relation Name") == rel:
            bitmap = next(
                (c.get("Index Name") for c in walk(nd) if c.get("Node Type") == "Bitmap Index Scan"),
                None,
            )
            info = {
                "node": nd.get("Node Type"),
                "index": nd.get("Index Name") or bitmap,
                "index_cond": nd.get("Index Cond"),
                "filter": nd.get("Filter"),
                "rows_removed_by_filter": nd.get("Rows Removed by Filter"),
                "shared_hit": nd.get("Shared Hit Blocks"),
                "plan_rows": nd.get("Plan Rows"),
                "actual_rows": nd.get("Actual Rows"),
                "loops": nd.get("Actual Loops"),
            }
            if info["index"]:
                return info
            fallback = fallback or info
    return fallback


def canonical(value):
    import datetime as _dt
    import decimal as _dec

    if value is None:
        return "\x00NULL"
    if isinstance(value, str):
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dec.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return repr(value)


async def fetch_rows(conn, params, sql):
    res = await conn.execute(text(sql), params)
    cols = list(res.keys())
    rows = res.fetchall()
    h = hashlib.sha256()
    for row in rows:
        for name, val in zip(cols, row):
            h.update(repr((name, canonical(val))).encode())
            h.update(b"\x1f")
        h.update(b"\x1e")
    return {"n_rows": len(rows), "cols": cols, "sha256": h.hexdigest()}


async def stats(conn):
    """reltuples / relpages / last_analyze, read on the SAME connection."""
    rows = (
        await conn.execute(
            text(
                "SELECT c.relname, c.reltuples, c.relpages, s.last_analyze, s.last_autoanalyze,"
                " s.n_live_tup, s.autoanalyze_count, s.analyze_count"
                " FROM pg_stat_user_tables s JOIN pg_class c ON c.oid = s.relid"
                " WHERE c.relnamespace = current_schema()::regnamespace"
                " AND c.relname IN ('memory_claims','memory_evidence','memory_chunks',"
                " 'memory_versions','memory_documents') ORDER BY c.relname"
            )
        )
    ).fetchall()
    return {
        r[0]: {
            "reltuples": r[1],
            "relpages": r[2],
            "last_analyze": None if r[3] is None else str(r[3]),
            "last_autoanalyze": None if r[4] is None else str(r[4]),
            "n_live_tup": r[5],
            "autoanalyze_count": r[6],
            "analyze_count": r[7],
        }
        for r in rows
    }


async def explain(conn, sql, params):
    res = await conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), params)
    doc = res.scalar()
    if isinstance(doc, str):
        doc = json.loads(doc)
    return doc[0] if isinstance(doc, list) else doc


async def measure(conn, sql, params, label):
    reps = []
    for _ in range(REPS):
        doc = await explain(conn, sql, params)
        root = doc["Plan"]
        ci = scan_info(root, "memory_claims") or {}
        ei = scan_info(root, "memory_evidence") or {}
        reps.append(
            {
                "shared_hit": root.get("Shared Hit Blocks"),
                "shared_read": root.get("Shared Read Blocks"),
                "exec_ms": doc.get("Execution Time"),
                "plan_ms": doc.get("Planning Time"),
                "claims_index": ci.get("index"),
                "claims_rows_removed_by_filter": ci.get("rows_removed_by_filter"),
                "claims_index_cond": ci.get("index_cond"),
                "claims_filter": ci.get("filter"),
                "evidence_index": ei.get("index"),
                "evidence_rows_removed_by_filter": ei.get("rows_removed_by_filter"),
                "evidence_index_cond": ei.get("index_cond"),
                "evidence_filter": ei.get("filter"),
                "root_est_rows": root.get("Plan Rows"),
                "root_actual_rows": root.get("Actual Rows"),
                "jit": doc.get("JIT", {}),
            }
        )
    hits = [r["shared_hit"] for r in reps]
    assert len(set(hits)) == 1, f"{label}: shared hit varied across reps: {hits}"
    assert len(set(r["claims_index"] for r in reps)) == 1, f"{label}: claims index varied"
    assert len(set(r["evidence_index"] for r in reps)) == 1, f"{label}: evidence index varied"
    execs = [r["exec_ms"] for r in reps]
    return {
        "label": label,
        "reps": reps,
        "shared_hit": reps[0]["shared_hit"],
        "shared_hit_reps": hits,
        "exec_ms": round(statistics.median(execs), 2),
        "exec_ms_reps": [round(e, 1) for e in execs],
        "claims_index": reps[0]["claims_index"],
        "claims_rows_removed_by_filter": reps[0]["claims_rows_removed_by_filter"],
        "evidence_index": reps[0]["evidence_index"],
        "evidence_rows_removed_by_filter": reps[0]["evidence_rows_removed_by_filter"],
        "root_est_rows": reps[0]["root_est_rows"],
        "root_actual_rows": reps[0]["root_actual_rows"],
        "jit": reps[0]["jit"],
    }


async def run_size(n):
    from api.services.ingestion import IngestionService
    from m0117_scale import Offline, build_documents

    sql = await capture_production_sql()
    engine = create_async_engine(URL, poolclass=NullPool)
    schema = "anx_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out = {"n": n, "schema": schema}
    t0 = time.perf_counter()
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
            documents = build_documents(n)
            service = IngestionService()
            service.embedder = Offline()
            for i, (path, content) in enumerate(documents, 1):
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    await service._ingest_content(db, content, path, corpus_id)
                if i % 500 == 0:
                    print(f"[N={n}]   ingested {i}/{len(documents)} docs", flush=True)

            counts = (
                await conn.execute(
                    text(
                        "SELECT (SELECT count(*) FROM memory_claims WHERE corpus_id=:c) AS claims,"
                        " (SELECT count(*) FROM memory_evidence WHERE corpus_id=:c) AS evidence,"
                        " (SELECT count(*) FROM memory_chunks WHERE corpus_id=:c) AS chunks,"
                        " (SELECT count(*) FROM memory_versions WHERE corpus_id=:c) AS versions,"
                        " (SELECT count(*) FROM memory_documents WHERE corpus_id=:c) AS documents"
                    ),
                    {"c": corpus_id},
                )
            ).mappings().one()
            out["rows"] = {k: int(v) for k, v in counts.items()}

            await conn.execute(text("SET plan_cache_mode = force_custom_plan"))
            out["plan_cache_mode"] = (await conn.execute(text("SHOW plan_cache_mode"))).scalar()

            pre = await stats(conn)
            out["stats_pre"] = pre
            assert pre["memory_claims"]["reltuples"] == -1, (
                f"N={n} claims reltuples={pre['memory_claims']['reltuples']}, expected -1")
            assert pre["memory_evidence"]["reltuples"] == -1, (
                f"N={n} evidence reltuples={pre['memory_evidence']['reltuples']}, expected -1")
            assert pre["memory_claims"]["last_analyze"] is None, f"N={n} claims last_analyze set"
            assert pre["memory_evidence"]["last_analyze"] is None, f"N={n} evidence last_analyze set"
            out["fresh_install_asserted"] = True
            print(
                f"[N={n}] fresh-install ASSERTED: claims reltuples={pre['memory_claims']['reltuples']}"
                f" evidence reltuples={pre['memory_evidence']['reltuples']} last_analyze=NULL"
                f" rows={out['rows']}", flush=True)

            params = {"c": corpus_id, "path": None, "at": None}
            before = await measure(conn, sql, params, "pre-ANALYZE")
            got_before = await fetch_rows(conn, params, sql)
            print(
                f"[N={n}] PRE  hit={before['shared_hit']} exec={before['exec_ms']}ms"
                f" claims_idx={before['claims_index']} rf={before['claims_rows_removed_by_filter']}"
                f" | ev_idx={before['evidence_index']} rf={before['evidence_rows_removed_by_filter']}"
                f" | est_rows={before['root_est_rows']}", flush=True)

            for tbl in ("memory_claims", "memory_evidence", "memory_versions", "memory_documents"):
                await conn.execute(text(f"ANALYZE {tbl}"))
            out["stats_post"] = await stats(conn)

            after = await measure(conn, sql, params, "post-ANALYZE")
            got_after = await fetch_rows(conn, params, sql)
            print(
                f"[N={n}] POST hit={after['shared_hit']} exec={after['exec_ms']}ms"
                f" claims_idx={after['claims_index']} rf={after['claims_rows_removed_by_filter']}"
                f" | ev_idx={after['evidence_index']} rf={after['evidence_rows_removed_by_filter']}"
                f" | est_rows={after['root_est_rows']}", flush=True)

            identical = (
                got_before["sha256"] == got_after["sha256"]
                and got_before["n_rows"] == got_after["n_rows"]
                and got_before["cols"] == got_after["cols"]
            )
            out["before"] = before
            out["after"] = after
            out["rows_sha256_before"] = got_before["sha256"]
            out["rows_sha256_after"] = got_after["sha256"]
            out["rows_identical_across_analyze"] = identical
            out["hit_ratio"] = (round(before["shared_hit"] / after["shared_hit"], 2)
                                if after["shared_hit"] else None)
            print(
                f"[N={n}] rows byte-identical across ANALYZE: {identical}"
                f" (sha={got_before['sha256'][:16]} n={got_before['n_rows']})"
                f" ratio={out['hit_ratio']}x", flush=True)
            assert identical, f"N={n}: ANALYZE changed the rows returned by _load"
        finally:
            await outer.rollback()
    await engine.dispose()
    out["seconds"] = round(time.perf_counter() - t0, 1)
    print(f"[N={n}] done in {out['seconds']}s (schema rolled back)", flush=True)
    return out


async def main():
    results = []
    for n in SIZES:
        results.append(await run_size(n))
        Path("/tmp/mp-analyze/matrix.json").write_text(json.dumps(results, indent=2) + "\n")
    print("written: /tmp/mp-analyze/matrix.json", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
