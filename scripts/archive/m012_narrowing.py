"""M012-A experiment: is the archive load O(n^2), and does a set-based rewrite fix it?

Baseline: `memory._load` runs three correlated `jsonb_agg` subqueries per version.
The plan at 1,000 versions showed `Actual Loops: 1000`, `Rows Removed by Filter: 999`
and 1,015,000 shared buffer hits, which is the signature of each version scanning
the whole corpus for its children and discarding 999 rows.

This compares the current statement against a set-based rewrite that aggregates each
child table once for the whole corpus and joins the result onto the versions.

Equivalence is asserted, not assumed: both statements must return byte-identical
rows, in the same order, for a corpus that contains a supersession, a conflict, a
tombstone and a restoration. A rewrite that is faster but different is a failure.
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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]

CURRENT = """
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
WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
  AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
ORDER BY v.observed_at, d.path, v.version_number
"""

# Set-based: each child table is aggregated once for the whole corpus, then the
# three aggregates are joined onto the version rows. Same rows, same order, one
# pass per table instead of one pass per version.
SET_BASED = """
WITH claims AS (
    SELECT mc.version_id, jsonb_agg(to_jsonb(mc) ORDER BY mc.key, mc.id) AS j
    FROM memory_claims mc WHERE mc.corpus_id = :c GROUP BY mc.version_id
), evidence AS (
    SELECT me.version_id, jsonb_agg(to_jsonb(me) ORDER BY me.id) AS j
    FROM memory_evidence me WHERE me.corpus_id = :c GROUP BY me.version_id
), chunks AS (
    SELECT ch.version_id,
           jsonb_agg(jsonb_build_object('id', ch.id, 'heading_path', ch.heading_path)
                     ORDER BY ch.order_index) AS j
    FROM memory_chunks ch WHERE ch.corpus_id = :c GROUP BY ch.version_id
)
SELECT v.*, d.path,
       COALESCE(cj.j, '[]'::jsonb) AS chunks,
       COALESCE(cl.j, '[]'::jsonb) AS claims,
       COALESCE(ej.j,  '[]'::jsonb) AS evidence
FROM memory_versions v
JOIN memory_documents d ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
LEFT JOIN claims  cl ON cl.version_id = v.id AND v.corpus_id = :c
LEFT JOIN evidence ej ON ej.version_id = v.id AND v.corpus_id = :c
LEFT JOIN chunks  cj ON cj.version_id = v.id AND v.corpus_id = :c
WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
  AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
ORDER BY v.observed_at, d.path, v.version_number
"""


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _evolving_corpus() -> list[tuple[str, str]]:
    """A corpus with the shapes where a narrowing rewrite could go wrong."""

    def doc(title, key, sentence, **extra):
        claims = "".join(
            f"  - key: {k}\n    value: {json.dumps(s)}\n"
            f"    claim: {json.dumps(s)}\n    evidence: {json.dumps(s)}\n"
            for k, s in [(key, sentence)]
        )
        body = (
            f'---\ntitle: "{title}"\ndocument_type: "design"\n'
            f"claims:\n{claims}---\n\n# {title}\n\n{sentence}\n"
        )
        return body

    files = [
        (
            "docs/db.md",
            doc("Storage", "architecture.database", "The primary database is PostgreSQL."),
        ),
        (
            "adrs/adr-011.md",
            doc(
                "ADR 011",
                "architecture.database",
                "The primary datastore is SQLite.",
                valid_from="2024-01-01T00:00:00+00:00",
            ),
        ),
    ]
    # A second, superseded version of docs/db.md plus a restored document.
    files.append(
        ("docs/db.md", doc("Storage", "architecture.database", "The primary database is MySQL."))
    )
    files.append(("docs/tmp.md", doc("Temp", "temp.note", "The temporary note is obsolete.")))
    return files


def _synthetic(n: int) -> list[tuple[str, str]]:
    from m0117_scale import build_documents

    return build_documents(n)


async def run(url: str, claims: int, repeats: int) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(url, poolclass=NullPool)
    schema = "narrow_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out: dict = {"claims_target": claims, "repeats": repeats, "equivalence": {}}

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
            service.embedder = _deterministic()
            for path, content in _evolving_corpus() + _synthetic(claims):
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    await service._ingest_content(db, content, path, corpus_id)

            params = {"c": corpus_id, "path": None, "at": None}
            # Equivalence first. A faster statement that returns different rows is a failure.
            old_rows = [tuple(r) for r in (await conn.execute(text(CURRENT), params)).fetchall()]
            new_rows = [tuple(r) for r in (await conn.execute(text(SET_BASED), params)).fetchall()]
            out["equivalence"] = {
                "rows_current": len(old_rows),
                "rows_set_based": len(new_rows),
                "byte_identical": [str(r) for r in old_rows] == [str(r) for r in new_rows],
            }
            if not out["equivalence"]["byte_identical"]:
                out["equivalence"]["first_difference"] = _first_diff(old_rows, new_rows)
                raise RuntimeError("the set-based rewrite is not equivalent; refusing to time it")

            for _ in range(2):
                await conn.execute(text(CURRENT), params)
            for _ in range(2):
                await conn.execute(text(SET_BASED), params)

            # Timed in separate runs. Interleaving them makes each wait for the
            # other to evict shared buffers, which measures the harness, not SQL.
            current_ms = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                await conn.execute(text(CURRENT), params)
                current_ms.append((time.perf_counter() - t0) * 1000)
            set_based_ms = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                await conn.execute(text(SET_BASED), params)
                set_based_ms.append((time.perf_counter() - t0) * 1000)

            out["current_ms"] = [round(v, 2) for v in current_ms]
            out["set_based_ms"] = [round(v, 2) for v in set_based_ms]
            out["current_p50"] = round(statistics.median(current_ms), 2)
            out["set_based_p50"] = round(statistics.median(set_based_ms), 2)

            for name, sql in (("current", CURRENT), ("set_based", SET_BASED)):
                plan = (
                    await conn.execute(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), params
                    )
                ).scalar()[0]
                out[f"plan_{name}"] = {
                    "planning_ms": plan["Planning Time"],
                    "execution_ms": plan["Execution Time"],
                    "nodes": _flatten(plan["Plan"]),
                }
        finally:
            await outer.rollback()
    await engine.dispose()
    return out


def _deterministic():
    from m0117_scale import Offline

    return Offline()


def _first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if str(x) != str(y):
            return {"row": i, "current": str(x)[:200], "set_based": str(y)[:200]}
    return {"length": [len(a), len(b)]}


def _flatten(node, depth=0):
    rows = []
    if isinstance(node, str):
        return rows
    rows.append(
        {
            "depth": depth,
            "type": node.get("Node Type"),
            "rows": node.get("Actual Rows"),
            "loops": node.get("Actual Loops"),
            "cost": node.get("Total Cost"),
            "hit": node.get("Shared Hit Blocks"),
            "removed_by_filter": node.get("Rows Removed by Filter"),
        }
    )
    for child in node.get("Plans", []) or []:
        rows.extend(_flatten(child, depth + 1))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claims", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m012-narrowing.json"))
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: set DATABASE_URL", file=sys.stderr)
        return 1
    url = url.replace("postgresql://", "postgresql+asyncpg://")

    result = asyncio.run(run(url, args.claims, args.repeats))
    print(f"corpus claims       : {result['claims_target']}")
    eq = result["equivalence"]
    print(f"rows current/set    : {eq['rows_current']} / {eq['rows_set_based']}")
    print(f"byte identical      : {eq['byte_identical']}")
    print(f"current   p50       : {result['current_p50']} ms  {result['current_ms']}")
    print(f"set-based  p50      : {result['set_based_p50']} ms  {result['set_based_ms']}")
    print(f"speedup             : {result['current_p50'] / result['set_based_p50']:.1f}x")
    for name in ("current", "set_based"):
        p = result[f"plan_{name}"]
        print(f"\n{name}: plan {p['planning_ms']:.2f} ms, exec {p['execution_ms']:.2f} ms")
        for n in p["nodes"][:8]:
            print(
                f"   {'  ' * n['depth']}{n['type']:<20} rows={n['rows']} loops={n['loops']} "
                f"cost={n['cost']} hit={n['hit']} removed={n['removed_by_filter']}"
            )
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
