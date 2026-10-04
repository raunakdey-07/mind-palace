"""M013.0: run the held-out question set through the same public path as M011.5.

The scoring, the failure taxonomy and the three strategies are imported from
``m0115_experiments`` rather than copied, so a difference between the development
set and the held-out set can only come from the questions. A second copy of the
scorer would eventually disagree with the first, and the comparison would stop
meaning anything.

    python scripts/benchmark/m0130_run_heldout.py --out docs/performance/m0130-heldout.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0115_dataset import build_corpus  # noqa: E402
from m0115_experiments import (  # noqa: E402
    FAILURES,
    _async_url,
    _migrate,
    _session,
    _stats,
    classify,
    lexical_detail_is_answer,
    score,
    select_with,
    summarise,
)
from m0130_heldout import build_questions, dataset_hash  # noqa: E402

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def _git() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
        timeout=10,
    ).stdout.strip()


async def run(url: str) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "m130_" + uuid4().hex[:12]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    questions = build_questions()
    report = {
        "meta": {
            "commit": _git(),
            "python": platform.python_version(),
            "dataset_hash": dataset_hash(),
            "questions": len(questions),
            "valid_at": VALID_AT.isoformat(),
            "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            "note": "held-out set; authored independently of the development benchmark",
        },
        "results": [],
        "timing": {"semantic_ms": [], "lexical_ms": []},
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
            for doc in build_corpus():
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)

            for question in questions:
                t0 = time.perf_counter()
                _full, semantic = await select_with(conn, schema, question.text, lexical=False)
                report["timing"]["semantic_ms"].append((time.perf_counter() - t0) * 1000)

                t0 = time.perf_counter()
                _full, lexical = await select_with(conn, schema, question.text, lexical=True)
                report["timing"]["lexical_ms"].append((time.perf_counter() - t0) * 1000)

                sem_ok, sem_code, sem_detail = score(question, semantic)
                lex_ok, lex_code, lex_detail = score(question, lexical)

                if lex_ok and lexical_detail_is_answer(lexical):
                    fallback_ok, fallback_code = lex_ok, ""
                else:
                    fallback_ok, fallback_code = sem_ok, sem_code

                report["results"].append(
                    {
                        "qid": question.qid,
                        "category": question.category,
                        "text": question.text,
                        "expect_state": question.expect_state,
                        "expect_key": question.key,
                        "semantic_ok": sem_ok,
                        "lexical_ok": lex_ok,
                        "fallback_ok": fallback_ok,
                        "failure": (
                            "" if fallback_ok else classify(question, fallback_code, lex_ok, sem_ok)
                        ),
                        "lexical_status": lex_detail["status"],
                        "semantic_status": sem_detail["status"],
                        "keys_returned": lex_detail["keys"],
                    }
                )
        finally:
            await outer.rollback()
    await engine.dispose()

    report["timing"] = {
        "semantic_ms": _stats(report["timing"]["semantic_ms"]),
        "lexical_ms": _stats(report["timing"]["lexical_ms"]),
    }
    report["summary"] = summarise(report["results"])
    report["failure_taxonomy"] = FAILURES
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0130-heldout.json"))
    args = parser.parse_args()

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    report = asyncio.run(run(url))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    summary = report["summary"]
    total = summary["total"]
    print(f"held-out dataset {report['meta']['dataset_hash'][:16]}  questions {total}")
    for name, count in summary["strategies"].items():
        print(f"  {name:<12} {count}/{total}")
    print("\nby category (n / semantic / lexical / fallback):")
    for category, row in sorted(summary["by_category"].items()):
        print(
            f"  {category:<14} {row['n']:>3}  {row['semantic']:>3}  {row['lexical']:>3}  "
            f"{row['fallback']:>3}"
        )
    print(f"\nfailures: {summary['failures']}")
    for r in report["results"]:
        if not r["fallback_ok"]:
            print(f"  {r['qid']:<24} {r['failure']:<4} {r['text']}")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
