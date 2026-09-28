"""M012 candidate-first resolution: measure, do not assume.

The hot-path map at 10,000 claims showed one warm query projecting 13,332
claims and 55,573 conflict groups to return one claim, with `resolve` at 85% of
elapsed time and the archive load at 2.9%.

The claim to test: relevance consumes only four fields of a claim, and those
four are already present as plain values in the rows the archive load returns.
If so, the relevance gate can run on those rows before any Pydantic model
exists, and the expensive projection can be built only for the keys the gate
selected.

This script measures candidate recall and answer equivalence for that idea
without touching production code. A candidate stage is allowed to over-select.
It is not allowed to under-select, so anything the gate might miss is measured
rather than assumed.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
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

VALID_AT = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
DATABASE = "postgresql://mpadmin:secret@localhost:5432/mindpalace"


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


class RowView:
    """The only four claim fields relevance reads, without a Pydantic model."""

    __slots__ = ("id", "claim", "key", "path", "status")

    def __init__(self, claim: dict, path: str):
        self.id = claim["id"]
        self.claim = claim["claim"]
        self.key = claim["key"]
        self.path = path
        self.status = claim.get("status")


def cheap_gate(versions: list[dict], question: str, intent: str, embedder):
    """Run the REAL relevance gate on raw rows, before anything is projected.

    Uses the production `relevance` function with the production scorer, on a
    stand-in response holding lightweight row views. Key selection is therefore
    identical to the shipped path by construction; only the object type differs.
    """
    from api.models.memory import MemoryRequest, MemoryResponse, State
    from api.services.memory_relevance import RelevancePolicy, relevance

    claims = [RowView(c, v["path"]) for v in versions for c in v["claims"]]
    if not claims:
        return set(), 0
    holder = MemoryResponse(query="", corpus="", state=State())
    object.__setattr__(holder, "current_memories", claims)
    request = MemoryRequest(corpus="x", query=question, valid_at=VALID_AT, budget=128000)
    scores, _, _ = relevance(
        holder, request, intent, embedder, RelevancePolicy.configured(), lexical=False
    )
    return set(scores), len(claims)


def narrow(versions: list[dict], keys: set[str]) -> list[dict]:
    """Keep every version of every document that carries a selected key.

    Key-level narrowing was tried first and broke 33 of 202 answers. The cause is
    version currency, not key selection: `project` decides what is current from
    the full version list, so dropping a document's older version makes a
    superseded claim look current. Keeping whole documents preserves ordering
    inside them, which is what currency is computed from.
    """
    if not keys:
        return []
    documents = {v["path"] for v in versions if any(c["key"] in keys for c in v["claims"])}
    return [v for v in versions if v["path"] in documents]


async def answer(db, schema: str, question: str, intent: str, versions):
    from api.models.memory import MemoryRequest
    from api.services.memory_public import State, project
    from api.services.memory_query import query as run_query

    request = MemoryRequest(
        corpus=schema, query=question, valid_at=VALID_AT, budget=128000, intent=intent
    )
    state = State(as_of=None, valid_at=VALID_AT)
    full = project(
        versions, request.model_copy(update={"query": "", "path": None}), "pack", state, None
    )
    return await run_query(full, request, db=db, corpus_id=schema)


async def run(scale: int) -> dict:
    from m0117_scale import Offline, build_documents

    from api.models.memory import MemoryRequest
    from api.services.corpora import get_corpus_by_name
    from api.services.embedder import Embedder
    from api.services.ingestion import IngestionService
    from api.services.memory import _load
    from api.services.memory_public import State, project
    from api.services.memory_query import query as run_query

    embedder = Embedder()
    engine = create_async_engine(
        DATABASE.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "cand_" + uuid4().hex[:10]
    corpus_id = __import__("hashlib").sha256(uuid4().bytes).hexdigest()
    questions = build_questions()
    out: dict = {"scale": scale, "questions": len(questions), "rows": []}

    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrations)
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": schema},
            )
            service = IngestionService()
            service.embedder = Offline()
            docs = [(d.path, d.content) for d in build_corpus()]
            if scale > len(docs):
                docs += build_documents(scale)
            for path, content in docs:
                async with _session(conn) as db:
                    await service._ingest_content(db, content, path, corpus_id)

            mismatches = []
            for question in questions:
                async with _session(conn) as db:
                    row = await get_corpus_by_name(db, schema)
                    versions = await _load(db, row["id"], chunk_text=False)
                # relevance does not use the intent for scoring, so the cheap
                # gate is free to pass "auto" for any question shape.
                keys, candidates = cheap_gate(versions, question.text, "auto", embedder)

                narrowed = narrow(versions, keys)
                request = MemoryRequest(
                    corpus=schema,
                    query=question.text,
                    valid_at=VALID_AT,
                    budget=128000,
                )
                state = State(as_of=None, valid_at=VALID_AT)
                full = project(
                    versions,
                    request.model_copy(update={"query": "", "path": None}),
                    "pack",
                    state,
                    None,
                )
                async with _session(conn) as db:
                    baseline = await run_query(full, request, db=db, corpus_id=row["id"])
                    if not narrowed:
                        # The cheap gate found nothing. Falling back is always safe.
                        narrow_answer = baseline
                        used_fallback = True
                    else:
                        small = project(
                            narrowed,
                            request.model_copy(update={"query": "", "path": None}),
                            "pack",
                            state,
                            None,
                        )
                        narrow_answer = await run_query(small, request, db=db, corpus_id=row["id"])
                        used_fallback = False
                same = baseline.canonical_json() == narrow_answer.canonical_json()
                out["rows"].append(
                    {
                        "qid": question.qid,
                        "same": same,
                        "candidates": candidates,
                        "keys": len(keys),
                        "versions_kept": len(narrowed),
                        "versions_total": len(versions),
                        "fallback": used_fallback,
                    }
                )
                if not same:
                    mismatches.append(question.qid)
        finally:
            await outer.rollback()
    await engine.dispose()

    out["equivalent"] = len(out["rows"]) - len(mismatches)
    out["total"] = len(out["rows"])
    out["mismatches"] = mismatches
    out["fallbacks"] = sum(1 for r in out["rows"] if r["fallback"])
    out["median_versions_kept"] = sorted(r["versions_kept"] for r in out["rows"])[
        len(out["rows"]) // 2
    ]
    out["median_versions_total"] = sorted(r["versions_total"] for r in out["rows"])[
        len(out["rows"]) // 2
    ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, default=0)
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m012-candidate.json"))
    args = parser.parse_args()
    result = asyncio.run(run(args.scale))
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"scale extra docs        : {result['scale']}")
    print(f"versions total (median) : {result['median_versions_total']}")
    print(f"versions kept  (median) : {result['median_versions_kept']}")
    print(f"fallbacks to full path  : {result['fallbacks']}")
    print(f"equivalent answers     : {result['equivalent']}/{result['total']}")
    if result["mismatches"]:
        print(f"MISMATCHES             : {result['mismatches'][:10]}")
    print(f"written: {args.out}")
    return 0 if not result["mismatches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
