"""M013: why fusion is inert. A diagnostic, not a benchmark.

The retrieval matrix showed hybrid/RRF identical to semantic on both datasets.
This script tests the candidate explanation directly: that the relevance gate
already prunes the candidate set to ~2 keys, so reciprocal rank fusion over an
already-pruned set has nothing to reorder.

Measures, per question:
  * keys accepted by the semantic gate
  * keys accepted by the lexical gate
  * how many lexical-only keys exist (the only thing fusion could possibly add)
  * how many semantic-only keys exist (the only thing fusion could demote)

If "lexical-only" is ~0 on questions where semantic misses, fusion cannot help,
and the correct conclusion is that fusion is not the missing ingredient.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
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

from m0115_dataset import build_corpus  # noqa: E402

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
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


async def main(dataset: str) -> dict:
    if dataset == "dev":
        from m0115_dataset import build_questions
    else:
        from m0130_heldout import build_questions
    questions = [q for q in build_questions() if q.key]

    from api.models.memory import MemoryRequest
    from api.services import memory as memory_service
    from api.services.corpora import corpus_id_for_name
    from api.services.embedder import Embedder
    from api.services.ingestion import IngestionService
    from api.services.memory_public import State, project
    from api.services.memory_relevance import RelevancePolicy, relevance

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    corpus_name = f"m013-diag-{dataset}"
    corpus_id = corpus_id_for_name(corpus_name)
    rows = []
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema = "m013diag_" + uuid4().hex[:10]
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrations)
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": corpus_name},
            )
            service = IngestionService()
            for doc in build_corpus():
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)
            async with _session(conn) as db:
                versions = await memory_service._load(
                    db, corpus_id, chunk_text=False, version_text=False
                )

            embedder = Embedder()
            policy = RelevancePolicy.configured()
            state = State(as_of=None, valid_at=VALID_AT)

            for q in questions:
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
                sem, _p, _w = relevance(full, request, "current", embedder, policy)
                lex, _p2, _w2 = relevance(full, request, "current", None, policy, lexical=True)
                sem_keys = set(sem)
                lex_keys = set(lex)
                rows.append(
                    {
                        "qid": q.qid,
                        "gold": q.key,
                        "semantic_keys": sorted(sem_keys),
                        "lexical_keys": sorted(lex_keys),
                        "semantic_has_gold": q.key in sem_keys,
                        "lexical_has_gold": q.key in lex_keys,
                        "lexical_only": sorted(lex_keys - sem_keys),
                        "semantic_only": sorted(sem_keys - lex_keys),
                        "union": sorted(sem_keys | lex_keys),
                    }
                )
        finally:
            await outer.rollback()
    await engine.dispose()

    sem_miss = [r for r in rows if not r["semantic_has_gold"]]
    lexical_only_total = sum(len(r["lexical_only"]) for r in sem_miss)
    recoverable = [r for r in sem_miss if r["gold"] in r["lexical_only"]]
    return {
        "dataset": dataset,
        "keyed_questions": len(rows),
        "semantic_gold_rate": round(sum(1 for r in rows if r["semantic_has_gold"]) / len(rows), 4),
        "lexical_gold_rate": round(sum(1 for r in rows if r["lexical_has_gold"]) / len(rows), 4),
        "mean_semantic_candidates": round(
            sum(len(r["semantic_keys"]) for r in rows) / len(rows), 2
        ),
        "mean_lexical_candidates": round(sum(len(r["lexical_keys"]) for r in rows) / len(rows), 2),
        "mean_union_candidates": round(sum(len(r["union"]) for r in rows) / len(rows), 2),
        "semantic_misses": len(sem_miss),
        "recoverable_by_fusion": len(recoverable),
        "lexical_only_keys_on_semantic_misses": lexical_only_total,
        "interpretation": (
            "Fusion can only help if a lexical-only key contains the gold key on a "
            "question the semantic gate missed. 'recoverable_by_fusion' counts exactly "
            "those. Recoverable == 0 means fusion cannot improve recall at all, "
            "regardless of RRF parameters."
        ),
        "sample_semantic_misses": [r for r in sem_miss][:10],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dev", choices=("dev", "heldout"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = asyncio.run(main(args.dataset))
    result["commit"] = os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip()
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for k, v in result.items():
        if k not in {"sample_semantic_misses", "interpretation"}:
            print(f"{k}: {v}")
    print(f"\nwritten: {args.out}")
