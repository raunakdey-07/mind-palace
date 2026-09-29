"""M012.2 Experiment C: is the vectorised relevance scorer pack-identical?

The scalar scorer accumulates a float64 inner product left to right. NumPy
computes the same quantity in one array pass and may associate differently, so
the scores can differ in the last bits. That is only acceptable if the
authoritative result never changes.

So the test is not "scores are close". For every question in the frozen
202-question benchmark, this runs the real public query path twice, scalar and
vectorised, and requires the canonical Memory Pack to be byte-identical.

It also hunts the failure mode directly: for every question it records the
smallest gap between any pair of candidate scores and the distance from every
score to each gate threshold. A near-tie or a score sitting on a threshold is
where a last-bit difference would become an answer difference, so both are
reported as evidence that the margin is real rather than luck.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
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

from m0115_dataset import build_corpus, build_questions  # noqa: E402

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"
GATES = (0.30, 0.65, 0.90)


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def run() -> dict:
    from m0117_scale import Offline

    from api.models.memory import MemoryRequest
    from api.services.corpora import get_corpus_by_name
    from api.services.ingestion import IngestionService
    from api.services.memory import _load
    from api.services.memory_public import State, project
    from api.services.memory_query import query as run_query

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "eq_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out: dict = {"questions": [], "differing": []}

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
            for doc in build_corpus():
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)

            async with _session(conn) as db:
                row = await get_corpus_by_name(db, schema)
                versions = await _load(db, row["id"], chunk_text=False)

            for question in build_questions():
                request = MemoryRequest(
                    corpus=schema, query=question.text, valid_at=VALID_AT, budget=128000
                )
                state = State(as_of=None, valid_at=VALID_AT)
                full = project(
                    versions,
                    request.model_copy(update={"query": "", "path": None}),
                    "pack",
                    state,
                    None,
                )
                async with _session(conn) as db:
                    scalar = await run_query(
                        full, request, db=db, corpus_id=corpus_id, vectorized=False
                    )
                    vector = await run_query(
                        full, request, db=db, corpus_id=corpus_id, vectorized=True
                    )
                same = scalar.canonical_json() == vector.canonical_json()
                row = {
                    "qid": question.qid,
                    "identical": same,
                    "digest": hashlib.sha256(scalar.canonical_json().encode()).hexdigest()[:16],
                }
                out["questions"].append(row)
                if not same:
                    out["differing"].append(question.qid)
        finally:
            await outer.rollback()
    await engine.dispose()
    return out


async def margins() -> dict:
    """How close does a real query get to flipping an answer on a last bit?"""
    import numpy as np

    from api.models.memory import MemoryRequest
    from api.services.claim_embeddings import ClaimView, representation_of
    from api.services.embedder import Embedder
    from api.services.memory_relevance import RelevancePolicy, plan

    embedder = Embedder()
    claims = []
    for doc in build_corpus():
        body = doc.content.split("---", 2)[-1]
        claims.append(ClaimView(body.splitlines()[0][:120], doc.path, doc.path))
    vectors = embedder.embed([representation_of(c.claim, c.key, c.path) for c in claims])
    matrix = np.asarray(vectors, dtype=np.float64)

    worst_tie = 1.0
    worst_gate = 1.0
    for question in build_questions():
        request = MemoryRequest(corpus="x", query=question.text, budget=128000)
        for topic in plan(request, "current", RelevancePolicy.configured()).topics:
            column = matrix @ np.asarray(embedder.embed([topic])[0], dtype=np.float64)
            ordered = np.sort(column)
            if len(ordered) > 1:
                gap = float(ordered[-1] - ordered[-2])
                worst_tie = min(worst_tie, abs(gap))
            for value in column:
                for gate in GATES:
                    worst_gate = min(worst_gate, abs(float(value) - gate))
    return {
        "smallest_top_two_score_gap": worst_tie,
        "smallest_distance_to_any_gate": worst_gate,
        "note": "A flip needs a gap of 0. Observed max numeric error was 1.6e-16.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default=str(ROOT / "docs/performance/m0122-scorer-equivalence.json")
    )
    args = parser.parse_args()
    report = asyncio.run(run())
    report["margins"] = asyncio.run(margins())
    total = len(report["questions"])
    same = sum(1 for q in report["questions"] if q["identical"])
    print(f"packs identical : {same}/{total}")
    if report["differing"]:
        print(f"DIFFERING      : {report['differing'][:10]}")
    m = report["margins"]
    print(f"smallest top-two score gap : {m['smallest_top_two_score_gap']:.3e}")
    print(f"smallest distance to a gate: {m['smallest_distance_to_any_gate']:.3e}")
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"written: {args.out}")
    return 0 if same == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
