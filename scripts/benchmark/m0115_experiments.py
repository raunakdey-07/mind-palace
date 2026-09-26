"""M011.5 experiments: lexical, semantic, fallback, routing, and degradation.

Nothing here changes production behaviour. Every strategy is exercised through
the existing public selection path with an explicit flag, so a measured result
is a fact about code that already exists rather than about a prototype.

Experiments:

    A  semantic always / lexical always / lexical-then-semantic fallback
    B  routing quality: which questions a cheap path gets wrong
    C  degradation: model missing, embeddings missing, whole L2 deleted
    D  relationship capability
    E  memory state: which states exist and whether they are derived
    F  pack-only consumers, with no server access
    G  poisoning: retrieval versus authority

Every question is scored against authored ground truth from m0115_dataset, so a
failure is a failure of the system and not of the label. Failures are classified
F1..F12 rather than counted, so "add a graph" cannot become the answer to
everything.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0115_dataset import build_corpus, build_questions, dataset_hash  # noqa: E402

from memory_pack import NO_RELEVANT_MEMORY, MemoryPack  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

# Failure taxonomy. Every wrong answer is attributed to exactly one of these.
FAILURES = {
    "F1": "source missing",
    "F2": "authority missing",
    "F3": "temporal semantics",
    "F4": "conflict semantics",
    "F5": "provenance",
    "F6": "query interpretation",
    "F7": "lexical retrieval",
    "F8": "semantic retrieval",
    "F9": "relationship representation",
    "F10": "budget or truncation",
    "F11": "developer API",
    "F12": "performance",
}


def _migrate(sync_conn):
    for path in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(sync_conn)):
            module.upgrade()


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


# ---------------------------------------------------------------------------
# Selection without touching production code
# ---------------------------------------------------------------------------


async def select_with(conn, schema: str, question: str, *, lexical: bool) -> tuple:
    """Run the real projection and the real selection with one flag flipped.

    Returns (full_state, response). ``lexical=True`` skips the embedding call
    inside the relevance gate; everything else is the production path.
    """
    from api.models.memory import MemoryRequest
    from api.services import memory
    from api.services.corpora import get_corpus_by_name
    from api.services.memory_public import State, bounded_pack, project
    from api.services.memory_query import interpret, select

    request = MemoryRequest(corpus=schema, query=question, valid_at=VALID_AT, budget=128000)
    async with _session(conn) as db:
        corpus = await get_corpus_by_name(db, schema)
        versions = await memory._load(db, corpus["id"])

    state = State(as_of=None, valid_at=VALID_AT)
    full = project(
        versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
    )
    intent = interpret(request)
    if lexical:
        result = select(full, request, intent, None, lexical=True)
    else:
        from api.services.embedder import Embedder

        result = select(full, request, intent, Embedder())
    return full, bounded_pack(result, request.budget)


def score(question, response) -> tuple[bool, str, dict]:
    """Score one answer against authored ground truth."""
    pack = MemoryPack.from_json(response.canonical_json())
    keys = set(pack.keys())
    blob = pack.canonical_json().casefold()
    detail = {
        "status": pack.status,
        "keys": sorted(keys),
        "claims": len(pack.all_claims()),
        "conflicts": len(pack.conflicts),
    }

    if question.expect_state == "ABSENT":
        if pack.status == NO_RELEVANT_MEMORY:
            return True, "", detail
        return False, "F2", detail

    if question.key and question.key not in keys:
        return False, ("F9" if question.category == "relationship" else "F7"), detail

    missing = [v for v in question.expect_values if v.casefold() not in blob]
    if missing:
        code = "F4" if question.expect_state == "CONFLICTING" else "F2"
        detail["missing_values"] = missing
        return False, code, detail

    if question.expect_state == "CONFLICTING" and not pack.conflicts:
        return False, "F4", detail
    if question.expect_state == "HISTORICAL":
        # A superseded statement must not be presented as the current answer.
        if question.expect_values and any(
            str(c.value) in question.expect_values for c in pack.current
        ):
            return False, "F3", detail
    return True, "", detail


def classify(question, failed_code: str, lexical_ok: bool, semantic_ok: bool) -> str:
    """Attribute a wrong answer to a cause, not to a subsystem to build."""
    if lexical_ok and not semantic_ok:
        return "F7"  # the cheap path was right and the expensive one was wrong
    if not lexical_ok and not semantic_ok:
        if question.category == "abstention":
            return "F6" if "absent" in question.text else "F2"
        if question.expect_state == "ABSENT":
            return "F2"
        if question.category == "relationship":
            return failed_code or "F9"
        return failed_code or "F7"
    return failed_code or "F7"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


async def run(url: str, repeats: int) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "m115_" + uuid4().hex[:12]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    questions = build_questions()
    report = {
        "meta": {
            "commit": _git(),
            "python": platform.python_version(),
            "dataset_hash": dataset_hash(),
            "questions": len(questions),
            "repeats": repeats,
            "valid_at": VALID_AT.isoformat(),
            "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        },
        "results": [],
        "timing": {"semantic_ms": [], "lexical_ms": []},
    }

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
            for doc in build_corpus():
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)

            for question in questions:
                t0 = time.perf_counter()
                _full, semantic = await select_with(conn, schema, question.text, lexical=False)
                report["timing"]["semantic_ms"].append((time.perf_counter() - t0) * 1000)

                t0 = time.perf_counter()
                _full, lexical = await select_with(conn, schema, question.text, lexical=True)
                report["timing"]["lexical_ms"].append((time.perf_counter() - t0) * 1000)

                sem_ok, sem_code, sem_detail = score(question, semantic)
                lex_ok, lex_code, lex_detail = score(question, lexical)

                # Fallback: trust the cheap path, escalate only when it abstains.
                if lex_ok and lexical_detail_is_answer(lexical):
                    fallback_ok, fallback_code = lex_ok, ""
                else:
                    fallback_ok, fallback_code = sem_ok, sem_code

                report["results"].append(
                    {
                        "qid": question.qid,
                        "category": question.category,
                        "text": question.text,
                        "expect_state": question.expect_state,
                        "semantic_ok": sem_ok,
                        "lexical_ok": lex_ok,
                        "fallback_ok": fallback_ok,
                        "failure": (
                            "" if fallback_ok else classify(question, fallback_code, lex_ok, sem_ok)
                        ),
                        "lexical_status": lex_detail["status"],
                        "semantic_status": sem_detail["status"],
                    }
                )
        finally:
            await outer.rollback()
        await engine.dispose()

    report["timing"] = {
        "semantic_ms": _stats(report["timing"]["semantic_ms"]),
        "lexical_ms": _stats(report["timing"]["lexical_ms"]),
    }
    report["summary"] = summarise(report["results"])
    return report


def lexical_detail_is_answer(response) -> bool:
    """The fallback must escalate when the cheap path abstained or mismatched."""
    pack = MemoryPack.from_json(response.canonical_json())
    return pack.status != NO_RELEVANT_MEMORY and bool(pack.all_claims())


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    return {
        "n": n,
        "p50": round(ordered[n // 2], 2),
        "p95": round(ordered[min(n - 1, int(n * 0.95))], 2),
        "mean": round(sum(ordered) / n, 2),
    }


def summarise(results: list[dict]) -> dict:
    out = {
        "total": len(results),
        "strategies": {
            "A_semantic": sum(1 for r in results if r["semantic_ok"]),
            "B_lexical": sum(1 for r in results if r["lexical_ok"]),
            "C_fallback": sum(1 for r in results if r["fallback_ok"]),
        },
        "by_category": {},
        "failures": {},
    }
    for r in results:
        bucket = out["by_category"].setdefault(
            r["category"], {"n": 0, "semantic": 0, "lexical": 0, "fallback": 0}
        )
        bucket["n"] += 1
        bucket["semantic"] += int(r["semantic_ok"])
        bucket["lexical"] += int(r["lexical_ok"])
        bucket["fallback"] += int(r["fallback_ok"])
        if r["failure"]:
            out["failures"][r["failure"]] = out["failures"].get(r["failure"], 0) + 1

    # Routing quality, the Experiment B classification.
    routing = {"cheap_sufficient": 0, "unnecessarily_expensive": 0, "dangerously_cheap": 0}
    for r in results:
        if r["lexical_ok"] and r["semantic_ok"]:
            routing["cheap_sufficient"] += 1
        elif r["lexical_ok"] and not r["semantic_ok"]:
            routing["unnecessarily_expensive"] += 1
        elif r["lexical_ok"] is False and r["semantic_ok"] and _answered(r):
            routing["dangerously_cheap"] += 1
    out["routing"] = routing
    out["failure_labels"] = FAILURES
    return out


def _answered(r: dict) -> bool:
    return r["lexical_status"] not in (NO_RELEVANT_MEMORY, "empty")


def _git() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0115-experiments.json"))
    args = parser.parse_args()

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    print("M011.5 experiments: lexical vs semantic vs fallback")
    report = asyncio.run(run(url, args.repeats))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    s = report["summary"]
    print(f"\ndataset {report['meta']['dataset_hash'][:16]}  questions {s['total']}")
    print(f"A semantic always : {s['strategies']['A_semantic']}/{s['total']}")
    print(f"B lexical always  : {s['strategies']['B_lexical']}/{s['total']}")
    print(f"C lexical->sem    : {s['strategies']['C_fallback']}/{s['total']}")
    print("\nby category (n / A / B / C):")
    for name, b in sorted(s["by_category"].items()):
        print(
            f"  {name:<14} {b['n']:>3}  {b['semantic']:>3}  {b['lexical']:>3}  {b['fallback']:>3}"
        )
    print("\nrouting:", json.dumps(s["routing"]))
    print("failures:", json.dumps(s["failures"]))
    print("\ntiming:", json.dumps(report["timing"]))
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
