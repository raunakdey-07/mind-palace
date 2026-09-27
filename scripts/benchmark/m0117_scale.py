"""M011.7: where does the query path change regime?

The claim representation cache was measured only to 88 claims, and a result
measured on a small corpus is not a result. This builds corpora at 100, 500,
1k, 5k and 10k claims and measures the same questions at each size.

The archive load is the suspect. `memory._load` returns every version with its
chunks, claims and evidence aggregated as JSON, so it is O(archive) per query
regardless of how few claims the question needs. If that is the cliff, it will
show up as a term that grows with claims while the cached semantic path does not.

Ingestion uses a deterministic offline embedder so building ten thousand claims
is feasible; the query path uses the real model, because a latency number from a
fake embedder would be meaningless.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.services.rehydrate import backfill_claim_embeddings  # noqa: E402
from memory_pack import MemoryPack  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DIM = 384

PROBE = (
    "What is the current database?",
    "Who owns the Orders Service?",
    "What is the current job queue?",
    "What is the current search engine?",
)

# Bodies are varied so the relevance gate is not scoring one repeated sentence.
BODY = "The system records this decision because the previous approach failed at scale."


class Offline:
    """Deterministic stand-in used only while building a corpus."""

    model_name = "offline-scale-fixture"
    version = "scale"
    dimension = DIM

    def embed(self, texts):
        out = []
        for value in texts:
            digest = hashlib.sha256(value.encode()).digest()
            vector = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(DIM)]
            norm = sum(v * v for v in vector) ** 0.5 or 1.0
            out.append([v / norm for v in vector])
        return out


def build_documents(target_claims: int) -> list[tuple[str, str]]:
    """Anchor facts plus filler, so the probe questions mean something at every size."""
    anchors = (
        ("architecture.database", "The primary database is PostgreSQL."),
        ("architecture.queue", "The job queue is RabbitMQ."),
        ("architecture.search", "The search engine is OpenSearch."),
        ("service.orders.owner", "The Orders Service is owned by the payments team."),
    )
    documents: list[tuple[str, str]] = []
    for key, sentence in anchors:
        documents.append((f"anchors/{key.split('.', 1)[1]}.md", _doc(key, sentence)))
    made = len(anchors)
    index = 0
    while made < target_claims:
        index += 1
        key_base = f"domain.{index % 97}.subsystem"
        for n, sentence in enumerate(
            (
                f"Component {index} stores telemetry in the {key_base} tier.",
                f"Component {index} is owned by the team-{index % 11} rota.",
                f"Component {index} depends on service-{index % 31} for reads.",
            )
        ):
            documents.append((f"components/{index:05d}.md", _doc(f"{key_base}.{n}", sentence)))
        made += 3
    return documents


def _doc(key: str, sentence: str) -> str:
    claims = (
        f"  - key: {key}\n"
        f"    value: {json.dumps(sentence)}\n"
        f"    claim: {json.dumps(sentence)}\n"
        f"    evidence: {json.dumps(sentence)}\n"
    )
    return (
        "---\n"
        f'title: "{key}"\n'
        'document_type: "design"\n'
        "claims:\n" + claims + "---\n"
        f"\n# {key}\n\n{sentence}\n\n{BODY}\n"
    )


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


async def counts(conn, corpus_id: str) -> dict:
    async with AsyncSession(bind=conn, expire_on_commit=False) as db:
        result = await db.execute(
            text(
                "SELECT (SELECT count(*) FROM memory_claims WHERE corpus_id = :c) AS claims, "
                "(SELECT count(*) FROM memory_versions WHERE corpus_id = :c) AS versions"
            ),
            {"c": corpus_id},
        )
    return {k: int(v) for k, v in result.mappings().one().items()}


async def clear_cache(conn, corpus_id: str) -> int:
    """Delete the cache and assert it went, so a silent no-op cannot pass."""
    async with AsyncSession(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as db:
        async with db.begin():
            await db.execute(
                text("DELETE FROM memory_claim_embeddings WHERE corpus_id = :c"), {"c": corpus_id}
            )
    return await cache_rows(conn, corpus_id)


async def cache_rows(conn, corpus_id: str) -> int:
    async with AsyncSession(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as db:
        result = await db.execute(
            text("SELECT count(*) FROM memory_claim_embeddings WHERE corpus_id = :c"),
            {"c": corpus_id},
        )
        return int(result.scalar())


async def time_query(conn, schema: str, question: str, breakdown: bool) -> dict:
    """Time one public query, and optionally the archive load inside it."""
    from api.models.memory import MemoryRequest
    from api.services import memory
    from api.services.corpora import get_corpus_by_name
    from api.services.memory_public import State, project

    request = MemoryRequest(corpus=schema, query=question, budget=128000)
    result = {"load": 0.0}
    start = time.perf_counter()
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as db:
        corpus = await get_corpus_by_name(db, schema)
        t0 = time.perf_counter()
        versions = await memory._load(db, corpus["id"])
        result["load"] = (time.perf_counter() - t0) * 1000
        result["versions"] = len(versions)
        result["rows"] = sum(len(v.get("claims") or []) for v in versions)
        if breakdown:
            t0 = time.perf_counter()
        state = State(as_of=None, valid_at=None)
        t0 = time.perf_counter()
        full = project(
            versions,
            request.model_copy(update={"query": "", "path": None}),
            "pack",
            state,
            None,
        )
        if breakdown:
            result["project"] = (time.perf_counter() - t0) * 1000
        from api.services.memory_query import query as run_query

        t0 = time.perf_counter()
        response = await run_query(full, request, db=db, corpus_id=corpus["id"])
        if breakdown:
            result["resolve"] = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
        else:
            t0 = None
        pack = MemoryPack.from_json(response.canonical_json())
        if breakdown:
            result["read"] = (time.perf_counter() - t0) * 1000
        result["claims_returned"] = len(pack.all_claims())
        result["status"] = pack.status
    result["total"] = (time.perf_counter() - start) * 1000
    return result


async def measure_size(url: str, target: int, repeats: int) -> dict:
    from api.services.ingestion import IngestionService

    engine = create_async_engine(url, poolclass=NullPool)
    documents = build_documents(target)
    schema = "scale_" + uuid4().hex[:10]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    out: dict = {"target_claims": target, "documents": len(documents)}

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
            t0 = time.perf_counter()
            for path, content in documents:
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    await service._ingest_content(db, content, path, corpus_id)
            out["ingest_s"] = round(time.perf_counter() - t0, 1)
            out.update(await counts(conn, corpus_id))

            # The real model must own the query path, so fill the cache with it.
            t0 = time.perf_counter()
            async with AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db:
                async with db.begin():
                    written = await backfill_claim_embeddings(db, corpus_id)
            out["backfill_s"] = round(time.perf_counter() - t0, 1)
            rows = await cache_rows(conn, corpus_id)
            if rows != written or written == 0:
                raise RuntimeError(f"backfill wrote {written} but {rows} rows are present")
            out["cache_rows"] = rows

            warm, load, cache_trace = [], [], []
            for _ in range(repeats):
                for question in PROBE:
                    # Record the cache state with every sample. A latency jump
                    # halfway through a run means the cache stopped being used,
                    # and that must be visible rather than inferred.
                    rows_now = await cache_rows(conn, corpus_id)
                    sample = await time_query(conn, schema, question, breakdown=False)
                    warm.append(sample["total"])
                    load.append(sample["load"])
                    cache_trace.append(rows_now)
            # The breakdown must be taken with the cache warm. Taken after the
            # clear it measures the embedding path and hides where a warm query
            # actually spends its time.
            one = await time_query(conn, schema, PROBE[0], breakdown=True)
            if one["claims_returned"] == 0:
                raise RuntimeError("the probe question resolved nothing; the corpus is unusable")
            out["breakdown_ms"] = {k: round(v, 2) for k, v in one.items() if isinstance(v, float)}
            out["probe_status"] = one["status"]
            out["probe_claims"] = one["claims_returned"]

            cleared = await clear_cache(conn, corpus_id)
            if cleared != 0:
                raise RuntimeError(f"cache still holds {cleared} rows after deletion")
            cold = []
            for question in PROBE:
                cold.append((await time_query(conn, schema, question, breakdown=False))["total"])
            out.update(
                {
                    "warm_p50": round(statistics.median(warm), 2),
                    "warm_samples": [round(v, 1) for v in warm],
                    "cache_rows_during_warm": cache_trace,
                    "load_p50": round(statistics.median(load), 2),
                    "cold_p50": round(statistics.median(cold), 2),
                    "cold_samples": [round(v, 1) for v in cold],
                    "repeats": repeats,
                }
            )
            one = await time_query(conn, schema, PROBE[0], breakdown=True)
            if one["claims_returned"] == 0:
                raise RuntimeError("the probe question resolved nothing; the corpus is unusable")
            out["breakdown_ms"] = {k: round(v, 2) for k, v in one.items() if isinstance(v, float)}
            out["probe_status"] = one["status"]
            out["probe_claims"] = one["claims_returned"]
        finally:
            await outer.rollback()
        await engine.dispose()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sizes", default="100,500,1000,5000,10000")
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0117-scale.json"))
    args = parser.parse_args()

    url = os.environ.get("MEMORY_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1
    url = url.replace("postgresql://", "postgresql+asyncpg://")

    sizes = [int(s) for s in args.sizes.split(",")]
    report = {"repeats": args.repeats, "points": []}
    for size in sizes:
        print(f"building {size} claims...", flush=True)
        point = asyncio.run(measure_size(url, size, args.repeats))
        report["points"].append(point)
        print(
            f"  claims={point['claims']:>6} rows={point.get('rows', 0):>6} "
            f"ingest={point['ingest_s']:>6.1f}s backfill={point['backfill_s']:>6.1f}s  "
            f"load_p50={point['load_p50']:>9.1f}  "
            f"warm_p50={point['warm_p50']:>9.1f}  cold_p50={point['cold_p50']:>9.1f} ms"
            f"  x{point['cold_p50'] / max(0.001, point['warm_p50']):.1f}",
            flush=True,
        )
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
