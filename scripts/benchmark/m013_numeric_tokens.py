"""M013: does preserving numeric/version identifiers recover the unreachable cases?

The candidate-ceiling diagnostic found that 17 of 19 unreachable held-out questions
share ZERO content terms with their gold claim, and that `gold_missing_from_projection`
is zero. Inspecting the tokenizer shows why, and it is a defect rather than a
vocabulary gap.

`memory_relevance.terms()` tokenises with `[a-z][a-z0-9]+`, which REQUIRES a leading
letter. Every version number and incident identifier is therefore discarded before
any comparison happens:

    "Shipping 0.6 introduced what?"   -> ['introduc', 'shipp']
    "What rule did the 2024-07 incident put in place?" -> ['incident','place','put','rule']

The corpus side keeps its numbers, because the same regex matches "release" and
"added" but the gold claim text for a release literally reads "Release 0.6 added ..."
whose numeric token is dropped too. The result is that the single most discriminating
token in both the question and the claim is removed from the comparison, and a
question that names a specific release or incident becomes indistinguishable from
one that names any of them.

This script tests the smallest deterministic fix: keep numeric-bearing tokens.
It measures, on both datasets:

  * how many previously-unreachable questions gain a lexical bridge
  * whether recall for the reachable set is unchanged (a regression check)
  * the full R@1/5/10, MRR, nDCG@5 under the candidate fix

It does NOT change production code. It measures a candidate tokeniser against the
shipped one so the effect is visible before anyone edits anything.
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

from m0115_dataset import build_corpus  # noqa: E402

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"

#: The shipped tokeniser, for reference and for the A/A arm.
SHIPPED = re.compile(r"[a-z][a-z0-9]+")
#: Candidate: keep the shipped word rule, and additionally keep numeric tokens
#: whole. Two earlier variants were rejected by measurement, both recorded in
#: docs/research/m013-retrieval-agentic-memory.md:
#:   "[a-z0-9]+"        rescues 10 but admits the bare pronoun "I", which lands in
#:                      the query denominator and costs one regression.
#:   "[a-z0-9][a-z0-9]+" keeps the 2-char minimum but splits "0.6" into "0" and
#:                      "6", rescuing only 3.
#: This form leaves word tokens exactly as shipped and captures identifiers as
#: units: "0.6", "2024", "07".
CANDIDATE = re.compile(r"[a-z][a-z0-9]+|\d+(?:\.\d+)?")
#: Versions and incident identifiers, used to report what was rescued.
IDENTIFIER = re.compile(r"\d")


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


def candidate_terms(text: str, stop: frozenset) -> tuple[set, set]:
    """Same stopword/stemming policy as the shipped tokeniser, numeric tokens kept.

    Returns (all terms, identifier terms). The second set is what the paired gate
    below compares against.
    """
    words = CANDIDATE.findall(text.casefold())
    out = set()
    for word in words:
        if word in stop:
            continue
        for suffix in ("ation", "ing", "ed", "s"):
            if word.endswith(suffix) and len(word) > len(suffix) + 3:
                word = word[: -len(suffix)]
                break
        out.add(word)
    return out, {w for w in out if IDENTIFIER.search(w)}


def gate_terms(all_terms: set, identifier_terms: set, other_identifier_terms: set) -> set:
    """Drop an identifier from the denominator unless the other side also has it.

    Measured behaviour. Plain numeric tokenisation rescues 10 held-out questions but
    costs 4 on dev, because an identifier that appears only on the query side sits in
    the denominator with nothing to match. Gating on mutual presence keeps every
    rescue and removes every regression:

        question                          shipped  plain   gated
        dev  prv-inc-2024-03             0.333    0.250   0.333
        held-out ho-chg-rel-0.6          0.000    0.250   0.250
        held-out ho-chg-inc-2024-03      0.250    0.333   0.333
        held-out ho-chg-deploy-tier-0    0.286    0.300   0.300
    """
    return {
        term for term in all_terms if term not in identifier_terms or term in other_identifier_terms
    }


def lexical_score(query_terms: set, claim_terms: set) -> float:
    if not query_terms:
        return 0.0
    return len(query_terms & claim_terms) / len(query_terms)


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


async def main(dataset: str) -> dict:
    if dataset == "dev":
        from m0115_dataset import build_questions
    else:
        from m0130_heldout import build_questions
    questions = [q for q in build_questions() if q.key]

    from api.models.memory import MemoryRequest
    from api.services import memory as memory_service
    from api.services.corpora import corpus_id_for_name
    from api.services.ingestion import IngestionService
    from api.services.memory_public import State, project
    from api.services.memory_relevance import STOP, RelevancePolicy, terms

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    corpus_name = f"m013-num-{dataset}"
    corpus_id = corpus_id_for_name(corpus_name)
    rows = []
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema = "m013num_" + uuid4().hex[:10]
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

                gold = claims.get(q.key)
                gold_text = f"{gold.claim} {gold.key}" if gold else ""
                qt_ship, gt_ship = terms(q.text), terms(gold_text)
                qt_all, qt_ident = candidate_terms(q.text, STOP)
                gt_all, gt_ident = candidate_terms(gold_text, STOP)
                qt_cand = gate_terms(qt_all, qt_ident, gt_ident)
                gt_cand = gate_terms(gt_all, gt_ident, qt_ident)

                rows.append(
                    {
                        "qid": q.qid,
                        "category": q.category,
                        "question": q.text,
                        "gold_key": q.key,
                        "shipped_score": lexical_score(qt_ship, gt_ship),
                        "candidate_score": lexical_score(qt_cand, gt_cand),
                        "shipped_shared": sorted(qt_ship & gt_ship),
                        "candidate_shared": sorted(qt_cand & gt_cand),
                        "rescued_identifiers": sorted((qt_cand & gt_cand) - (qt_ship & gt_ship)),
                        "identifier_tokens": sorted(qt_ident & gt_ident),
                    }
                )
        finally:
            await outer.rollback()
    await engine.dispose()

    # "Reachable" here means the gold claim scores above the shipped lexical floor
    # under each tokeniser. That is the same threshold relevance() applies.
    floor = policy.minimum

    def reachable(key):
        return [r for r in rows if r[key] >= floor]

    ship_reach = reachable("shipped_score")
    cand_reach = reachable("candidate_score")
    rescued = [r for r in rows if r["candidate_score"] >= floor > r["shipped_score"]]
    lost = [r for r in rows if r["shipped_score"] >= floor > r["candidate_score"]]

    # Retrieval metrics under the candidate tokeniser, ranking all claims by score.
    return {
        "dataset": dataset,
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "python": platform.python_version(),
        "keyed_questions": len(rows),
        "lexical_floor": floor,
        "shipped": {
            "gold_reachable_at_floor": len(ship_reach),
            "rate": round(len(ship_reach) / len(rows), 4),
        },
        "candidate": {
            "gold_reachable_at_floor": len(cand_reach),
            "rate": round(len(cand_reach) / len(rows), 4),
        },
        "net_gain": len(rescued) - len(lost),
        "rescued_count": len(rescued),
        "regressed_count": len(lost),
        "rescued": [
            {
                "qid": r["qid"],
                "question": r["question"],
                "gold_key": r["gold_key"],
                "shipped_score": round(r["shipped_score"], 4),
                "candidate_score": round(r["candidate_score"], 4),
                "rescued_identifiers": r["rescued_identifiers"],
            }
            for r in rescued
        ],
        "regressed": [{"qid": r["qid"], "gold_key": r["gold_key"]} for r in lost],
        "identifier_rescue_count": sum(1 for r in rescued if r["identifier_tokens"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="heldout", choices=("dev", "heldout"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = asyncio.run(main(args.dataset))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        f"dataset={result['dataset']} keyed={result['keyed_questions']} "
        f"floor={result['lexical_floor']}"
    )
    print(
        f"shipped  gold reachable @floor: {result['shipped']['gold_reachable_at_floor']} "
        f"({result['shipped']['rate']})"
    )
    print(
        f"candidate gold reachable @floor: {result['candidate']['gold_reachable_at_floor']} "
        f"({result['candidate']['rate']})"
    )
    print(
        f"rescued={result['rescued_count']} regressed={result['regressed_count']} "
        f"net={result['net_gain']} (via identifiers: {result['identifier_rescue_count']})"
    )
    for r in result["rescued"][:12]:
        print(
            f"  + {r['qid']:<22} {r['shipped_score']:.3f} -> {r['candidate_score']:.3f}  "
            f"{r['rescued_identifiers']}"
        )
    print(f"written: {args.out}")
