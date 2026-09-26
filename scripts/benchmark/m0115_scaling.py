"""M011.5: how does authority resolution scale with archive size?

M011 found authority resolution at 92.6% of pipeline cost. This measures how
that cost grows, by ingesting prefixes of the corpus and timing the same
question at each size. If the gate embeds every claim on every query, cost
should track archive size, and that would be work the question did not ask for.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0115_dataset import build_corpus  # noqa: E402
from m0115_experiments import VALID_AT, _async_url, _migrate, _session  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

PROBE_QUESTIONS = (
    "What is the current primary datastore?",
    "Who owns the Orders Service?",
    "What is the current job queue?",
    "What is the current search engine?",
)


async def counts(conn, corpus_id: str) -> dict:
    async with _session(conn) as db:
        row = await db.execute(
            text(
                "SELECT (SELECT count(*) FROM memory_versions WHERE corpus_id = :c) AS versions, "
                "(SELECT count(*) FROM memory_claims WHERE corpus_id = :c) AS claims, "
                "(SELECT count(*) FROM chunks ch JOIN documents d ON d.id = ch.doc_id "
                "  WHERE d.corpus_id = :c) AS chunks"
            ),
            {"c": corpus_id},
        )
    return {k: int(v) for k, v in row.mappings().one().items()}


async def time_question(conn, schema: str, question: str, *, lexical: bool) -> float:
    from api.models.memory import MemoryRequest
    from api.services import memory
    from api.services.corpora import get_corpus_by_name
    from api.services.memory_public import State, project
    from api.services.memory_query import interpret, select

    request = MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000)
    start = time.perf_counter()
    async with _session(conn) as db:
        corpus = await get_corpus_by_name(db, schema)
        versions = await memory._load(db, corpus["id"])
    state = State(as_of=None, valid_at=VALID_AT)
    full = project(
        versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
    )
    intent = interpret(request)
    if lexical:
        select(full, request, intent, None, lexical=True)
    else:
        from api.services.embedder import Embedder

        select(full, request, intent, Embedder())
    return (time.perf_counter() - start) * 1000


async def run(url: str, repeats: int) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    docs = build_corpus()
    report = {"repeats": repeats, "points": []}

    for prefix in (10, 25, 45, len(docs)):
        schema = "m115s_" + uuid4().hex[:10]
        corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
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
                for doc in docs[:prefix]:
                    async with _session(conn) as db:
                        await service._ingest_content(db, doc.content, doc.path, corpus_id)
                size = await counts(conn, corpus_id)

                semantic, lexical = [], []
                for _ in range(repeats):
                    for question in PROBE_QUESTIONS:
                        semantic.append(await time_question(conn, schema, question, lexical=False))
                        lexical.append(await time_question(conn, schema, question, lexical=True))
                point = {
                    "documents_ingested": prefix,
                    **size,
                    "semantic_p50": round(statistics.median(semantic), 2),
                    "semantic_p95": round(sorted(semantic)[int(len(semantic) * 0.95) - 1], 2),
                    "lexical_p50": round(statistics.median(lexical), 2),
                    "lexical_p95": round(sorted(lexical)[int(len(lexical) * 0.95) - 1], 2),
                }
                point["ms_per_claim"] = round(point["semantic_p50"] / max(1, size["claims"]), 2)
                report["points"].append(point)
                print(
                    f"  docs={prefix:>3} versions={size['versions']:>3} "
                    f"claims={size['claims']:>3}  "
                    f"semantic p50={point['semantic_p50']:>7.2f} ms  "
                    f"lexical p50={point['lexical_p50']:>6.2f} ms  "
                    f"{point['ms_per_claim']:.2f} ms/claim",
                    flush=True,
                )
            finally:
                # The schema was created inside the rolled-back transaction, so
                # it never existed outside this connection and needs no cleanup.
                await outer.rollback()
    await engine.dispose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0115-scaling.json"))
    args = parser.parse_args()
    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    print("M011.5 authority-resolution scaling")
    report = asyncio.run(run(url, args.repeats))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
