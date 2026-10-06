"""M013: a real PostgreSQL full-text search arm, measured against the shipped lexical one.

Production has no FTS. `memory_relevance.relevance(lexical=True)` is Python set
intersection over claim tokens. This builds an actual tsvector/tsquery arm on the
same PostgreSQL that already holds the authoritative corpus, and measures whether it
is worth having. It adds no second database.

What it indexes and why
-----------------------
The obvious choice, `claim` alone, provably cannot work for this corpus. The
identifier-bearing questions ask about "2024-03" or "tier-0", but those strings live
in the authored KEY and the document PATH, not in the claim sentence:

  key   incident.2024-03.orders.root / constraint.deploy.tier-0
  claim "The orders service caps its connection pool at 30."

so an FTS over claim text alone cannot bridge those questions at all -- the same
trap the Python tokeniser fell into. This arm therefore indexes key + claim + value
+ path, which is exactly the material the projection already reads.

Determinism
-----------
`ts_rank_cd` is not guaranteed stable across PostgreSQL versions, so the ORDER BY
falls back to (rank DESC, key ASC) and every tie is broken on the authoritative
key. Candidate generation only: this arm returns ranked KEYS. It never touches claim
status, validity, supersession, conflicts or evidence.

Scored on the authored key, like every other arm, with seeded bootstrap CIs.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
import platform
import random
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

#: Column expression under test. `simple` avoids stemming surprises on identifiers;
#: the english config is measured separately so the cost of stemming is visible.
ARMS = ("fts_simple", "fts_english")


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


#: `path` lives on memory_documents, not memory_claims, so the vector expression
#: joins to it. That is the point of this arm: the identifier a question asks
#: about ("2024-03", "tier-0") lives in the path and the key, never in the
#: claim sentence, so an FTS over claim text alone cannot bridge those.
_DOC_VECTOR = (
    "(coalesce(c.key,'') || ' ' || coalesce(c.claim,'') || ' ' "
    "|| coalesce(c.value::text,'') || ' ' || coalesce(d.path,''))"
)


def bootstrap_ci(values, iterations: int = 2000, seed: int = 12345):
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return (
        round(means[int(0.025 * len(means))], 4),
        round(means[min(len(means) - 1, int(0.975 * len(means)))], 4),
    )


async def setup(conn, corpus_id: str) -> str:
    from api.services.ingestion import IngestionService

    schema = "fts_" + uuid4().hex[:10]
    await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    await conn.execute(text(f'SET search_path TO "{schema}", public'))
    await conn.execute(
        text("INSERT INTO corpora(id, name) VALUES (:id, :name) ON CONFLICT DO NOTHING"),
        {"id": corpus_id, "name": schema},
    )
    service = IngestionService()
    for doc in build_corpus():
        async with _session(conn) as db:
            await service._ingest_content(db, doc.content, doc.path, corpus_id)
    await conn.execute(text("COMMIT"))
    await conn.execute(text(f'SET search_path TO "{schema}", public'))
    # Statistics, so the planner is not measuring a missing-analyse artefact.
    for table in ("memory_versions", "memory_documents", "memory_claims", "memory_evidence"):
        await conn.execute(text(f"ANALYZE {table}"))

    # Experimental FTS. `path` lives on memory_documents and PostgreSQL forbids a
    # subquery in an index expression, so the searchable text is materialised into
    # a SEPARATE derived table rather than a column on the authoritative one. That
    # keeps this arm strictly experimental: no authoritative table is altered, and
    # dropping the schema removes every trace. This is what an L2-style index would
    # look like if it were ever adopted.
    await conn.execute(
        text(
            "CREATE TABLE m013_fts (corpus_id CHAR(64) NOT NULL, key TEXT NOT NULL, "
            "fts_text TEXT NOT NULL)"
        )
    )
    await conn.execute(
        text(
            "INSERT INTO m013_fts (corpus_id, key, fts_text) "
            "SELECT c.corpus_id, c.key, "
            "  coalesce(c.key,'') || ' ' || coalesce(c.claim,'') || ' ' "
            "  || coalesce(c.value::text,'') || ' ' || coalesce(d.path,'') "
            "FROM memory_claims c JOIN memory_documents d "
            "  ON d.corpus_id = c.corpus_id AND d.id = c.memory_document_id"
        )
    )
    for config, name in (("simple", "ix_m013_fts"), ("english", "ix_m013_fts_en")):
        await conn.execute(
            text(f"CREATE INDEX {name} ON m013_fts USING GIN (to_tsvector('{config}', fts_text))")
        )
    await conn.execute(text("ANALYZE memory_claims"))
    await conn.execute(text("ANALYZE memory_documents"))
    await conn.execute(text("ANALYZE m013_fts"))
    return schema


FTS_SQL = {
    # `plainto_tsquery` ANDs every token the configuration does not discard.
    # Under 'simple' that means stopwords survive, so a natural question requires
    # the claim to contain "what" AND "is" AND "the" -- and matches nothing. That is
    # a property of the configuration, not of the data, and it is why the simple arm
    # returns zero candidates. 'english' discards stopwords and stems, which is the
    # configuration that actually works on question text.
    f"fts_{config}": f"""
        SELECT f.key, ts_rank_cd(
                   to_tsvector('{config}', f.fts_text),
                   plainto_tsquery('{config}', :q)) AS rank
        FROM m013_fts f
        WHERE f.corpus_id = :c
          AND to_tsvector('{config}', f.fts_text)
              @@ plainto_tsquery('{config}', :q)
        ORDER BY rank DESC, f.key ASC
    """
    for config in ("simple", "english")
}


async def main(dataset: str) -> dict:
    if dataset == "dev":
        from m0115_dataset import build_questions
    else:
        from m0130_heldout import build_questions
    questions = [q for q in build_questions() if q.key]

    from api.services.corpora import corpus_id_for_name
    from api.services.memory_relevance import STOP, terms

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    corpus_name = f"m013-fts-{dataset}"
    corpus_id = corpus_id_for_name(corpus_name)
    results = {
        arm: {"r1": [], "r5": [], "r10": [], "mrr": [], "ndcg5": [], "cand": [], "ms": []}
        for arm in ARMS
    }
    # The shipped Python lexical arm, for a same-process comparison.
    results["python_lexical"] = {"r1": [], "r5": [], "r10": [], "mrr": [], "ndcg5": [], "cand": []}
    index_bytes = None

    async with engine.connect() as conn:
        # Ingest commits per document (the supersession triggers queue events), so
        # DDL cannot run inside a caller transaction. The schema is dropped
        # explicitly afterwards instead of being rolled back.
        schema_name = await setup(conn, corpus_id)
        # _session() opens a SAVEPOINT, which requires an active transaction.
        # setup() ends with a COMMIT, so autobegin left the connection idle-but-open.
        await conn.rollback()
        outer = await conn.begin()
        try:
            async with _session(conn) as db:
                sizes = await db.execute(
                    text("SELECT pg_size_pretty(pg_relation_size('ix_m013_fts'))")
                )
                index_bytes = sizes.scalar()
                rows = await db.execute(
                    text("SELECT key, claim, value::text FROM memory_claims WHERE corpus_id = :c"),
                    {"c": corpus_id},
                )
                claim_rows = {r[0]: f"{r[1]} {r[0]} {r[2] or ''}" for r in rows}

            for q in questions:
                for arm in ARMS:
                    import time as _t

                    # Warm once so plan caching is not charged to the first
                    # sample, then time a second identical execution.
                    async with _session(conn) as db:
                        await db.execute(text(FTS_SQL[arm]), {"c": corpus_id, "q": q.text})
                    _t0 = _t.perf_counter()
                    async with _session(conn) as db:
                        res = await db.execute(text(FTS_SQL[arm]), {"c": corpus_id, "q": q.text})
                        ranked = [r[0] for r in res]
                    results[arm]["ms"].append((_t.perf_counter() - _t0) * 1000)

                    gold = q.key
                    bucket = results[arm]
                    bucket["cand"].append(len(ranked))
                    bucket["r1"].append(1.0 if gold in ranked[:1] else 0.0)
                    bucket["r5"].append(1.0 if gold in ranked[:5] else 0.0)
                    bucket["r10"].append(1.0 if gold in ranked[:10] else 0.0)
                    bucket["mrr"].append(
                        next((1.0 / i for i, k in enumerate(ranked, 1) if k == gold), 0.0)
                    )
                    gains = [1.0 if k == gold else 0.0 for k in ranked[:5]]
                    dcg = sum(g / math.log2(i + 1) for i, g in enumerate(gains, 1))
                    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, 6))
                    bucket["ndcg5"].append(dcg / ideal if ideal else 0.0)

                # Python lexical, scored identically for a like-for-like row.
                qt = {t for t in terms(q.text)}
                qt = {t for t in qt if t not in STOP}
                scored = []
                for key, txt in claim_rows.items():
                    ct = {t for t in terms(txt)}
                    ct = {t for t in ct if t not in STOP}
                    scored.append((key, len(qt & ct) / len(qt) if qt else 0.0))
                ranked = [k for k, _ in sorted(scored, key=lambda kv: (-kv[1], kv[0]))]
                gold = q.key
                bucket = results["python_lexical"]
                bucket["cand"].append(len(ranked))
                bucket["r1"].append(1.0 if gold in ranked[:1] else 0.0)
                bucket["r5"].append(1.0 if gold in ranked[:5] else 0.0)
                bucket["r10"].append(1.0 if gold in ranked[:10] else 0.0)
                bucket["mrr"].append(
                    next((1.0 / i for i, k in enumerate(ranked, 1) if k == gold), 0.0)
                )
                gains = [1.0 if k == gold else 0.0 for k in ranked[:5]]
                dcg = sum(g / math.log2(i + 1) for i, g in enumerate(gains, 1))
                ideal = sum(1.0 / math.log2(i + 1) for i in range(1, 6))
                bucket["ndcg5"].append(dcg / ideal if ideal else 0.0)
        finally:
            await outer.rollback()
            if schema_name:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                await conn.execute(text("COMMIT"))
    await engine.dispose()

    table = {}
    for arm, bucket in results.items():
        entry = {}
        for metric in ("r1", "r5", "r10", "mrr", "ndcg5"):
            if bucket[metric]:
                entry[metric] = round(sum(bucket[metric]) / len(bucket[metric]), 4)
                entry[f"{metric}_ci95"] = list(bootstrap_ci(bucket[metric]))
        if bucket["cand"]:
            entry["mean_candidates"] = round(sum(bucket["cand"]) / len(bucket["cand"]), 2)
        if bucket.get("ms"):
            ordered = sorted(bucket["ms"])
            entry["p50_ms"] = round(ordered[len(ordered) // 2], 3)
        table[arm] = entry

    return {
        "dataset": dataset,
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "python": platform.python_version(),
        "keyed_questions": len(questions),
        "index_size": index_bytes,
        "indexed_columns": ["key", "claim", "value::text", "path"],
        "note": (
            "FTS arm indexes key+claim+value+path because the identifier a question "
            "asks about (2024-03, tier-0) lives in the key and path, not the claim "
            "sentence. Candidate generation only; returns ranked keys."
        ),
        "table": table,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dev", choices=("dev", "heldout"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = asyncio.run(main(args.dataset))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    print(
        print(
            f"\n{'arm':<16} {'R@1':>7} {'R@5':>7} {'R@10':>7} "
            f"{'MRR':>7} {'nDCG5':>7} {'cand':>7} {'p50ms':>8}"
        )
    )
    for arm, row in result["table"].items():
        print(
            f"{arm:<16} {row['r1']:>7.3f} {row['r5']:>7.3f} {row['r10']:>7.3f} "
            f"{row['mrr']:>7.3f} {row['ndcg5']:>7.3f} "
            f"{str(row.get('mean_candidates', '-')):>7} {str(row.get('p50_ms', '-')):>8}"
        )
    print(f"\nindex size: {result['index_size']}")
    print(f"written: {args.out}")
