"""M013 held-out v2: scaffolding, freeze enforcement, and the frozen runner.

Deliberately ships with ZERO questions. See docs/research/heldout-v2-protocol.md.

The tokenizer fix measured +10 rescued / 0 regressed on held-out v1, but it was
DISCOVERED by reading v1, so v1 can no longer validate it. This module exists so
that v2 is a real gate rather than a promise:

  freeze   compute and record the dataset hash; refuses an empty or invalid set
  verify   recompute the hash and fail if the set changed after freezing
  run      execute the frozen comparison exactly once, writing raw evidence

Validation is deliberately strict. A question whose gold key does not exist in the
corpus is marked UNSCORABLE rather than being given an invented label, and
`freeze` refuses while any answerable question is unverified.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
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

from m0115_dataset import Question, build_corpus  # noqa: E402
from m0115_dataset import dataset_hash as dev_dataset_hash  # noqa: E402

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"
FREEZE_PATH = ROOT / "docs/performance/heldout-v2-freeze.json"

#: ---------------------------------------------------------------------------
#: HELD-OUT v2 — authored from the corpus, protocol section 8.
#:
#: Authoring rules followed, recorded so a reviewer can audit them:
#:
#: * Every answerable question names a gold key that exists in
#:   `build_corpus()`; the gate re-verifies this and refuses otherwise.
#: * Wording is deliberately INDIRECT and avoids repeating the gold claim's own
#:   vocabulary, because v1 showed that direct wording overstates performance: dev
#:   reached a 0.9657 candidate ceiling against held-out's 0.8643 precisely because
#:   dev questions reused the authored words.
#: * Identifier questions quote identifiers that genuinely occur in the corpus
#:   (0.6/0.7/0.8, 2024-03/07/09/11, tier-0..3, the 30 and 500 limits). None were
#:   invented to favour any particular implementation.
#: * ABSENT questions ask for facts the corpus genuinely never establishes. They
#:   are not number-swaps of real claims.
#: * No retrieval benchmark was run while authoring, and no question was chosen
#:   because of how any tokenizer is expected to behave. The categories come from
#:   the frozen protocol's targets, not from results.
#:
#: `note` records the reasoning signal each question depends on.
#: ---------------------------------------------------------------------------

hv2_QUESTIONS: list[Question] = [
    # -- why_decision (5): rationale, phrased without the claim's own words ------
    Question(
        "hv2-why_decision-01",
        "why_decision",
        "The team moved off the earlier database engine. What capability drove that?",
        "decision.database.reason",
        "CURRENT",
        ("JSONB",),
        (),
        "indirect; asks for the reason without naming JSONB or advisory locks",
    ),
    Question(
        "hv2-why_decision-02",
        "why_decision",
        "Why was the old caching layer replaced rather than kept?",
        "decision.cache.reason",
        "CURRENT",
        ("TTL",),
        (),
        "indirect; 'caching layer' avoids 'shared cache'",
    ),
    Question(
        "hv2-why_decision-03",
        "why_decision",
        "What was the operational argument for changing the messaging broker?",
        "decision.queue.reason",
        "CURRENT",
        ("dead lettering",),
        (),
        "indirect; 'messaging broker' avoids 'job queue'",
    ),
    Question(
        "hv2-why_decision-04",
        "why_decision",
        "Licensing concerns influenced one technology change. Which one, and what else mattered?",
        "decision.search.reason",
        "CURRENT",
        ("licence",),
        (),
        "indirect; the licence reason is the discriminator between the six",
    ),
    Question(
        "hv2-why_decision-05",
        "why_decision",
        "The reporting store was migrated for performance reasons. What workload drove it?",
        "decision.warehouse.reason",
        "CURRENT",
        ("columnar",),
        (),
        "indirect; 'reporting store' avoids 'analytics warehouse'",
    ),
    # -- identifier (3): real corpus identifiers, exact-match demand -----------
    Question(
        "hv2-identifier-01",
        "identifier",
        "Which constraint was established after the 2024-11 incident?",
        "constraint.ledger.settlement",
        "CURRENT",
        ("idempotent",),
        (),
        "identifier demand: 2024-11 must survive tokenisation to reach the claim",
    ),
    Question(
        "hv2-identifier-02",
        "identifier",
        "What limit does tier-0 deployment impose?",
        "constraint.deploy.tier-0",
        "CURRENT",
        ("single writer",),
        (),
        "identifier demand: tier-0 must survive tokenisation",
    ),
    Question(
        "hv2-identifier-03",
        "identifier",
        "Following release 0.7, what changed?",
        "release.0.7",
        "CURRENT",
        ("ledger",),
        (),
        "identifier demand: the dotted version token 0.7 must survive",
    ),
    # -- temporal_state (4) ---------------------------------------------------
    Question(
        "hv2-temporal_state-01",
        "temporal_state",
        "Which datastore is in production today?",
        "architecture.postgres",
        "CURRENT",
        ("PostgreSQL",),
        ("MySQL",),
        "direct current-state",
    ),
    Question(
        "hv2-temporal_state-02",
        "temporal_state",
        "Name the search component currently serving queries.",
        "architecture.search",
        "CURRENT",
        ("OpenSearch",),
        ("Elasticsearch",),
        "avoids 'search engine'",
    ),
    Question(
        "hv2-temporal_state-03",
        "temporal_state",
        "What binary store backs the media pipeline at present?",
        "architecture.storage",
        "CURRENT",
        ("S3",),
        ("EBS",),
        "indirect naming; 'media pipeline' is not the authored key",
    ),
    Question(
        "hv2-temporal_state-04",
        "temporal_state",
        "Which component handles background messaging in production?",
        "architecture.queue",
        "CURRENT",
        ("RabbitMQ",),
        ("SQS",),
        "indirect naming",
    ),
    # -- version_update (4) ---------------------------------------------------
    Question(
        "hv2-version_update-01",
        "version_update",
        "What replaced the previous warehouse technology?",
        "architecture.warehouse",
        "CURRENT",
        ("ClickHouse",),
        ("Redshift",),
        "asks what replaced the prior value, not the current value alone",
    ),
    Question(
        "hv2-version_update-02",
        "version_update",
        "The object storage choice changed at some point. What is it now?",
        "architecture.storage",
        "CURRENT",
        ("S3",),
        ("EBS",),
        "temporal framing without naming the old value",
    ),
    Question(
        "hv2-version_update-03",
        "version_update",
        "Which queue technology superseded the previous one?",
        "architecture.queue",
        "CURRENT",
        ("RabbitMQ",),
        ("SQS",),
        "supersession framing",
    ),
    Question(
        "hv2-version_update-04",
        "version_update",
        "The shared cache was migrated once. Which product is used now?",
        "architecture.redis",
        "CURRENT",
        ("Redis",),
        ("Memcached",),
        "migration framing",
    ),
    # -- supersession (3) -----------------------------------------------------
    Question(
        "hv2-supersession-01",
        "supersession",
        "An ADR claims to supersede an earlier one about the edge store. "
        "What does it assert about the datastore?",
        "architecture.datastore",
        "CURRENT",
        ("SQLite",),
        (),
        "key-drift hazard: the claim sits under a different authored key",
    ),
    Question(
        "hv2-supersession-02",
        "supersession",
        "Which team is responsible for the component that escalated risk using the ledger?",
        "service.risk.owner",
        "CURRENT",
        ("data",),
        (),
        "relationship through supersession-adjacent ownership",
    ),
    Question(
        "hv2-supersession-03",
        "supersession",
        "When someone must be paged about the Archive Job, which team answers?",
        "runbook.archive.escalation",
        "CURRENT",
        ("data",),
        (),
        "escalation path for a service that is not named by its own label",
    ),
    # -- conflict (3) --------------------------------------------------------
    Question(
        "hv2-conflict-01",
        "conflict",
        "Two records disagree about the primary datastore. What are the competing values?",
        "architecture.postgres",
        "CONFLICTING",
        ("PostgreSQL", "SQLite"),
        (),
        "requires both sides of a keyed contradiction",
    ),
    Question(
        "hv2-conflict-02",
        "conflict",
        "Is there an unreconciled architectural record naming a different primary store?",
        "architecture.datastore",
        "CURRENT",
        ("SQLite",),
        (),
        "key-drift: conflicting content under a different authored key",
    ),
    Question(
        "hv2-conflict-03",
        "conflict",
        "Which documented position contradicts the datastore actually in production?",
        "architecture.postgres",
        "CONFLICTING",
        ("SQLite",),
        (),
        "asks which side is the outlier",
    ),
    # -- cross_document (3) --------------------------------------------------
    Question(
        "hv2-cross_document-01",
        "cross_document",
        "Which team owns every service that sits directly behind the API Gateway?",
        "team.platform.services",
        "CURRENT",
        ("Gateway", "Identity"),
        (),
        "requires service docs and the ownership table together",
    ),
    Question(
        "hv2-cross_document-02",
        "cross_document",
        "Name the services the data team is accountable for.",
        "team.data.services",
        "CURRENT",
        ("Analytics", "Archive", "Risk"),
        (),
        "cross-document enumeration from the ownership table",
    ),
    Question(
        "hv2-cross_document-03",
        "cross_document",
        "Which services run at tier-1?",
        "team.payments.services",
        "CURRENT",
        ("Orders", "Ledger"),
        (),
        "requires service tier facts plus team membership",
    ),
    # -- relationship (3) -----------------------------------------------------
    Question(
        "hv2-relationship-01",
        "relationship",
        "The Risk Engine relies on other components to function. Name them.",
        "service.risk.depends_on",
        "CURRENT",
        ("ledger", "gateway"),
        (),
        "dependency chain phrased as reliance",
    ),
    Question(
        "hv2-relationship-02",
        "relationship",
        "What must be present for the Media Service to operate?",
        "service.media.depends_on",
        "CURRENT",
        ("gateway", "storage"),
        (),
        "dependency phrased as an operating prerequisite",
    ),
    Question(
        "hv2-relationship-03",
        "relationship",
        "Which components does the Search Indexer need before it can run?",
        "service.search-indexer.depends_on",
        "CURRENT",
        ("queue", "search", "postgres"),
        (),
        "multi-part dependency enumeration",
    ),
    # -- negative_evidence (2): the corpus explicitly stops short -------------
    Question(
        "hv2-negative_evidence-01",
        "negative_evidence",
        "Was the gateway left unowned?",
        None,
        "ABSENT",
        (),
        (),
        "a meeting note says the gateway 'stays team-neutral', but "
        "docs/services/gateway.md authors service.gateway.owner. The corpus DOES "
        "establish an owner, so the premise is false and no answer is correct. "
        "Verified while authoring: the initial draft scored this against a "
        "wrong key and was corrected before any benchmark ran.",
    ),
    Question(
        "hv2-negative_evidence-02",
        "negative_evidence",
        "What is the sanctioned way to remove records that are no longer on disk?",
        None,
        "ABSENT",
        (),
        (),
        "plausible within the domain; the corpus records no such procedure",
    ),
    # -- abstention (2): genuinely unanswerable -------------------------------
    Question(
        "hv2-abstention-01",
        "abstention",
        "Which vendor supplies the production message broker?",
        None,
        "ABSENT",
        (),
        (),
        "domain-plausible; the corpus names RabbitMQ but records no vendor",
    ),
    Question(
        "hv2-abstention-02",
        "abstention",
        "How many replicas does the API Gateway run?",
        None,
        "ABSENT",
        (),
        (),
        "domain-plausible; no replica count is recorded anywhere",
    ),
]

#: Evidence ids are recorded where the corpus makes them unambiguous. Questions
#: without an entry are scored on the authored key alone, which is the protocol's
#: stated gold rule; `UNSCORABLE` is reserved for a gold that cannot be defended.
GOLD_EVIDENCE: dict[str, tuple[str, ...]] = {}

ANSWERABLE: dict[str, bool] = {}

#: Per-category targets, keyed to protocol section 8. Frozen before authoring.
CATEGORY_TARGETS = {
    "temporal_state": 4,
    "version_update": 4,
    "why_decision": 5,
    "supersession": 3,
    "conflict": 3,
    "cross_document": 3,
    "relationship": 3,
    "identifier": 3,
    "negative_evidence": 2,
    "abstention": 2,
}

#: Evidence-id mapping and answerability live here rather than on the shared
#: dataclass, which must not change: both dev and v1 construct Question positionally.
GOLD_EVIDENCE: dict[str, tuple[str, ...]] = {}
ANSWERABLE: dict[str, bool] = {}


def build_questions() -> list[Question]:
    return list(hv2_QUESTIONS)


def dataset_hash() -> str:
    """Hash of corpus plus v2 questions.

    Includes the corpus so a change to the documents invalidates the freeze, exactly
    as the dev and v1 hashes do.
    """
    blob = json.dumps(
        {
            "documents": [{"path": d.path, "content": d.content} for d in build_corpus()],
            "questions": [
                {
                    "qid": q.qid,
                    "category": q.category,
                    "text": q.text,
                    "key": q.key,
                    "expect_state": q.expect_state,
                    "expect_values": list(q.expect_values),
                    "forbid_values": list(q.forbid_values),
                    "gold_evidence_ids": list(GOLD_EVIDENCE.get(q.qid, ())),
                    "answerable": ANSWERABLE.get(q.qid, q.key is not None),
                }
                for q in build_questions()
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def corpus_keys() -> set[str]:
    """Every authored key the corpus actually contains."""
    from m0115_dataset import INCIDENTS, SERVICES, TECHNOLOGIES

    keys = {f"architecture.{t}" for t in TECHNOLOGIES}
    keys |= {spec["reason_key"] for spec in TECHNOLOGIES.values()}
    for sid, *_ in SERVICES:
        keys |= {f"service.{sid}.owner", f"service.{sid}.tier", f"runbook.{sid}.escalation"}
        keys.add(f"service.{sid}.depends_on")
    for period, sid, _slug, _cause, key, _c in INCIDENTS:
        keys |= {f"incident.{period}.{sid}.root", key}
    keys.add("config.database.url")
    keys |= {f"release.{v}" for v in ("0.6", "0.7", "0.8")}
    keys |= {f"constraint.deploy.tier-{i}" for i in range(4)}
    keys |= {f"team.{t}.services" for t in ("platform", "payments", "discovery", "data")}
    keys |= {"architecture.datastore"}
    return keys


def validate() -> tuple[list[dict], list[str]]:
    """Return (rows, problems). Never raises; reports everything at once."""
    problems: list[str] = []
    known = corpus_keys()
    rows: list[dict] = []
    seen: set[str] = set()

    for q in build_questions():
        answerable = ANSWERABLE.get(q.qid, q.key is not None)
        if q.qid in seen:
            problems.append(f"{q.qid}: duplicate qid")
        seen.add(q.qid)
        if q.category not in CATEGORY_TARGETS:
            problems.append(f"{q.qid}: unknown category {q.category!r}")
        if q.expect_state == "ABSENT":
            if q.key is not None:
                problems.append(f"{q.qid}: ABSENT question must have key=None")
            if q.expect_values:
                problems.append(f"{q.qid}: ABSENT question must not expect values")
        elif not answerable:
            if q.key is not None:
                problems.append(
                    f"{q.qid}: marked UNSCORABLE but carries key {q.key!r}; "
                    "unscorable questions must not assert a gold key"
                )
        elif q.key is None:
            problems.append(f"{q.qid}: answerable question requires a key")
        elif q.key not in known:
            problems.append(f"{q.qid}: gold key {q.key!r} does not exist in the corpus")
        rows.append(
            {
                "qid": q.qid,
                "category": q.category,
                "text": q.text,
                "key": q.key,
                "expect_state": q.expect_state,
                "expect_values": list(q.expect_values),
                "forbid_values": list(q.forbid_values),
                "gold_evidence_ids": list(GOLD_EVIDENCE.get(q.qid, ())),
                "answerable": answerable,
            }
        )
    return rows, problems


def category_counts() -> dict[str, int]:
    counts = {c: 0 for c in CATEGORY_TARGETS}
    for q in build_questions():
        if q.category in counts:
            counts[q.category] += 1
    return counts


# ---------------------------------------------------------------------------
# Metrics shared with the frozen protocol
# ---------------------------------------------------------------------------


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


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def run_frozen(arm: str) -> dict:
    """Execute v2 once against the frozen configuration."""
    questions = build_questions()
    rows, _ = validate()

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
    corpus_name = "m013-hv2"
    corpus_id = corpus_id_for_name(corpus_name)
    metrics = {"r1": [], "r5": [], "r10": [], "mrr": [], "ndcg5": []}
    abstention = []
    per_question = []

    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            schema = "hv2_" + uuid4().hex[:10]
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

            import math

            for q, row in zip(questions, rows):
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
                sem, _p, _w = relevance(
                    full,
                    request,
                    "current",
                    embedder if arm != "lexical" else None,
                    policy,
                    lexical=(arm == "lexical"),
                )
                ranked = [k for k, _ in sorted(sem.items(), key=lambda kv: (-kv[1], kv[0]))]

                if q.expect_state == "ABSENT":
                    abstention.append(1.0 if not ranked else 0.0)
                    per_question.append({**row, "ranked": ranked, "scored": False})
                    continue
                if not row["answerable"]:
                    per_question.append({**row, "ranked": ranked, "scored": False})
                    continue

                gold = q.key
                metrics["r1"].append(1.0 if gold in ranked[:1] else 0.0)
                metrics["r5"].append(1.0 if gold in ranked[:5] else 0.0)
                metrics["r10"].append(1.0 if gold in ranked[:10] else 0.0)
                metrics["mrr"].append(
                    next((1.0 / i for i, k in enumerate(ranked, 1) if k == gold), 0.0)
                )
                gains = [1.0 if k == gold else 0.0 for k in ranked[:5]]
                dcg = sum(g / math.log2(i + 1) for i, g in enumerate(gains, 1))
                ideal = sum(1.0 / math.log2(i + 1) for i in range(1, 6))
                metrics["ndcg5"].append(dcg / ideal if ideal else 0.0)
                per_question.append({**row, "ranked": ranked, "scored": True})
        finally:
            await outer.rollback()
    await engine.dispose()

    scored = len(metrics["r1"])
    ceiling = (
        1.0
        if scored == 0
        else sum(1 for p in per_question if p["scored"] and p["key"] in p["ranked"]) / scored
    )
    return {
        "arm": arm,
        "scored_questions": scored,
        "unscorable_questions": sum(
            1 for p in per_question if not p["scored"] and p["expect_state"] != "ABSENT"
        ),
        "abstention_questions": len(abstention),
        "metrics": {
            k: {"point": round(sum(v) / len(v), 4), "ci95": list(bootstrap_ci(v))}
            for k, v in metrics.items()
            if v
        },
        "abstention_accuracy": round(sum(abstention) / len(abstention), 4) if abstention else None,
        "candidate_recall_ceiling": round(ceiling, 4),
        "unreachable": [
            p["qid"] for p in per_question if p["scored"] and p["key"] not in p["ranked"]
        ],
        "per_question": per_question,
    }


def cmd_freeze(args) -> int:
    questions = build_questions()
    rows, problems = validate()
    counts = category_counts()

    print(f"questions: {len(questions)} (target ~{sum(CATEGORY_TARGETS.values())})")
    for category, target in CATEGORY_TARGETS.items():
        got = counts.get(category, 0)
        flag = "ok" if got == target else f"TARGET {target}"
        print(f"  {category:<20} {got:>2}  {flag}")

    if problems:
        print("\nVALIDATION PROBLEMS:")
        for problem in problems:
            print(f"  !! {problem}")
        print("\nfreeze REFUSED. Fix the questions first.")
        return 1
    if not questions:
        print("\nThe set is EMPTY. freeze REFUSED.")
        print("Owner must author the questions first (protocol section 8).")
        print("Machine-generated questions are not acceptable for a held-out set.")
        return 1

    missing = [c for c, t in CATEGORY_TARGETS.items() if counts.get(c, 0) < t]
    if missing and not args.allow_partial:
        print(f"\ncategories below target: {missing}")
        print("Re-run with --allow-partial to freeze anyway.")
        return 1

    freeze = {
        "dataset_hash": dataset_hash(),
        "corpus_hash": dev_dataset_hash(),
        "questions": len(questions),
        "categories": counts,
        "question_rows": rows,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
    }
    Path(args.out).write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")
    print(f"\nfrozen. hash={freeze['dataset_hash'][:16]} written: {args.out}")
    return 0


def cmd_verify(args) -> int:
    if not FREEZE_PATH.exists():
        print("FAIL: no freeze file. Run `freeze` first.")
        return 1
    frozen = json.loads(FREEZE_PATH.read_text())
    current = dataset_hash()
    if frozen["dataset_hash"] != current:
        print("FAIL: dataset hash changed after freeze")
        print(f"  frozen  : {frozen['dataset_hash']}")
        print(f"  current : {current}")
        print("The held-out set must be immutable after freezing. Start a new version.")
        return 1
    print(f"OK: dataset hash unchanged ({current[:16]})")
    return 0


def cmd_run(args) -> int:
    if not build_questions():
        print("FAIL: no questions. The held-out v2 gate cannot be run on an empty set.")
        return 1
    if cmd_verify(args) != 0:
        print("FAIL: refusing to run against a set that differs from its freeze.")
        return 1
    report = {
        "meta": {
            "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
            "dataset_hash": dataset_hash(),
            "arm": args.arm,
            "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            "bootstrap": "percentile, 2000 iterations, seed 12345",
            "note": "held-out v2; frozen, run once for the recorded configuration",
        },
        **asyncio.run(run_frozen(args.arm)),
    }
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    m = report["metrics"]
    print(
        f"\narm={report['arm']} scored={report['scored_questions']} "
        f"unscorable={report['unscorable_questions']}"
    )
    for name in ("r1", "r5", "r10", "mrr", "ndcg5"):
        if name in m:
            print(f"  {name:<6} {m[name]['point']:.4f}  CI {m[name]['ci95']}")
    print(f"  candidate recall ceiling: {report['candidate_recall_ceiling']}")
    if report["abstention_accuracy"] is not None:
        print(f"  abstention accuracy     : {report['abstention_accuracy']}")
    print(f"  unreachable: {report['unreachable']}")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--out", default=str(FREEZE_PATH))
    f.add_argument("--allow-partial", action="store_true")
    f.set_defaults(func=cmd_freeze)
    v = sub.add_parser("verify")
    v.set_defaults(func=cmd_verify)
    r = sub.add_parser("run")
    r.add_argument("--arm", default="semantic", choices=("semantic", "lexical"))
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_run)
    parsed = parser.parse_args()
    raise SystemExit(parsed.func(parsed))
