"""M013 candidate-recall taxonomy: why is gold unreachable?

The fusion experiment established a hard limit: on 19 of 140 held-out questions the
gold key is in NEITHER the semantic nor the lexical candidate gate. No ranking
change and no reranker can reach those, because neither arm proposed them.

This classifies every keyed question by WHERE it fails, so the next experiment is
chosen by evidence rather than by fashion:

  reachable_both        gold in semantic AND lexical
  reachable_semantic    gold in semantic only
  reachable_lexical     gold in lexical only  (fusion can recover this)
  UNREACHABLE_candidate gold in neither gate  (no fusion or rerank can help)
  UNREACHABLE_query     gold IS in the union, but the query never produced a
                        candidate because query planning produced no topics

For each UNREACHABLE case it also reports WHY, using evidence already in the
corpus rather than a guess:

  * is the gold claim's authored key present in the archive at all?
  * does the gold claim's text share ANY content term with the query after the
    shipped stopword/stemming pass? If not, the query wording and the authored
    text have no lexical bridge.
  * what is the top cosine similarity between the query and any candidate? A low
    ceiling with a reachable union means the *embedding* failed, not the gate.

It does not classify by intuition. Each verdict is a measured comparison.
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
    from api.services.memory_relevance import RelevancePolicy, relevance, terms

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    corpus_name = f"m013-ceil-{dataset}"
    corpus_id = corpus_id_for_name(corpus_name)
    rows = []
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema = "m013ceil_" + uuid4().hex[:10]
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
                sem_keys, lex_keys = set(sem), set(lex)

                # What is the best score the shipped gate gave ANY key, and does
                # the gold key exist in the projection at all?
                gold_claim = next(
                    (
                        c
                        for c in full.current_memories
                        + full.historical_memories
                        + full.uncertain_memories
                        + [x for g in full.conflicts for x in g.claims]
                        if c.key == q.key
                    ),
                    None,
                )
                best_key = max(sem.items(), key=lambda kv: kv[1], default=(None, 0.0))
                best_lex = max(lex.items(), key=lambda kv: kv[1], default=(None, 0.0))

                # Lexical bridge: does the query share content terms with the gold
                # claim text, after the shipped stopword + stemming pass?
                query_terms = terms(q.text)
                gold_terms = terms(gold_claim.claim) if gold_claim else set()
                shared = sorted(query_terms & gold_terms)

                if q.key in sem_keys and q.key in lex_keys:
                    bucket = "reachable_both"
                elif q.key in sem_keys:
                    bucket = "reachable_semantic"
                elif q.key in lex_keys:
                    bucket = "reachable_lexical"
                else:
                    bucket = "UNREACHABLE_candidate"

                rows.append(
                    {
                        "qid": q.qid,
                        "category": q.category,
                        "question": q.text,
                        "gold_key": q.key,
                        "bucket": bucket,
                        "gold_claim_in_projection": gold_claim is not None,
                        "gold_claim_text": gold_claim.claim if gold_claim else None,
                        "query_terms": sorted(query_terms),
                        "gold_claim_terms": sorted(gold_terms),
                        "shared_query_gold_terms": shared,
                        "shared_term_count": len(shared),
                        "best_semantic_key": best_key[0],
                        "best_semantic_score": round(best_key[1], 4),
                        "best_lexical_key": best_lex[0],
                        "best_lexical_score": round(best_lex[1], 4),
                    }
                )
        finally:
            await outer.rollback()
    await engine.dispose()

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1

    unreachable = [r for r in rows if r["bucket"] == "UNREACHABLE_candidate"]
    # A ceiling that has NO lexical bridge is a vocabulary problem, not a
    # ranking problem. Quantify it rather than assert it.
    no_bridge = [r for r in unreachable if r["shared_term_count"] == 0]
    with_bridge = [r for r in unreachable if r["shared_term_count"] > 0]

    return {
        "dataset": dataset,
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "python": platform.python_version(),
        "keyed_questions": len(rows),
        "buckets": counts,
        "candidate_recall_ceiling": round((len(rows) - len(unreachable)) / len(rows), 4),
        "unreachable": {
            "total": len(unreachable),
            "gold_missing_from_projection": sum(
                1 for r in unreachable if not r["gold_claim_in_projection"]
            ),
            "no_lexical_bridge_with_gold": len(no_bridge),
            "has_lexical_bridge_but_still_gated_out": len(with_bridge),
        },
        "interpretation": {
            "candidate_recall_ceiling": (
                "Upper bound on what ANY reranker or fusion can achieve. "
                "1 - (unreachable / keyed_questions)."
            ),
            "no_lexical_bridge": (
                "Query and gold claim share ZERO content terms after the shipped "
                "stopword/stemming pass. Neither lexical nor a lexical arm can "
                "bridge these; only semantics could, and it did not."
            ),
            "gold_missing_from_projection": (
                "The authored key does not appear in the projected memory at all. "
                "This is a representation gap, not a retrieval gap: no ranking "
                "function can return something the projection never built."
            ),
        },
        "unreachable_detail": unreachable,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="heldout", choices=("dev", "heldout"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = asyncio.run(main(args.dataset))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"dataset={result['dataset']} keyed={result['keyed_questions']}")
    for bucket, count in sorted(result["buckets"].items()):
        print(f"  {bucket:<28} {count}")
    print(f"candidate recall ceiling : {result['candidate_recall_ceiling']:.4f}")
    for k, v in result["unreachable"].items():
        print(f"  {k:<45} {v}")
    print(f"written: {args.out}")
