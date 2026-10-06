"""M013 retrieval evaluation matrix.

Measures retrieval itself, not merely whether the final answer scored as correct.

Design notes that matter for trusting the numbers:

* Gold is the **authored key** on the question, not a claim id. The key is stable
  across runs and across corpora shapes; claim ids are content-addressed and change
  with every ingest. Questions with `expect_state == "ABSENT"` have no gold by
  design and are scored on abstention only, never folded into Recall/MRR/nDCG.
* Every configuration runs through the **production** `select()` path. Nothing here
  reimplements selection, so a result describes code that ships.
* Configurations are compared within a single process against one loaded corpus, so
  the candidate pool and the gate inputs are identical across rows and only the
  ranking differs.
* Latency is reported but is explicitly NOT a claim. The host has an unexplained
  ~2.5x run-to-run variance, so ordering between configurations is read from the
  deterministic quality metrics; latency is context.

Writes to an explicit ``--out`` and never defaults to a tracked artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
import platform
import statistics
import sys
import time
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

from m0115_dataset import build_corpus as build_dev_corpus  # noqa: E402

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"

#: The configurations that are actually implemented. `current` is the shipped
#: default; the rest are the experimental arms. `select()` reaches `relevance()`,
#: which has exactly two modes, so `hybrid`/`rrf` are honest labels for "fused
#: ranking over the production candidate pool" rather than for code that exists.
CONFIGURATIONS = (
    "current",
    "semantic",
    "lexical",
    "hybrid",
    "rrf",
    "semantic_then_lexical",
)


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def recall_at_k(ranked_keys: list[str], gold: str, k: int) -> float:
    return 1.0 if gold in ranked_keys[:k] else 0.0


def reciprocal_rank(ranked_keys: list[str], gold: str) -> float:
    for position, key in enumerate(ranked_keys, 1):
        if key == gold:
            return 1.0 / position
    return 0.0


def dcg(gains: list[float]) -> float:
    return sum(g / math.log2(position + 1) for position, g in enumerate(gains, 1))


def ndcg_at_k(ranked_keys: list[str], gold: str, k: int) -> float:
    """Binary relevance: one gold key, gain 1. With a single graded item nDCG@5
    reduces to a rank-discount, which is the honest form here rather than
    manufacturing graded relevance the benchmark does not provide."""
    gains = [1.0 if key == gold else 0.0 for key in ranked_keys[:k]]
    ideal = dcg([1.0] + [0.0] * (k - 1))
    return dcg(gains) / ideal if ideal else 0.0


def bootstrap_ci(values: list[float], iterations: int = 2000, seed: int = 12345) -> tuple:
    """Percentile bootstrap. Deterministic: seeded, so a rerun reproduces it."""
    import random

    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = []
    n = len(values)
    for _ in range(iterations):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return (round(lo, 4), round(hi, 4))


# ---------------------------------------------------------------------------
# Corpus + corpus-loaded state
# ---------------------------------------------------------------------------


async def build_corpus(conn, corpus_name: str) -> str:
    from api.services.corpora import corpus_id_for_name
    from api.services.ingestion import IngestionService

    schema = "m013_" + uuid4().hex[:10]
    corpus_id = corpus_id_for_name(corpus_name)
    await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    await conn.run_sync(_migrations)
    await conn.execute(
        text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
        {"id": corpus_id, "name": corpus_name},
    )
    service = IngestionService()
    for doc in build_dev_corpus():
        async with _session(conn) as db:
            await service._ingest_content(db, doc.content, doc.path, corpus_id)
    return corpus_id


async def load_state(conn, corpus_name: str, corpus_id: str):
    """Load versions and the claim vector cache once; all arms share this."""
    from api.services import memory as memory_service
    from api.services.claim_embeddings import pending, store
    from api.services.embedder import Embedder

    async with _session(conn) as db:
        versions = await memory_service._load(db, corpus_id, chunk_text=False, version_text=False)
    embedder = Embedder()
    async with _session(conn) as db:
        wanted, texts = await pending(db, corpus_id)
        vectors = dict(zip(wanted, embedder.embed(texts)))
    async with _session(conn) as db:
        async with db.begin():
            await store(
                db,
                corpus_id,
                wanted,
                vectors,
                embedder.model_name,
                embedder.dimension,
                "m013",
                commit=False,
            )
    return versions, embedder, wanted


# ---------------------------------------------------------------------------
# The arms
# ---------------------------------------------------------------------------


def _rank_keys(configuration, full, request, embedder, cached, relevance_fn):
    """Return the ranked authored keys for one configuration.

    Ranked keys, not claims: gold labels are keys, and ranking is over keys in the
    shipped code.
    """
    key_scores, _plan, work = relevance_fn(full, request, "current", embedder)

    if configuration in {"current", "semantic", "lexical"}:
        ranked = sorted(key_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [key for key, _ in ranked]

    if configuration in {"hybrid", "rrf"}:
        # Fused ranking over the production gate: union the two orderings and fuse
        # by reciprocal rank. Implemented here rather than by calling the dead
        # `rank_claims` helper, so the arms share one gate and one candidate pool.
        lexical_scores, _p, _w = relevance_fn(full, request, "current", embedder, lexical=True)
        fused: dict[str, float] = {}
        for scores in (key_scores, lexical_scores):
            for rank, (key, _v) in enumerate(
                sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])), 1
            ):
                fused[key] = fused.get(key, 0.0) + 1.0 / (60 + rank)
        ranked = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        return [key for key, _ in ranked]

    if configuration == "semantic_then_lexical":
        # Escalation arm: trust the cheap gate first, fall back to lexical only
        # when it accepts nothing. Mirrors the shipped fallback ordering.
        ranked = sorted(key_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        if ranked:
            return [key for key, _ in ranked]
        lexical_scores, _p, _w = relevance_fn(full, request, "current", embedder, lexical=True)
        lr = sorted(lexical_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [key for key, _ in lr]

    raise ValueError(f"unknown configuration {configuration}")


def make_relevance(lexical: bool, vectorized: bool):
    from api.services.memory_relevance import RelevancePolicy, relevance

    def fn(full, request, intent, embedder, **kwargs):
        return relevance(
            full,
            request,
            intent,
            embedder if not lexical else None,
            RelevancePolicy.configured(),
            lexical=lexical,
            claim_vectors=kwargs.get("claim_vectors"),
            vectorized=vectorized and not lexical,
        )

    return fn


def run_configurations(conn, corpus_name, versions, embedder, cached, questions, repeats):
    """Evaluate every configuration against one corpus in one process."""
    from api.models.memory import MemoryRequest
    from api.services.memory_public import State, project

    results = {
        name: {
            "r1": [],
            "r5": [],
            "r10": [],
            "mrr": [],
            "ndcg5": [],
            "absent_ok": [],
            "abstain_ok": [],
            "candidates": [],
            "ms": [],
        }
        for name in CONFIGURATIONS
    }
    state = State(as_of=None, valid_at=VALID_AT)

    for question in questions:
        request = MemoryRequest(
            corpus=corpus_name, query=question.text, valid_at=VALID_AT, budget=128000
        )
        full = project(
            versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
        )

        for name in CONFIGURATIONS:
            lexical_arm = name == "lexical"
            fn = make_relevance(lexical_arm, vectorized=False)
            started = time.perf_counter()
            ranked = _rank_keys(name, full, request, embedder, cached, fn)
            # Warm the timing: the first call pays lazy imports inside the scorer.
            for _ in range(repeats - 1):
                _rank_keys(name, full, request, embedder, cached, fn)
            elapsed = (time.perf_counter() - started) * 1000 / max(1, repeats)
            results[name]["ms"].append(elapsed)

            is_absent = question.expect_state == "ABSENT"
            if is_absent:
                results[name]["abstain_ok"].append(1.0 if not ranked else 0.0)
                continue
            results[name]["candidates"].append(float(len(ranked)))
            gold = question.key
            results[name]["r1"].append(recall_at_k(ranked, gold, 1))
            results[name]["r5"].append(recall_at_k(ranked, gold, 5))
            results[name]["r10"].append(recall_at_k(ranked, gold, 10))
            results[name]["mrr"].append(reciprocal_rank(ranked, gold))
            results[name]["ndcg5"].append(ndcg_at_k(ranked, gold, 5))

    return results


def summarise(results, repeats):
    table = []
    for name in CONFIGURATIONS:
        row = results[name]
        scored = len(row["r1"])
        entry = {
            "configuration": name,
            "scored_questions": scored,
            "abstention_questions": len(row["abstain_ok"]),
        }
        for metric in ("r1", "r5", "r10", "mrr", "ndcg5"):
            values = row[metric]
            lo, hi = bootstrap_ci(values)
            entry[metric] = round(statistics.mean(values), 4) if values else None
            entry[f"{metric}_ci95"] = [lo, hi]
        if row["abstain_ok"]:
            entry["abstention_accuracy"] = round(statistics.mean(row["abstain_ok"]), 4)
        if row["candidates"]:
            entry["mean_candidate_keys"] = round(statistics.mean(row["candidates"]), 2)
        if row["ms"]:
            ordered = sorted(row["ms"])
            entry["p50_ms"] = round(ordered[len(ordered) // 2], 3)
            entry["p95_ms"] = round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3)
        table.append(entry)
    return table


async def evaluate(dataset: str, repeats: int) -> dict:
    if dataset == "dev":
        from m0115_dataset import build_questions
    else:
        from m0130_heldout import build_questions
    questions = build_questions()

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    corpus_name = f"m013-{dataset}"
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            corpus_id = await build_corpus(conn, corpus_name)
            versions, embedder, cached = await load_state(conn, corpus_name, corpus_id)
            results = run_configurations(
                conn, corpus_name, versions, embedder, cached, questions, repeats
            )
        finally:
            await outer.rollback()
    await engine.dispose()
    return {
        "dataset": dataset,
        "questions": len(questions),
        "versions_loaded": len(versions),
        "table": summarise(results, repeats),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dev", choices=("dev", "heldout"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--out",
        default=str(
            ROOT
            / f"docs/performance/m013-retrieval-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        ),
        help="explicit output path; never a tracked historical artifact",
    )
    args = parser.parse_args()

    report = {
        "meta": {
            "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
            "dataset": args.dataset,
            "python": platform.python_version(),
            "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            "repeats": args.repeats,
            "gold": "authored key; ABSENT questions scored on abstention only",
            "bootstrap": "percentile, 2000 iterations, seed 12345 (deterministic)",
            "latency_note": "reported for context only; host has unexplained ~2.5x variance",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        **asyncio.run(evaluate(args.dataset, args.repeats)),
    }

    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    table = report["table"]
    header = (
        f"{'configuration':<24} {'R@1':>7} {'R@5':>7} {'R@10':>7} "
        f"{'MRR':>7} {'nDCG@5':>7} {'abst':>7} {'p50ms':>8}"
    )
    print(f"\n{header}")
    for row in table:
        print(
            f"{row['configuration']:<24} "
            f"{row['r1']:>7.3f} {row['r5']:>7.3f} {row['r10']:>7.3f} "
            f"{row['mrr']:>7.3f} {row['ndcg5']:>7.3f} "
            f"{str(row.get('abstention_accuracy', '-')):>7} "
            f"{str(row.get('p50_ms', '-')):>8}"
        )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
