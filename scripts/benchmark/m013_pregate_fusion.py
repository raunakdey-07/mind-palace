"""M013: does pre-gate fusion actually recover what the diagnostic says is available?

The diagnostic established *headroom*: on 16 of 22 dev semantic misses, the gold
key was present in the lexical gate's output but pruned by the semantic gate. It
explicitly did not establish that fusion achieves it. This tests achievability.

The mechanism difference from the inert matrix rows: fuse the two gates' candidate
sets BEFORE the relevance cut, then rank the union. Concretely, a key is admitted
if either gate would have admitted it, and the fused score orders them. That is
the only change under test.

Authority safety: this selects which authored KEYS are returned as candidates.
It never touches claim status, validity, supersession, conflicts or evidence --
those are decided by the persistence projection before any of this runs. Every arm
here is compared on gold-key recall, and the 202-pack equivalence gate is the
separate check that nothing authoritative moved.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import random
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


def bootstrap_ci(values: list[float], iterations: int = 2000, seed: int = 12345):
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return (
        round(means[int(0.025 * len(means))], 4),
        round(means[min(len(means) - 1, int(0.975 * len(means)))], 4),
    )


def rrf_fuse(orderings: list[list[str]], k: int) -> list[str]:
    """Reciprocal rank fusion over already-ordered candidate lists."""
    scores: dict[str, float] = {}
    for ordering in orderings:
        for rank, key in enumerate(ordering, 1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return [key for key, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def order(scores: dict[str, float]) -> list[str]:
    return [k for k, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


async def main(dataset: str, k_values: tuple[int, ...]) -> dict:
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
    corpus_name = f"m013-pre-{dataset}"
    corpus_id = corpus_id_for_name(corpus_name)
    per_question = []
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema = "m013pre_" + uuid4().hex[:10]
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
                t0 = time.perf_counter()
                sem, _p, _w = relevance(full, request, "current", embedder, policy)
                sem_ms = (time.perf_counter() - t0) * 1000
                t0 = time.perf_counter()
                lex, _p2, _w2 = relevance(full, request, "current", None, policy, lexical=True)
                lex_ms = (time.perf_counter() - t0) * 1000
                per_question.append(
                    {
                        "qid": q.qid,
                        "gold": q.key,
                        "sem": dict(sem),
                        "lex": dict(lex),
                        "sem_ms": sem_ms,
                        "lex_ms": lex_ms,
                    }
                )
        finally:
            await outer.rollback()
    await engine.dispose()

    def score_arm(get_ranked) -> dict:
        arms = {"r1": [], "r5": [], "r10": [], "mrr": [], "ndcg5": [], "cand": []}
        for row in per_question:
            ranked = get_ranked(row)
            gold = row["gold"]
            arms["cand"].append(len(ranked))
            arms["r1"].append(1.0 if gold in ranked[:1] else 0.0)
            arms["r5"].append(1.0 if gold in ranked[:5] else 0.0)
            arms["r10"].append(1.0 if gold in ranked[:10] else 0.0)
            arms["mrr"].append(next((1.0 / i for i, k in enumerate(ranked, 1) if k == gold), 0.0))
            gains = [1.0 if k == gold else 0.0 for k in ranked[:5]]
            import math

            dcg = sum(g / math.log2(i + 1) for i, g in enumerate(gains, 1))
            ideal = sum(1.0 / math.log2(i + 1) for i in range(1, 6))
            arms["ndcg5"].append(dcg / ideal if ideal else 0.0)
        out = {}
        for metric in ("r1", "r5", "r10", "mrr", "ndcg5"):
            lo, hi = bootstrap_ci(arms[metric])
            out[metric] = round(sum(arms[metric]) / len(arms[metric]), 4)
            out[f"{metric}_ci95"] = [lo, hi]
        out["mean_candidates"] = round(sum(arms["cand"]) / len(arms["cand"]), 2)
        return out

    table = {
        "semantic_only": score_arm(lambda r: order(r["sem"])),
        "lexical_only": score_arm(lambda r: order(r["lex"])),
        # Pre-gate union: admit a key if EITHER gate would have, then rank by RRF.
        "union_concat": score_arm(lambda r: rrf_fuse([order(r["sem"]), order(r["lex"])], 60)),
    }
    for k in k_values:
        table[f"union_rrf_k{k}"] = score_arm(
            lambda r, kk=k: rrf_fuse([order(r["sem"]), order(r["lex"])], kk)
        )

    # Did pre-gate fusion recover the specific misses the diagnostic found?
    recovered = [
        r["qid"] for r in per_question if r["gold"] not in r["sem"] and r["gold"] in r["lex"]
    ]
    still_missing = [
        r["qid"] for r in per_question if r["gold"] not in r["sem"] and r["gold"] not in r["lex"]
    ]
    return {
        "dataset": dataset,
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "python": platform.python_version(),
        "keyed_questions": len(per_question),
        "rrf_k_values": list(k_values),
        "note": (
            "union_* arms admit a key if EITHER gate would have, then rank by RRF. "
            "This is the change under test; semantic_only is the shipped behaviour."
        ),
        "table": table,
        "gold_in_lexical_but_not_semantic": sorted(recovered),
        "gold_in_neither_gate": sorted(still_missing),
        "recoverable_count": len(recovered),
        "unrecoverable_count": len(still_missing),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dev", choices=("dev", "heldout"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--rrf-k", default="10,60")
    args = parser.parse_args()
    ks = tuple(int(v) for v in args.rrf_k.split(","))
    result = asyncio.run(main(args.dataset, ks))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    print(f"\n{'arm':<22} {'R@1':>7} {'R@5':>7} {'R@10':>7} {'MRR':>7} {'nDCG5':>7} {'cand':>6}")
    for name, row in result["table"].items():
        print(
            f"{name:<22} {row['r1']:>7.3f} {row['r5']:>7.3f} {row['r10']:>7.3f} "
            f"{row['mrr']:>7.3f} {row['ndcg5']:>7.3f} {row['mean_candidates']:>6.2f}"
        )
    print(f"\nrecoverable_by_union: {result['recoverable_count']}")
    print(f"gold_in_neither_gate: {result['unrecoverable_count']}")
    print(f"written: {args.out}")
