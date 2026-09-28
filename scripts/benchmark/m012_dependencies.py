"""M012 authority dependency inventory, derived from the implementation.

The previous experiment showed that narrowing the archive breaks 32 of 202
answers even when key selection is identical, because `project` and `select`
derive currency, conflicts and change records from the whole archive.

Before proposing any relation index, this derives the actual edges from the code
that consumes them, and measures the transitive closure each one produces. A
closure that approaches corpus size kills the idea before it is built.

Edges are discovered by instrumenting the real resolver: run every benchmark
question, and for each claim that survives into the answer, record which other
rows the resolver read to decide it.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
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


async def run() -> dict:
    from m0117_scale import Offline

    from api.models.memory import MemoryRequest
    from api.services.corpora import get_corpus_by_name
    from api.services.ingestion import IngestionService
    from api.services.memory import _load
    from api.services.memory_public import State, project
    from api.services.memory_query import query as run_query

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "dep_" + uuid4().hex[:10]
    corpus_id = __import__("hashlib").sha256(uuid4().bytes).hexdigest()

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
            for doc in build_corpus():
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)

            async with _session(conn) as db:
                row = await get_corpus_by_name(db, schema)
                versions = await _load(db, row["id"], chunk_text=False)

            out = await _analyse(
                versions, conn, row["id"], schema, run_query, project, State, MemoryRequest
            )
        finally:
            await outer.rollback()
    await engine.dispose()
    return out


async def _analyse(versions, conn, corpus_id, schema, run_query, project, State, Request):
    """For each answer claim, measure the closure the resolver actually needs."""
    questions = build_questions()

    all_claims = {}
    for v in versions:
        for c in v["claims"]:
            all_claims[c["id"]] = {
                **c,
                "path": v["path"],
                "version_id": v["id"],
                "event": v["event"],
                "version_number": v["version_number"],
                "document_id": v["memory_document_id"],
            }
    by_key = collections.defaultdict(list)
    for cid, c in all_claims.items():
        by_key[c["key"]].append(cid)
    doc_versions = collections.defaultdict(list)
    for v in versions:
        doc_versions[v["path"]].append(v["id"])

    stats = {
        "corpus": {
            "versions": len(versions),
            "documents": len({v["path"] for v in versions}),
            "claims": len(all_claims),
            "keys": len(by_key),
        },
        "edge_density": {},
        "closure": {},
        "observed": [],
    }

    # Edge types derived from what the schema and resolver actually relate.
    for key, ids in by_key.items():
        docs = {all_claims[i]["path"] for i in ids}
        stats["edge_density"][key] = {
            "claims": len(ids),
            "versions": len({all_claims[i]["version_id"] for i in ids}),
            "documents": len(docs),
        }
    sizes = [d["claims"] for d in stats["edge_density"].values()]
    stats["claims_per_key"] = {
        "max": max(sizes) if sizes else 0,
        "mean": round(sum(sizes) / len(sizes), 2) if sizes else 0,
    }

    # The closure that matters: answer claim -> same key -> that key's documents
    # -> every version of those documents -> claims on those versions.
    closures = []
    for question in questions:
        if question.key is None:
            continue
        async with _session(conn) as db:
            request = Request(corpus=schema, query=question.text, valid_at=VALID_AT, budget=128000)
            state = State(as_of=None, valid_at=VALID_AT)
            full = project(
                versions,
                request.model_copy(update={"query": "", "path": None}),
                "pack",
                state,
                None,
            )
            answer = await run_query(full, request, db=db, corpus_id=corpus_id)
        answer_ids = {c.id for c in answer.current_memories}
        answer_ids |= {c.id for g in answer.conflicts for c in g.claims}
        touched_claims, touched_versions, touched_docs = set(), set(), set()
        for cid in answer_ids:
            touched_claims.add(cid)
            touched_versions.add(all_claims[cid]["version_id"])
            touched_docs.add(all_claims[cid]["path"])
            # same-key closure
            for other in by_key.get(all_claims[cid]["key"], []):
                touched_claims.add(other)
                touched_versions.add(all_claims[other]["version_id"])
                touched_docs.add(all_claims[other]["path"])
            # every version of the documents involved, which is what currency needs
            for path in list(touched_docs):
                for vid in doc_versions[path]:
                    touched_versions.add(vid)
                    touched_docs.add(path)
            for vid in list(touched_versions):
                for c in versions:
                    if c["id"] == vid:
                        for cc in c["claims"]:
                            touched_claims.add(cc["id"])
        closures.append(
            {
                "qid": question.qid,
                "answer_claims": len(answer_ids),
                "closure_claims": len(touched_claims),
                "closure_versions": len(touched_versions),
                "closure_documents": len(touched_docs),
            }
        )
    stats["observed"] = closures
    for field, total in (
        ("closure_claims", "claims"),
        ("closure_versions", "versions"),
        ("closure_documents", "documents"),
    ):
        values = sorted(c[field] for c in closures)
        if not values:
            continue
        n = len(values)
        stats["closure"][field] = {
            "median": values[n // 2],
            "p95": values[min(n - 1, int(n * 0.95))],
            "max": values[-1],
            "corpus_total": stats["corpus"][total],
            "max_pct_of_corpus": round(values[-1] / max(1, stats["corpus"][total]) * 100, 1),
        }
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m012-dependencies.json"))
    args = parser.parse_args()
    result = asyncio.run(run())
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    print("corpus:")
    for k, v in result["corpus"].items():
        print(f"  {k:<20} {v}")
    print(f"\nclaims per authored key: {result['claims_per_key']}")
    print("\ndependency closure per answered question:")
    for field, s in result["closure"].items():
        print(
            f"  {field:<20} median={s['median']:>4} p95={s['p95']:>5} max={s['max']:>5}"
            f"   corpus={s['corpus_total']:>5}  max is {s['max_pct_of_corpus']}% of corpus"
        )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
