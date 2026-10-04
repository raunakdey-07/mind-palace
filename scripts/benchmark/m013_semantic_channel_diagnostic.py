"""M013 diagnostic: does evidence-aware embedding improve the SEMANTIC channel?

Research only. Nothing here is selected on; this measures whether enriching the
embedded representation moves gold candidates closer, which is the precondition
for the production experiment.

For every scored question on dev, v1 and v2, and for the known ABSENT cases, it
computes for each candidate:

    S_old = cos(query, claim + key + path)                  # production
    S_new = cos(query, claim + key + path + evidence + doc)  # candidate

and reports, without selecting on the new score:

  * gold candidate score under each representation
  * nearest NON-gold candidate score under each
  * the margin between them (this is what the acceptance floor actually sees)
  * whether a gold candidate that failed the production floor now clears it
  * how many non-gold candidates clear the floor under each

The last two are the diagnostic that matters: an enrichment that lifts gold but
lifts non-gold equally is not a retrieval win.
"""

from __future__ import annotations

import argparse
import asyncio
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"
FLOOR = 0.30
STRONG = 0.65


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


async def corpus_state():
    from m0115_dataset import build_corpus

    from api.services.ingestion import IngestionService

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "diag_" + uuid4().hex[:10]
    corpus_id = "d" * 64
    rows = []
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
            for doc in build_corpus():
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)
            async with _session(conn) as db:
                r = await db.execute(
                    text(
                        "SELECT c.id, c.key, c.claim, d.path, "
                        "  COALESCE((SELECT string_agg(e.quote, ' ' ORDER BY e.id) "
                        "     FROM memory_evidence e WHERE e.corpus_id=c.corpus_id "
                        "       AND e.version_id=c.version_id AND e.claim_id=c.id),'') "
                        "FROM memory_claims c JOIN memory_documents d "
                        "  ON d.corpus_id=c.corpus_id AND d.id=c.memory_document_id "
                        "WHERE c.corpus_id=:cid"
                    ),
                    {"cid": corpus_id},
                )
                rows = [
                    {"id": x[0], "key": x[1], "claim": x[2], "path": x[3], "evidence": x[4] or ""}
                    for x in r
                ]
        finally:
            await outer.rollback()
    await engine.dispose()

    for row in rows:
        # Document context: the path is the only context available on both the
        # SQL fill path and the projected path, so it is the honest choice here.
        row["old"] = f"{row['claim']} {row['key']} {row['path']}"
        row["new"] = f"{row['claim']} {row['key']} {row['path']} {row['evidence']}".strip()
    return rows


def questions_for(dataset):
    if dataset == "dev":
        from m0115_dataset import build_questions
    else:
        mod = "m0130_heldout" if dataset == "v1" else "m013_heldout_v2"
        build_questions = __import__(mod).build_questions
    return [q for q in build_questions() if q.key]


async def run(dataset: str) -> dict:
    from api.services.embedder import Embedder

    rows = await corpus_state()
    embedder = Embedder()
    qs = questions_for(dataset)

    # Embed every candidate once per representation.
    vec_old = embedder.embed([r["old"] for r in rows])
    vec_new = embedder.embed([r["new"] for r in rows])

    out = []
    for q in qs:
        qv = embedder.embed([q.text])[0]
        gold_idx = next((i for i, r in enumerate(rows) if r["key"] == q.key), None)
        s_old = [cos(qv, v) for v in vec_old]
        s_new = [cos(qv, v) for v in vec_new]

        def summarise(scores):
            gold = scores[gold_idx] if gold_idx is not None else None
            negatives = [s for i, s in enumerate(scores) if i != gold_idx]
            best_neg = max(negatives) if negatives else None
            return {
                "gold": round(gold, 4) if gold is not None else None,
                "best_negative": round(best_neg, 4) if best_neg is not None else None,
                "margin": (
                    round(gold - best_neg, 4) if gold is not None and best_neg is not None else None
                ),
                "gold_clears_floor": gold is not None and gold >= FLOOR,
                "negatives_clearing_floor": sum(1 for s in negatives if s >= FLOOR),
                "negatives_clearing_strong": sum(1 for s in negatives if s >= STRONG),
            }

        out.append(
            {
                "qid": q.qid,
                "category": q.category,
                "gold_key": q.key,
                "old": summarise(s_old),
                "new": summarise(s_new),
            }
        )

    return {
        "dataset": dataset,
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "python": platform.python_version(),
        "candidates": len(rows),
        "floor": FLOOR,
        "strong": STRONG,
        "per_question": out,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", default="dev,v1,v2")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = {
        "meta": {
            "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
            "note": "score-only diagnostic; nothing is selected on the new score",
            "old": "claim + key + path",
            "new": "claim + key + path + own evidence",
        },
        "datasets": {d: asyncio.run(run(d)) for d in args.datasets.split(",")},
    }
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    for name, block in report["datasets"].items():
        pq = block["per_question"]
        gained = sum(
            1 for r in pq if not r["old"]["gold_clears_floor"] and r["new"]["gold_clears_floor"]
        )
        lost = sum(
            1 for r in pq if r["old"]["gold_clears_floor"] and not r["new"]["gold_clears_floor"]
        )
        neg_delta = sum(
            r["new"]["negatives_clearing_floor"] - r["old"]["negatives_clearing_floor"] for r in pq
        )
        strong_delta = sum(
            r["new"]["negatives_clearing_strong"] - r["old"]["negatives_clearing_strong"]
            for r in pq
        )
        margin_gain = [
            r["new"]["margin"] - r["old"]["margin"]
            for r in pq
            if r["new"]["margin"] is not None and r["old"]["margin"] is not None
        ]
        mean_margin = sum(margin_gain) / len(margin_gain) if margin_gain else 0.0
        print(f"\n=== {name} ({len(pq)} scored, {block['candidates']} candidates) ===")
        print(f"  gold newly clears floor : {gained}")
        print(f"  gold stops clearing floor: {lost}")
        print(f"  mean margin change      : {mean_margin:+.4f}")
        print(f"  negatives >= floor  delta: {neg_delta:+d}")
        print(f"  negatives >= strong delta: {strong_delta:+d}")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
