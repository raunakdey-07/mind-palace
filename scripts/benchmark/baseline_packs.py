"""Phase 0 baseline: capture the full authoritative output of every benchmark question.

Scores are not enough to gate on. Every later change in this milestone must be
able to answer "is the authoritative output byte-identical to the baseline?", and
that needs the packs themselves, not a count of the ones that matched.

Writes one file containing, per question, the question id, the canonical pack,
and the SHA-256 of that pack, plus a single digest over the whole set. A later
run compares against this file and names every question whose pack moved.

    capture   write the baseline
    compare   diff against a baseline file and exit non-zero on any difference

Two runs of unchanged code used to disagree on all 202 packs, because the harness
gave the corpus a random name and let PostgreSQL stamp ``clock_timestamp()``. The
corpus id feeds every content-addressed id in the archive (document, version,
claim, chunk, evidence, conflict), so a random corpus id randomises the whole pack,
and the wall clock randomises ``observed_at``.

Both are pinned here so the digest measures authority rather than the run:

* a fixed corpus name, which fixes the id chain, because ``corpus_id`` is
  ``sha256("corpus:" + name)``;
* ``observed_at`` replaced by its rank within the pack, which preserves the
  ordering that authority depends on while dropping the clock reading itself.

A wall-clock value that moves without reordering anything carries no authority and
would otherwise mask a real change on every single run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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

from m0115_dataset import build_corpus, build_questions  # noqa: E402

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
CORPUS_NAME = "mp-baseline"
URL = os.environ.get("DATABASE_URL") or ("postgresql://mpadmin:secret@localhost:5432/mindpalace")


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


def _timestamps(node, found=None):
    """Collect every ``observed_at`` string in a decoded pack."""
    if found is None:
        found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "observed_at" and isinstance(value, str):
                found.add(value)
            else:
                _timestamps(value, found)
    elif isinstance(node, list):
        for item in node:
            _timestamps(item, found)
    return found


def _rank_times(node, order):
    """Replace each ``observed_at`` with its rank, keeping list order untouched."""
    if isinstance(node, dict):
        return {
            k: (order[v] if k == "observed_at" and isinstance(v, str) else _rank_times(v, order))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rank_times(item, order) for item in node]
    return node


def normalize(canonical_json: str) -> str:
    """Pin the two run-scoped inputs so the digest reflects authority, not the run."""
    pack = json.loads(canonical_json)
    rank = {value: i for i, value in enumerate(sorted(_timestamps(pack)))}
    return json.dumps(_rank_times(pack, rank), sort_keys=True, separators=(",", ":"))


async def capture() -> dict:
    """Run every benchmark question and record its full canonical pack."""
    from api.models.memory import MemoryRequest
    from api.services.corpora import corpus_id_for_name
    from api.services.ingestion import IngestionService
    from api.services.memory_public import execute_in_session

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "base_" + uuid4().hex[:10]
    corpus_id = corpus_id_for_name(CORPUS_NAME)
    questions = build_questions()
    packs: dict[str, str] = {}

    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrations)
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": CORPUS_NAME},
            )
            service = IngestionService()
            for doc in build_corpus():
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)

            for question in questions:
                async with _session(conn) as db:
                    response = await execute_in_session(
                        db,
                        "query",
                        MemoryRequest(
                            corpus=CORPUS_NAME,
                            query=question.text,
                            valid_at=VALID_AT,
                            budget=128000,
                        ),
                    )
                packs[question.qid] = normalize(response.canonical_json())
        finally:
            await outer.rollback()
    await engine.dispose()

    blob = "\n".join(f"{qid}\t{packs[qid]}" for qid in sorted(packs))
    return {
        "questions": len(packs),
        "dataset_hash": _dataset_hash(),
        "corpus": CORPUS_NAME,
        "normalized_fields": ["observed_at"],
        "set_digest": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
        "packs": {
            qid: {
                "digest": hashlib.sha256(packs[qid].encode("utf-8")).hexdigest(),
                "canonical_json": packs[qid],
            }
            for qid in sorted(packs)
        },
    }


def _dataset_hash() -> str:
    from m0115_dataset import dataset_hash

    return dataset_hash()


def compare(baseline_path: str, current_path: str, show: str | None = None) -> int:
    baseline = json.loads(Path(baseline_path).read_text())
    current = json.loads(Path(current_path).read_text())

    if baseline["questions"] != current["questions"]:
        print(f"FAIL: question count {current['questions']} != baseline {baseline['questions']}")
        return 1
    if baseline["dataset_hash"] != current["dataset_hash"]:
        print(
            f"FAIL: dataset hash changed\n  baseline {baseline['dataset_hash']}\n"
            f"  current  {current['dataset_hash']}"
        )
        return 1

    moved = []
    for qid, entry in baseline["packs"].items():
        other = current["packs"].get(qid)
        if other is None:
            moved.append((qid, "missing"))
        elif other["digest"] != entry["digest"]:
            moved.append((qid, "changed"))

    print(f"baseline commit : {baseline.get('commit', '?')[:12]}")
    print(f"current  commit : {current.get('commit', '?')[:12]}")
    print(f"questions       : {current['questions']}")
    print(f"set digest      : {current['set_digest'][:16]}")
    if not moved:
        print("authoritative output: IDENTICAL to baseline")
        return 0
    print(f"authoritative output: {len(moved)} QUESTION(S) DIFFER")
    for qid, why in moved:
        print(f"  {qid}: {why}")
    if show:
        _show(baseline["packs"], current["packs"], show)
    return 1


def _walk(x, y, path=""):
    if type(x) is not type(y):
        yield f"TYPE {path}: {type(x).__name__} vs {type(y).__name__}"
    elif isinstance(x, dict):
        for key in sorted(set(x) | set(y)):
            if key not in x or key not in y:
                yield f"MISSING {path}.{key}"
            else:
                yield from _walk(x[key], y[key], f"{path}.{key}")
    elif isinstance(x, list):
        if len(x) != len(y):
            yield f"LENGTH {path}: {len(x)} vs {len(y)}"
        else:
            for i, (u, v) in enumerate(zip(x, y)):
                yield from _walk(u, v, f"{path}[{i}]")
    elif x != y:
        yield f"VALUE {path}:\n    baseline={x!r}\n    current ={y!r}"


def _show(baseline_packs, current_packs, qid: str) -> None:
    if qid not in baseline_packs or qid not in current_packs:
        print(f"\n{show_help()}: {qid} is not in both captures")
        return
    print(f"\n--- {qid} ---")
    lines = list(
        _walk(
            json.loads(baseline_packs[qid]["canonical_json"]),
            json.loads(current_packs[qid]["canonical_json"]),
        )
    )
    print("\n".join(lines) if lines else "(no field differs)")


def show_help() -> str:
    return "--show QID"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--out", required=True)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--baseline", required=True)
    cmp_.add_argument("--current", required=True)
    cmp_.add_argument("--show", help="print the field-level diff for one question id")
    args = parser.parse_args()

    if args.action == "compare":
        return compare(args.baseline, args.current, args.show)

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    result = asyncio.run(capture())
    result["commit"] = os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip()
    result["captured_at"] = datetime.now(timezone.utc).isoformat()
    result["valid_at"] = VALID_AT.isoformat()
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"captured {result['questions']} packs")
    print(f"dataset hash   : {result['dataset_hash'][:16]}")
    print(f"set digest     : {result['set_digest']}")
    print(f"commit         : {result['commit'][:12]}")
    print(f"written        : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
