"""M013: apply the measured tokenizer fix and score frozen held-out v2.

Run ONCE against the frozen set. The configuration was committed before this
executed, so nothing here is tuned against the result.

The candidate is the gated numeric-identifier tokenizer measured on v1:
  * word tokens exactly as shipped
  * numeric/version tokens kept whole ("0.6", "2024", "07")
  * an identifier is dropped from the denominator unless it appears on BOTH sides

Reports per-question rescue/regression so every difference is classified rather
than summarised away.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import random
import re
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

SHIP = re.compile(r"[a-z][a-z0-9]+")
CAND = re.compile(r"[a-z][a-z0-9]+|\d+(?:\.\d+)?")
IDENT = re.compile(r"\d")


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


def tokenize(text_value: str, pattern: re.Pattern, stop: frozenset) -> tuple[set, set]:
    out = set()
    for word in pattern.findall(text_value.casefold()):
        if word in stop:
            continue
        for suffix in ("ation", "ing", "ed", "s"):
            if word.endswith(suffix) and len(word) > len(suffix) + 3:
                word = word[: -len(suffix)]
                break
        out.add(word)
    return out, {w for w in out if IDENT.search(w)}


def gated(all_terms: set, ident: set, other_ident: set) -> set:
    return {t for t in all_terms if t not in ident or t in other_ident}


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


async def main(dataset: str, floor: float) -> dict:
    if dataset == "dev":
        from m0115_dataset import build_questions
    else:
        from m013_heldout_v2 import build_questions
    questions = build_questions()

    from api.models.memory import MemoryRequest
    from api.services import memory as memory_service
    from api.services.corpora import corpus_id_for_name
    from api.services.embedder import Embedder
    from api.services.ingestion import IngestionService
    from api.services.memory_public import State, project
    from api.services.memory_relevance import STOP, RelevancePolicy, relevance

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    corpus_name = f"m013-fix-{dataset}"
    corpus_id = corpus_id_for_name(corpus_name)
    rows = []

    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema = "fix_" + uuid4().hex[:10]
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrations)
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": corpus_name},
            )
            service = IngestionService()
            from m0115_dataset import build_corpus

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
                claims = {}
                for group in (
                    full.current_memories,
                    full.historical_memories,
                    full.uncertain_memories,
                ):
                    for c in group:
                        claims[c.key] = c
                for g in full.conflicts:
                    for c in g.claims:
                        claims.setdefault(c.key, c)

                # Shipped semantic gate (unchanged in both arms).
                sem, _p, _w = relevance(full, request, "current", embedder, policy)
                sem_ranked = [k for k, _ in sorted(sem.items(), key=lambda kv: (-kv[1], kv[0]))]

                # Two lexical tokenizers over identical claim text.
                def lexical(mode):
                    scored = []
                    for key, claim in claims.items():
                        blob = f"{claim.claim} {claim.key} {claim.value} {claim.path}"
                        if mode == "shipped":
                            qt, _ = tokenize(q.text, SHIP, STOP)
                            ct, _ = tokenize(blob, SHIP, STOP)
                        else:
                            qa, qi = tokenize(q.text, CAND, STOP)
                            ca, ci = tokenize(blob, CAND, STOP)
                            qt = gated(qa, qi, ci)
                            ct = gated(ca, ci, qi)
                        scored.append((key, len(qt & ct) / len(qt) if qt else 0.0))
                    scored = [kv for kv in scored if kv[1] >= floor]
                    return [k for k, _ in sorted(scored, key=lambda kv: (-kv[1], kv[0]))]

                ship_lex = lexical("shipped")
                cand_lex = lexical("candidate")
                gold = q.key
                rows.append(
                    {
                        "qid": q.qid,
                        "category": q.category,
                        "text": q.text,
                        "gold_key": gold,
                        "expect_state": q.expect_state,
                        "semantic_reachable": gold in sem_ranked if gold else None,
                        "shipped_lex_reachable": gold in ship_lex if gold else None,
                        "candidate_lex_reachable": gold in cand_lex if gold else None,
                        "shipped_lex_ranked": ship_lex,
                        "candidate_lex_ranked": cand_lex,
                    }
                )
        finally:
            await outer.rollback()
    await engine.dispose()

    def metrics(key):
        vals = [1.0 if r[key] else 0.0 for r in rows if r["gold_key"]]
        return {"point": round(sum(vals) / len(vals), 4), "ci95": list(bootstrap_ci(vals))}

    rescued = [
        r["qid"]
        for r in rows
        if r["gold_key"] and not r["shipped_lex_reachable"] and r["candidate_lex_reachable"]
    ]
    regressed = [
        r["qid"]
        for r in rows
        if r["gold_key"] and r["shipped_lex_reachable"] and not r["candidate_lex_reachable"]
    ]
    return {
        "dataset": dataset,
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "python": platform.python_version(),
        "questions": len(questions),
        "lexical_floor": floor,
        "shipped_lexical_recall": metrics("shipped_lex_reachable"),
        "candidate_lexical_recall": metrics("candidate_lex_reachable"),
        "semantic_recall": metrics("semantic_reachable"),
        "rescued": rescued,
        "regressed": regressed,
        "rescued_count": len(rescued),
        "regressed_count": len(regressed),
        "per_question": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dev", choices=("dev", "heldout"))
    parser.add_argument("--floor", type=float, default=0.30)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = asyncio.run(main(args.dataset, args.floor))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"dataset={result['dataset']} questions={result['questions']}")
    print(
        f"  shipped  lexical recall: {result['shipped_lexical_recall']['point']:.4f} "
        f"CI {result['shipped_lexical_recall']['ci95']}"
    )
    print(
        f"  candidate lexical recall: {result['candidate_lexical_recall']['point']:.4f} "
        f"CI {result['candidate_lexical_recall']['ci95']}"
    )
    print(f"  semantic recall        : {result['semantic_recall']['point']:.4f}")
    print(f"  rescued={result['rescued_count']} regressed={result['regressed_count']}")
    print(f"  rescued: {result['rescued']}")
    print(f"  regressed: {result['regressed']}")
    print(f"written: {args.out}")
