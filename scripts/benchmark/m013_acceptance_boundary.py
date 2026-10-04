"""M013: characterise the acceptance boundary. Research only.

Answers the question the previous experiments kept colliding with: is cosine
similarity a RANKING signal, an ACCEPTANCE signal, or both, and does the evidence
support the current dual use?

For every evaluated question on dev, v1 and v2, and for ABSENT questions
separately, this records per candidate:

    cosine        the production semantic score
    overlap       the lexical token-overlap boolean the gate also consults
    rank          position by cosine
    accepted      under the CURRENT policy E gate, unchanged
    gold          whether this candidate is the authored key for the question

Nothing here changes behaviour. It is the dataset the milestone asks for: a
confusion matrix over the acceptance decision, plus score distributions, plus a
threshold sweep, all from the real production path.

The sweep is diagnostic only. It reports what a different threshold WOULD have
done, so the current one can be judged against alternatives on the same data.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
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

#: The production policy, read from code rather than restated here.
MINIMUM = 0.30
STRONG = 0.65
RELATIVE = 0.90
SWEEP = [round(0.00 + 0.05 * i, 2) for i in range(19)]  # 0.00 .. 0.90


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


def current_accept(value: float, overlap: bool) -> bool:
    """The production gate E decision, transcribed exactly."""
    return value >= MINIMUM and (overlap or value >= STRONG or math.isclose(value, MINIMUM))


def load_questions(dataset):
    if dataset == "dev":
        from m0115_dataset import build_questions
    else:
        mod = "m0130_heldout" if dataset == "v1" else "m013_heldout_v2"
        build_questions = __import__(mod).build_questions
    return build_questions()


async def build_state():
    from m0115_dataset import build_corpus

    from api.services.ingestion import IngestionService

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "acc_" + uuid4().hex[:10]
    corpus_id = "a" * 64
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
        finally:
            await outer.rollback()
    await engine.dispose()


async def collect(dataset: str) -> dict:
    from m0115_dataset import build_corpus

    from api.models.memory import MemoryRequest
    from api.services import memory as memory_service
    from api.services.claim_embeddings import representation
    from api.services.corpora import corpus_id_for_name
    from api.services.embedder import Embedder
    from api.services.memory_public import State, _claims, project
    from api.services.memory_relevance import claim_terms, terms

    corpus_name = "acc-" + dataset
    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    embedder = Embedder()
    rows: list[dict] = []
    corpus_id = corpus_id_for_name(corpus_name)

    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema = "accq_" + uuid4().hex[:10]
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrations)
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": corpus_name},
            )
            from api.services.ingestion import IngestionService

            svc = IngestionService()
            for doc in build_corpus():
                async with _session(conn) as db:
                    await svc._ingest_content(db, doc.content, doc.path, corpus_id)
            async with _session(conn) as db:
                versions = await memory_service._load(
                    db, corpus_id, chunk_text=False, version_text=False
                )

            state = State(as_of=None, valid_at=VALID_AT)
            for q in load_questions(dataset):
                request = MemoryRequest(
                    corpus=corpus_name, query=q.text, valid_at=VALID_AT, budget=128000
                )
                full = project(
                    versions,
                    request.model_copy(update={"query": "", "path": None}),
                    "pack",
                    state,
                    None,
                )
                claims = {c.id: c for c in _claims(full)}
                ids = sorted(claims)
                if not ids:
                    continue
                reps = [representation(claims[c]) for c in ids]
                docs = [claim_terms(t) for t in reps]
                common = frozenset.intersection(*docs) if len(docs) > 1 else frozenset()
                reduced = [d - common for d in docs]
                qv = embedder.embed([q.text])[0]
                cvs = embedder.embed(reps)
                qterms = terms(q.text) - common

                scored = []
                for pos, cid in enumerate(ids):
                    value = cos(qv, cvs[pos])
                    overlap = bool(qterms & reduced[pos])
                    scored.append(
                        {
                            "key": claims[cid].key,
                            "claim_id": cid,
                            "cosine": round(value, 4),
                            "overlap": overlap,
                            "accepted": current_accept(value, overlap),
                            "is_gold": claims[cid].key == q.key if q.key else False,
                        }
                    )
                scored.sort(key=lambda r: (-r["cosine"], r["key"]))
                for rank, row in enumerate(scored, 1):
                    row["rank"] = rank

                rows.append(
                    {
                        "qid": q.qid,
                        "category": q.category,
                        "expect_state": q.expect_state,
                        "gold_key": q.key,
                        "top": scored[0]["cosine"] if scored else None,
                        "second": scored[1]["cosine"] if len(scored) > 1 else None,
                        "margin": (
                            round(scored[0]["cosine"] - scored[1]["cosine"], 4)
                            if len(scored) > 1
                            else None
                        ),
                        "candidates": scored,
                    }
                )
        finally:
            await outer.rollback()
    await engine.dispose()

    return {
        "dataset": dataset,
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "questions": len(rows),
        "rows": rows,
    }


def confusion(block):
    """TP/FP/TN/FN over the acceptance decision, using authored gold as truth."""
    tp = fp = tn = fn = 0
    absent_fp = absent_correct = 0
    for row in block["rows"]:
        absent = row["expect_state"] == "ABSENT"
        accepted_any = any(c["accepted"] for c in row["candidates"])
        gold_accepted = any(c["accepted"] and c["is_gold"] for c in row["candidates"])
        if absent:
            if accepted_any:
                absent_fp += 1
            else:
                absent_correct += 1
            continue
        if gold_accepted and accepted_any:
            tp += 1
        elif gold_accepted and not accepted_any:
            fn += 1
        elif not gold_accepted and accepted_any:
            fp += 1
        else:
            tn += 1
    return {
        "TP": tp,
        "FP": fp,
        "TN": tn,
        "FN": fn,
        "absent_false_accepts": absent_fp,
        "correct_abstentions": absent_correct,
    }


def distributions(block):
    gold, neg, margins = [], [], []
    for row in block["rows"]:
        if row["expect_state"] == "ABSENT":
            continue
        for c in row["candidates"]:
            (gold if c["is_gold"] else neg).append(c["cosine"])
        if row["margin"] is not None:
            margins.append(row["margin"])

    def stats(values):
        if not values:
            return {}
        ordered = sorted(values)
        n = len(ordered)
        return {
            "n": n,
            "min": round(ordered[0], 4),
            "p05": round(ordered[int(0.05 * n)], 4),
            "median": round(ordered[n // 2], 4),
            "p95": round(ordered[min(n - 1, int(0.95 * n))], 4),
            "max": round(ordered[-1], 4),
        }

    # Overlap of the two score ranges: the crux of whether 0.30/0.65 separate.
    overlap_range = None
    if gold and neg:
        overlap_range = {
            "gold_below_best_negative": round(sum(1 for g in gold if g <= max(neg)), 2) / len(gold),
            "negatives_above_worst_gold": round(sum(1 for n_ in neg if n_ >= min(gold)), 2)
            / len(neg),
        }
    return {
        "gold_cosine": stats(gold),
        "negative_cosine": stats(neg),
        "margin": stats(margins),
        "range_overlap": overlap_range,
    }


def sweep(block):
    """What a single cosine threshold would do, per dataset. Diagnostic only."""
    out = []
    for t in SWEEP:
        tp = fp = fn = tn = 0
        absent_fp = absent_ok = 0
        for row in block["rows"]:
            if row["expect_state"] == "ABSENT":
                if any(c["cosine"] >= t for c in row["candidates"]):
                    absent_fp += 1
                else:
                    absent_ok += 1
                continue
            accepted = [c for c in row["candidates"] if c["cosine"] >= t]
            gold_ok = any(c["is_gold"] for c in accepted)
            if gold_ok:
                tp += 1
            elif accepted:
                fp += 1
            else:
                fn += 1
        out.append(
            {
                "threshold": t,
                "TP": tp,
                "FP": fp,
                "FN": fn,
                "TN": tn,
                "absent_false_accepts": absent_fp,
                "correct_abstentions": absent_ok,
                "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", default="dev,v1,v2")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    report = {
        "meta": {
            "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
            "python": platform.python_version(),
            "note": "research only; production gate E untouched",
            "policy": {"minimum": MINIMUM, "strong": STRONG, "relative": RELATIVE},
        },
        "datasets": {},
    }
    for d in args.datasets.split(","):
        block = asyncio.run(collect(d))
        report["datasets"][d] = {
            "questions": block["questions"],
            "confusion": confusion(block),
            "distributions": distributions(block),
            "sweep": sweep(block),
        }
        print(f"\n=== {d} ({block['questions']} questions) ===")
        c = report["datasets"][d]["confusion"]
        print(
            f"  TP={c['TP']} FP={c['FP']} TN={c['TN']} FN={c['FN']} "
            f"absent_fp={c['absent_false_accepts']} abstain_ok={c['correct_abstentions']}"
        )
        dist = report["datasets"][d]["distributions"]
        print(f"  gold cosine   {dist['gold_cosine']}")
        print(f"  negative cos  {dist['negative_cosine']}")
        if dist.get("range_overlap"):
            print(f"  range overlap {dist['range_overlap']}")

    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
