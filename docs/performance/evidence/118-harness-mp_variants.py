"""Scratch harness: can a QUERY RESTRUCTURE remove the _load plan cliff without ANALYZE?

Throwaway schema, created inside a transaction that is rolled back at the end.
Runs migrations from the repo, ingests with m0117_scale.build_documents +
IngestionService + the deterministic Offline embedder, then executes V0..V6
under EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON).

No ANALYZE. No new index. No new migration. No DDL outside the throwaway schema.
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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path("/mnt/Warehouse/projects/Mind Palace")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))

URL = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
EV = ROOT / "docs" / "performance" / "evidence"

# --------------------------------------------------------------------------
# V0 -- byte-for-byte the statement api/services/memory.py::_load emits for
# chunk_text=False, version_text=False.  Asserted against the live source at
# run time by capture_production_sql(); the sweep aborts if they differ.
# --------------------------------------------------------------------------
V0_SQL = """
        SELECT v.corpus_id, v.memory_document_id, v.id, v.predecessor_id, v.version_number, v.document_id, v.event, v.observed_at, v.fingerprint, d.path,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id', c.id, 'heading_path', c.heading_path) ORDER BY c.order_index)
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
        WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
          AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
        ORDER BY v.observed_at, d.path, v.version_number
    """

# V1 -- LEFT JOIN LATERAL.  WHY: the subquery plan nodes become a join tree, so
# the planner prices the whole nest at once instead of pricing a per-loop scan.
V1_SQL = """        SELECT v.corpus_id, v.memory_document_id, v.id, v.predecessor_id, v.version_number, v.document_id, v.event, v.observed_at, v.fingerprint, d.path,
            COALESCE(ch.agg, '[]'::jsonb) AS chunks,
            COALESCE(cl.agg, '[]'::jsonb) AS claims,
            COALESCE(ev.agg, '[]'::jsonb) AS evidence
        FROM memory_versions v JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(jsonb_build_object('id', c.id, 'heading_path', c.heading_path) ORDER BY c.order_index) AS agg
            FROM memory_chunks c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id) ch ON true
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id) AS agg
            FROM memory_claims c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id) cl ON true
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) AS agg
            FROM memory_evidence e WHERE e.corpus_id = v.corpus_id AND e.version_id = v.id) ev ON true
        WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
          AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
        ORDER BY v.observed_at, d.path, v.version_number
    """

# V2 -- WITH ... MATERIALIZED, one pre-aggregate per child table, joined on
# (corpus_id, version_id).  WHY: materialising turns N per-loop lookups into one
# hash aggregate + one hash join, so the bad index is never considered at all.
_CTE_HEAD = """        WITH ch AS MATERIALIZED (
            SELECT c.corpus_id, c.version_id,
                   jsonb_agg(jsonb_build_object('id', c.id, 'heading_path', c.heading_path) ORDER BY c.order_index) AS agg
            FROM memory_chunks c WHERE c.corpus_id = :c GROUP BY c.corpus_id, c.version_id),
             cl AS MATERIALIZED (
            SELECT c.corpus_id, c.version_id, jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id) AS agg
            FROM memory_claims c WHERE c.corpus_id = :c GROUP BY c.corpus_id, c.version_id),
             ev AS MATERIALIZED (
            SELECT e.corpus_id, e.version_id, jsonb_agg(to_jsonb(e) ORDER BY e.id) AS agg
            FROM memory_evidence e WHERE e.corpus_id = :c GROUP BY e.corpus_id, e.version_id)
        SELECT v.corpus_id, v.memory_document_id, v.id, v.predecessor_id, v.version_number, v.document_id, v.event, v.observed_at, v.fingerprint, d.path,
            COALESCE(ch.agg, '[]'::jsonb) AS chunks,
            COALESCE(cl.agg, '[]'::jsonb) AS claims,
            COALESCE(ev.agg, '[]'::jsonb) AS evidence
        FROM memory_versions v JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        LEFT JOIN ch ON ch.corpus_id = v.corpus_id AND ch.version_id = v.id
        LEFT JOIN cl ON cl.corpus_id = v.corpus_id AND cl.version_id = v.id
        LEFT JOIN ev ON ev.corpus_id = v.corpus_id AND ev.version_id = v.id
        WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
          AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
        ORDER BY v.observed_at, d.path, v.version_number
    """
V2_SQL = _CTE_HEAD

# V3 -- identical correlated subqueries, predicate order swapped
# (version_id first) and corpus_id restated on both child scans.
# WHY: clause order is supposed to be irrelevant to the planner; if it is not,
# the production SQL has a latent ordering dependence.
V3_SQL = """        SELECT v.corpus_id, v.memory_document_id, v.id, v.predecessor_id, v.version_number, v.document_id, v.event, v.observed_at, v.fingerprint, d.path,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id', c.id, 'heading_path', c.heading_path) ORDER BY c.order_index)
                FROM memory_chunks c WHERE c.version_id = v.id AND c.corpus_id = v.corpus_id),
                '[]'::jsonb) AS chunks,
            COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id)
                FROM memory_claims c WHERE c.version_id = v.id AND c.corpus_id = v.corpus_id),
                '[]'::jsonb) AS claims,
            COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)
                FROM memory_evidence e WHERE e.version_id = v.id AND e.corpus_id = v.corpus_id),
                '[]'::jsonb) AS evidence
        FROM memory_versions v JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
          AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
        ORDER BY v.observed_at, d.path, v.version_number
    """

# V4 -- same shape as V2 but as inline derived tables instead of CTEs.
# WHY: separates "pre-aggregation" from "CTE materialisation"; if V2 wins and
# V4 does not, the win came from materialisation, not from the grouping.
V4_SQL = """        SELECT v.corpus_id, v.memory_document_id, v.id, v.predecessor_id, v.version_number, v.document_id, v.event, v.observed_at, v.fingerprint, d.path,
            COALESCE(ch.agg, '[]'::jsonb) AS chunks,
            COALESCE(cl.agg, '[]'::jsonb) AS claims,
            COALESCE(ev.agg, '[]'::jsonb) AS evidence
        FROM memory_versions v JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        LEFT JOIN (SELECT c.corpus_id, c.version_id,
                          jsonb_agg(jsonb_build_object('id', c.id, 'heading_path', c.heading_path) ORDER BY c.order_index) AS agg
                   FROM memory_chunks c WHERE c.corpus_id = :c GROUP BY c.corpus_id, c.version_id) ch
               ON ch.corpus_id = v.corpus_id AND ch.version_id = v.id
        LEFT JOIN (SELECT c.corpus_id, c.version_id, jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id) AS agg
                   FROM memory_claims c WHERE c.corpus_id = :c GROUP BY c.corpus_id, c.version_id) cl
               ON cl.corpus_id = v.corpus_id AND cl.version_id = v.id
        LEFT JOIN (SELECT e.corpus_id, e.version_id, jsonb_agg(to_jsonb(e) ORDER BY e.id) AS agg
                   FROM memory_evidence e WHERE e.corpus_id = :c GROUP BY e.corpus_id, e.version_id) ev
               ON ev.corpus_id = v.corpus_id AND ev.version_id = v.id
        WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
          AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
        ORDER BY v.observed_at, d.path, v.version_number
    """

# V7 -- one-token change: replace the correlated `c.corpus_id = v.corpus_id`
# with the bound parameter `:c`, which is provably equal to it because the
# WHERE clause already fixes v.corpus_id = :c.  WHY: a constant equality gives
# the planner a predicate it can estimate even with no column statistics.
V7_SQL = """        SELECT v.corpus_id, v.memory_document_id, v.id, v.predecessor_id, v.version_number, v.document_id, v.event, v.observed_at, v.fingerprint, d.path,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id', c.id, 'heading_path', c.heading_path) ORDER BY c.order_index)
                FROM memory_chunks c WHERE c.corpus_id = :c AND c.version_id = v.id),
                '[]'::jsonb) AS chunks,
            COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id)
                FROM memory_claims c WHERE c.corpus_id = :c AND c.version_id = v.id),
                '[]'::jsonb) AS claims,
            COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)
                FROM memory_evidence e WHERE e.corpus_id = :c AND e.version_id = v.id),
                '[]'::jsonb) AS evidence
        FROM memory_versions v JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
          AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
        ORDER BY v.observed_at, d.path, v.version_number
    """

RESET = (
    "SET LOCAL default_statistics_target = 100",
    "SET LOCAL enable_indexscan = on",
    "SET LOCAL enable_seqscan = on",
    "SET LOCAL enable_bitmapscan = on",
    "SET LOCAL random_page_cost = 4",
)

VARIANTS = [
    ("V0", V0_SQL, (), (), "BASELINE: production _load narrow SQL, verbatim"),
    ("V1", V1_SQL, (), (), "LEFT JOIN LATERAL for the three aggregates"),
    ("V2", V2_SQL, (), (), "WITH ... MATERIALIZED pre-aggregate, joined on (corpus_id, version_id)"),
    ("V3", V3_SQL, (), (), "correlated subqueries, version_id predicate written first"),
    ("V4", V4_SQL, (), (), "inline pre-grouped derived tables instead of CTEs"),
    ("V5", V0_SQL, (), ("SET LOCAL default_statistics_target = 1000",),
     "V0 under default_statistics_target=1000 (session setting only, collects NO statistics)"),
    ("V6a", V0_SQL, (), ("SET LOCAL enable_indexscan = off",),
     "DIAGNOSTIC CONTROL: force non-index-scan paths, to see if the planner can be steered"),
    ("V6b", V0_SQL, (), ("SET LOCAL enable_seqscan = off", "SET LOCAL enable_bitmapscan = off"),
     "DIAGNOSTIC CONTROL: force a plain index scan, to price the composite index directly"),
    ("V6c", V0_SQL, (), ("SET LOCAL random_page_cost = 1.1",),
     "DIAGNOSTIC CONTROL: SSD-like random_page_cost, to see if cost model alone flips the plan"),
    ("V7", V7_SQL, (), (), "corpus_id bound to the :c parameter instead of v.corpus_id"),
]

PARAMS_KEYS = ("c", "path", "at")
DUMP_PLANS_FOR = set()


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
    """Minimal session stand-in that records the statement _load emits."""

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
    from api.services import memory

    cap = _Capture()
    await memory._load(cap, "0" * 64, chunk_text=False, version_text=False)
    return cap.sql, cap.params


# ---------------------------------------------------------------- plan tools
def walk(node):
    yield node
    for child in node.get("Plans") or []:
        yield from walk(child)


def scan_info(plan, rel):
    """First index-bearing scan node on `rel`; falls back to the first node."""
    fallback = None
    for nd in walk(plan):
        if nd.get("Relation Name") == rel:
            # A Bitmap Heap Scan carries no Index Name; the Bitmap Index Scan
            # child does. Report the index that was actually used.
            bitmap = next((c.get("Index Name") for c in walk(nd)
                           if c.get("Node Type") == "Bitmap Index Scan"), None)
            info = {
                "node": nd.get("Node Type"),
                "index": nd.get("Index Name") or bitmap,
                "index_cond": nd.get("Index Cond"),
                "filter": nd.get("Filter"),
                "rows_removed_by_filter": nd.get("Rows Removed by Filter"),
                "loops": nd.get("Actual Loops"),
                "shared_hit": nd.get("Shared Hit Blocks"),
                "rows": nd.get("Actual Rows"),
                "plan_rows": nd.get("Plan Rows"),
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
    return {"n_rows": len(rows), "cols": cols, "sha256": h.hexdigest(), "rows": rows}


def as_json(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw)[0]


# ---------------------------------------------------------------- main sweep
async def run_size(n: int, reps: int, dump_v0: bool) -> dict:
    from api.services.ingestion import IngestionService
    from m0117_scale import Offline, build_documents

    prod_sql, prod_params = await capture_production_sql()
    fidelity = {"match": prod_sql == V0_SQL, "prod_len": len(prod_sql), "v0_len": len(V0_SQL)}
    if not fidelity["match"]:
        fidelity["prod_repr"] = repr(prod_sql)
        fidelity["v0_repr"] = repr(V0_SQL)
        fidelity["diff"] = [
            (i, a, b)
            for i, (a, b) in enumerate(
                zip(prod_sql.splitlines(True), V0_SQL.splitlines(True))
            )
            if a != b
        ]
    print(f"[N={n}] V0 == production _load: {fidelity['match']} "
          f"(prod {fidelity['prod_len']} chars, harness {fidelity['v0_len']} chars)", flush=True)

    engine = create_async_engine(URL, poolclass=NullPool)
    schema = "var_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out = {
        "n": n, "schema": schema, "corpus_id": corpus_id,
        "fidelity": fidelity, "production_sql": prod_sql,
        "variants": {}, "dump_v0": dump_v0,
    }
    t_start = time.perf_counter()
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            t0 = time.perf_counter()
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrations)
            migrate_s = time.perf_counter() - t0
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": schema},
            )
            documents = build_documents(n)
            service = IngestionService()
            service.embedder = Offline()
            t0 = time.perf_counter()
            for i, (path, content) in enumerate(documents, 1):
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    await service._ingest_content(db, content, path, corpus_id)
                if i % 200 == 0:
                    print(f"[N={n}]   ingested {i}/{len(documents)} docs "
                          f"({time.perf_counter() - t0:.0f}s)", flush=True)
            ingest_s = time.perf_counter() - t0

            stats = (await conn.execute(text(
                "SELECT c.relname, c.reltuples, c.relpages, s.n_live_tup, s.last_analyze, s.last_autoanalyze "
                "FROM pg_stat_user_tables s JOIN pg_class c ON c.oid = s.relid "
                "WHERE c.relnamespace = current_schema()::regnamespace "
                "AND c.relname IN ('memory_claims','memory_evidence','memory_chunks','memory_versions') "
                "ORDER BY c.relname"
            ))).fetchall()
            counts = (await conn.execute(text(
                "SELECT (SELECT count(*) FROM memory_claims WHERE corpus_id=:c) AS claims,"
                " (SELECT count(*) FROM memory_evidence WHERE corpus_id=:c) AS evidence,"
                " (SELECT count(*) FROM memory_chunks WHERE corpus_id=:c) AS chunks,"
                " (SELECT count(*) FROM memory_versions WHERE corpus_id=:c) AS versions,"
                " (SELECT count(*) FROM memory_documents WHERE corpus_id=:c) AS documents"
            ), {"c": corpus_id})).mappings().one()
            await conn.execute(text("SET plan_cache_mode = force_custom_plan"))
            settings = {}
            for key in ("plan_cache_mode", "default_statistics_target", "jit", "shared_buffers",
                        "work_mem", "random_page_cost", "seq_page_cost", "enable_indexscan",
                        "enable_seqscan", "enable_bitmapscan", "server_version"):
                settings[key] = (await conn.execute(text("SHOW " + key))).scalar()
            print(f"[N={n}] migrate {migrate_s:.1f}s  ingest {ingest_s:.1f}s  "
                  f"rows={dict(counts)}", flush=True)
            print(f"[N={n}] pg_stat_user_tables BEFORE any EXPLAIN:\n"
                  + "\n".join("      " + " | ".join("NULL" if v is None else str(v) for v in row)
                              for row in stats), flush=True)

            params = {"c": corpus_id, "path": None, "at": None}
            assert prod_params is not None and set(prod_params) == set(PARAMS_KEYS), prod_params
            out["row_counts"] = {k: int(v) for k, v in counts.items()}
            out["pg_stat_user_tables"] = [
                [("NULL" if v is None else str(v)) for v in row] for row in stats
            ]
            out["settings"] = {k: str(v) for k, v in settings.items()}
            out["migrate_s"] = round(migrate_s, 1)
            out["ingest_s"] = round(ingest_s, 1)

            baseline_hash = None
            plans_by_name = {}
            for name, sql, _r, pre, why in VARIANTS:
                for stmt in RESET:
                    await conn.execute(text(stmt))
                for stmt in pre:
                    await conn.execute(text(stmt))
                samples = []
                plans = []
                roots = []
                for _ in range(reps):
                    res = await conn.execute(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), params
                    )
                    doc = as_json(res.scalar())
                    plans.append(doc)
                    roots.append(doc["Plan"])
                    samples.append(doc)
                # Non-EXPLAIN execution, for the row-for-row comparison.
                for stmt in RESET:
                    await conn.execute(text(stmt))
                for stmt in pre:
                    await conn.execute(text(stmt))
                got = await fetch_rows(conn, params, sql)
                rec = {
                    "why": why,
                    "pre": list(pre),
                    "planning_ms": [round(p["Planning Time"], 3) for p in samples],
                    "exec_ms": [round(p["Execution Time"], 3) for p in samples],
                    "shared_hit": [r.get("Shared Hit Blocks") for r in roots],
                    "shared_read": [r.get("Shared Read Blocks") for r in roots],
                    "temp_read": [r.get("Temp Read Blocks") for r in roots],
                    "claims_index": scan_info(roots[0], "memory_claims"),
                    "evidence_index": scan_info(roots[0], "memory_evidence"),
                    "chunks_index": scan_info(roots[0], "memory_chunks"),
                    "root_node": roots[0].get("Node Type"),
                    "jit": samples[0].get("JIT", {}),
                    "jit_timing": samples[0].get("JIT Timing", {}),
                    "nondefault_settings": samples[0].get("Settings", {}),
                    "n_rows": got["n_rows"],
                    "cols": got["cols"],
                    "sha256": got["sha256"],
                }
                if name == "V0":
                    rec["identical_to_V0"] = "SELF"
                    baseline_hash = got["sha256"]
                    out["v0_explain_json"] = plans[0]
                    if dump_v0:
                        p = EV / f"112-v0-explain-n{n:05d}.json"
                        p.write_text(json.dumps(plans[0], indent=2) + "\n")
                        out["v0_json_path"] = str(p.relative_to(ROOT))
                else:
                    rec["identical_to_V0"] = (got["sha256"] == baseline_hash
                                              and got["n_rows"] == rec["n_rows"])
                out["variants"][name] = rec
                plans_by_name[name] = plans
                if n in DUMP_PLANS_FOR:
                    (EV / f"113-all-variants-n{n:05d}.json").write_text(
                        json.dumps(out["variants"], indent=2) + "\n")
                    (EV / f"114-plans-n{n:05d}.json").write_text(
                        json.dumps({k: v[0] for k, v in plans_by_name.items()}, indent=2) + "\n")
                    out["all_variants_json"] = [
                        f"docs/performance/evidence/113-all-variants-n{n:05d}.json",
                        f"docs/performance/evidence/114-plans-n{n:05d}.json"]
                ci = rec["claims_index"] or {}
                ei = rec["evidence_index"] or {}
                print(
                    f"[N={n}] {name:>4}  plan={[round(x,3) for x in rec['planning_ms']]} "
                    f"exec={[round(x,1) for x in rec['exec_ms']]} "
                    f"hit={rec['shared_hit']} "
                    f"claims_idx={ci.get('index')} rf={ci.get('rows_removed_by_filter')} | "
                    f"ev_idx={ei.get('index')} rf={ei.get('rows_removed_by_filter')} | "
                    f"rows={rec['n_rows']} same={rec['identical_to_V0']}",
                    flush=True,
                )
        finally:
            await outer.rollback()
    await engine.dispose()
    out["total_s"] = round(time.perf_counter() - t_start, 1)
    print(f"[N={n}] done in {out['total_s']}s (schema rolled back)", flush=True)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dump-v0", action="store_true")
    ap.add_argument("--dump-plans-for", default="")
    args = ap.parse_args()
    global DUMP_PLANS_FOR
    DUMP_PLANS_FOR = {int(x) for x in args.dump_plans_for.split(",") if x.strip()}
    results = []
    for n in [int(s) for s in args.sizes.split(",")]:
        results.append(await run_size(n, args.reps, args.dump_v0))
        Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
    print("written:", args.out, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
