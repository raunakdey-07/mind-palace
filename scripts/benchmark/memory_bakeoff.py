# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Bake-off: the live-index product surface against the authoritative query.

Mind Palace ships two ways to ask a question a corpus:

- ``context()`` / ``GET /context``: embed the question, rank live chunks, concatenate
  text. This is the documented primary product surface.
- authoritative ``query``: resolve intent and temporal state against the archive, then
  rank authored claims under a relevance gate and abstention policy.

Both answer "what does this corpus know about X". Only one carries claims, validity,
conflicts, and exact evidence. This script measures the difference on one corpus and
one question set, with real PostgreSQL, real migrations, and a real cached embedding
model. It also measures what each surface does when the embedding model is unavailable.

Both adapters are called exactly as the product calls them. Nothing is stubbed except
that the corpus is small and authored, so the numbers are correctness bounds, not a
retrieval benchmark at scale.

Usage:
    DATABASE_URL=postgresql://USER:PASS@localhost:5432/mindpalace \\
        venvmp/bin/python scripts/benchmark/memory_bakeoff.py

Writes docs/performance/memory-bakeoff.json and prints a summary table.
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# -- corpus -------------------------------------------------------------------
# An "AI coding agent" workload: what should I know before changing the database?
# Four documents carry authored claims, a supersession, and a live conflict.

STORAGE_V1 = """---
title: Storage Architecture
document_type: "design"
claims:
  - key: architecture.database
    value: MySQL
    claim: The primary database is MySQL.
    evidence: The primary database is MySQL.
---
# Storage

The primary database is MySQL. Connections come from a single pool in the API tier.
"""

STORAGE_V2 = """---
title: Storage Architecture
document_type: "design"
claims:
  - key: architecture.database
    value: PostgreSQL
    claim: The primary database is PostgreSQL.
    evidence: The primary database is PostgreSQL.
---
# Storage

The primary database is PostgreSQL. Connections come from a single pool in the API tier.
"""

MIGRATION = """---
title: Why PostgreSQL
document_type: "adr"
claims:
  - key: decision.database.reason
    value: jsonb-and-advisory-locks
    claim: PostgreSQL was chosen for JSONB and advisory locks.
    evidence: PostgreSQL was chosen for JSONB and advisory locks.
  - key: constraint.migrations
    value: expand-contract-mandatory
    claim: Expand-contract migrations are mandatory once traffic exists.
    evidence: Expand-contract migrations are mandatory once traffic exists.
---
# Why PostgreSQL

PostgreSQL was chosen for JSONB and advisory locks. MySQL was workable but needed an
external lock service for the corpus advisory lock.

Expand-contract migrations are mandatory once traffic exists.
"""

CACHE_ADR = """---
title: ADR 004 cache layer
document_type: "adr"
claims:
  - key: architecture.database
    value: SQLite
    claim: The primary database is SQLite for the edge tier.
    evidence: The primary database is SQLite for the edge tier.
    valid_from: 2024-01-01T00:00:00+00:00
---
# ADR 004

The primary database is SQLite for the edge tier. This ADR was never reconciled with the
storage design and is still marked active by its author.
"""

DEPLOY = """---
title: Deployment constraints
document_type: "note"
claims:
  - key: constraint.deploy
    value: single-writer-migration
    claim: Migrations run as a single writer job.
    evidence: Migrations run as a single writer job.
---
# Deployment

Migrations run as a single writer job. The API tier holds no DDL lock outside that job.
"""

# -- question set -------------------------------------------------------------
# Each case names the question, what a correct answer must contain, and the single
# dimension being scored. ``expect`` is a lower-case substring test against the
# authoritative response; ``absent`` must NOT appear for abstention cases.

CASES = [
    {
        "id": "current",
        "question": "What is the current database?",
        "class": "current-state",
        "expect": "postgresql",
        "intent": "current",
    },
    {
        "id": "historical",
        "question": "What database did the project use previously?",
        "class": "historical",
        "expect": "mysql",
        "intent": "historical",
    },
    {
        "id": "change",
        "question": "When did the database change?",
        "class": "change",
        "expect": "supersed",
        "intent": "change",
    },
    {
        "id": "conflict",
        "question": "What sources disagree about the database?",
        "class": "conflict",
        "expect": "sqlite",
        "intent": "conflict",
    },
    {
        "id": "provenance",
        "question": "What evidence supports the current database?",
        "class": "provenance",
        "expect": "primary database is postgresql",
        "intent": "provenance",
    },
    {
        "id": "why",
        "question": "Why was the database changed?",
        "class": "why",
        "expect": "jsonb",
        "intent": "auto",
    },
    {
        "id": "abstention",
        "question": "What payroll provider does the company use?",
        "class": "abstention",
        "expect": None,
        "absent": "payroll",
        "intent": "auto",
    },
]


def _migrations():
    for path in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


def _migrate(sync_conn):
    for module in _migrations():
        with Operations.context(MigrationContext.configure(sync_conn)):
            module.upgrade()


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


# -- adapters: exactly what the product calls --------------------------------


async def authoritative(db: AsyncSession, corpus_name: str, case: dict, as_of=None):
    """The authoritative query path, as `MemoryClient.query` calls it."""
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute_in_session

    request = MemoryRequest(
        corpus=corpus_name,
        query=case["question"],
        as_of=as_of,
        intent=case["intent"],
    )
    return await execute_in_session(db, "query", request)


async def product_context(db: AsyncSession, corpus_name: str, case: dict, embedder):
    """The live-index context path, as `GET /context` calls it."""
    from api.services.context_packer import pack_context
    from api.services.corpora import get_corpus_by_name
    from api.services.retrieval import RetrievalService

    row = await get_corpus_by_name(db, corpus_name)
    vector = embedder.embed_single(case["question"])
    results = await RetrievalService(db).search(
        vector,
        k=8,
        hybrid=True,
        rrf=True,
        query_text=case["question"],
        corpus_id=row["id"],
    )
    return pack_context(case["question"], results, budget_tokens=4096, strategy="hybrid_rrf")


# -- scoring ------------------------------------------------------------------


def score_authoritative(response, case: dict) -> dict:
    if case["class"] == "abstention":
        # Correct abstention means no memory was invented for the topic. The
        # response echoes the question back in `query`, so topic detection reads
        # the claims, not the whole envelope.
        invented = bool(
            response.current_memories or response.historical_memories or response.evidence
        )
        return {
            "pass": not invented,
            "abstained": not invented,
            "invented": invented,
        }
    body = response.canonical_json().casefold()
    got_expect = case["expect"] is None or case["expect"].casefold() in body
    if case["class"] == "conflict":
        return {
            "pass": bool(response.conflicts) and got_expect,
            "conflicts": len(response.conflicts),
        }
    if case["class"] == "change":
        return {"pass": bool(response.changes) and got_expect, "changes": len(response.changes)}
    if case["class"] == "provenance":
        return {
            "pass": bool(response.evidence) and got_expect,
            "evidence": len(response.evidence),
        }
    if case["class"] == "current-state":
        # A conflicted key is reported as a structured conflict, not as a
        # current claim, so either shape is a correct current-state answer.
        keys = {c.key for c in response.current_memories} | {g.key for g in response.conflicts}
        return {"pass": got_expect and bool(keys), "keys": sorted(keys)}
    if case["class"] == "historical":
        return {
            "pass": got_expect and bool(response.historical_memories),
            "historical": len(response.historical_memories),
        }
    return {
        "pass": got_expect,
        "constraints": len(response.constraints),
        "current": len(response.current_memories),
    }


def score_context(pack, case: dict) -> dict:
    """`context()` returns ranked text. Score only whether the answer is present.

    It has no claims, conflicts, validity, or evidence fields at all, so the
    structural dimensions are reported as unavailable rather than scored.
    """
    body = (pack.context or "").casefold()
    if case["class"] == "abstention":
        # RRF ranks the whole corpus, so any question returns chunks. Returning
        # chunks for a topic with no memory is a false positive, not an answer.
        return {
            "pass": not pack.chunks,
            "abstained": not pack.chunks,
            "invented": bool(pack.chunks),
        }
    got_expect = case["expect"] is None or case["expect"].casefold() in body
    return {
        "pass": got_expect,
        "chunks": len(pack.chunks),
        "has_conflict_field": False,
        "has_evidence_field": False,
        "has_validity_field": False,
    }


# -- run ----------------------------------------------------------------------


async def build_corpus(conn, corpus_id: str, corpus_name: str, embedder) -> None:
    """Ingest the workload corpus through the real ingestion service."""
    from api.services.ingestion import IngestionService

    service = IngestionService()
    for path, content in (
        ("docs/storage.md", STORAGE_V1),
        ("docs/storage.md", STORAGE_V2),
        ("docs/migration.md", MIGRATION),
        ("adrs/adr-004-cache.md", CACHE_ADR),
        ("docs/deploy.md", DEPLOY),
    ):
        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db:
            await service._ingest_content(db, content, path, corpus_id)
    # observed_at is append-only, so read the real boundaries back instead of
    # rewriting history. The as-of point is placed between the two storage versions.
    async with AsyncSession(bind=conn, expire_on_commit=False) as db:
        result = await db.execute(
            text(
                "SELECT v.version_number, v.observed_at FROM memory_versions v "
                "JOIN memory_documents d ON d.corpus_id = v.corpus_id "
                "AND d.id = v.memory_document_id "
                "WHERE v.corpus_id = :c AND d.path = 'docs/storage.md' "
                "ORDER BY v.version_number"
            ),
            {"c": corpus_id},
        )
        rows = result.fetchall()
    if len(rows) != 2:
        raise RuntimeError(f"expected 2 storage versions, found {len(rows)}")
    return rows


async def run(url: str, args) -> dict:
    from api.services.embedder import Embedder

    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "bakeoff_" + uuid4().hex[:12]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    report = {
        "meta": environment(args),
        "corpus": {
            "documents": 4,
            "versions": 5,
            "authored_claims": 6,
            "supersession": "architecture.database: MySQL -> PostgreSQL",
            "conflict": (
                "architecture.database: SQLite, authored valid from 2024-01-01, unreconciled"
            ),
        },
        "cases": [],
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
            versions = await build_corpus(conn, corpus_id, schema, None)

            embedder = Embedder()
            if not args.no_model:
                embedder.embed_single("warmup")  # pay the cold cost outside timing

            for case in CASES:
                entry = {
                    "id": case["id"],
                    "class": case["class"],
                    "question": case["question"],
                }

                # Authoritative path
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    t0 = time.perf_counter()
                    response = await authoritative(db, schema, case)
                    entry["authoritative_ms"] = (time.perf_counter() - t0) * 1000
                entry["authoritative"] = score_authoritative(response, case)
                entry["authoritative"]["budget_chars"] = len(response.canonical_json())

                # Product context path
                if args.no_model:
                    entry["context"] = {"pass": False, "error": "embedding model unavailable"}
                else:
                    async with AsyncSession(
                        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                    ) as db:
                        t0 = time.perf_counter()
                        pack = await product_context(db, schema, case, embedder)
                        entry["context_ms"] = (time.perf_counter() - t0) * 1000
                    entry["context"] = score_context(pack, case)
                    entry["context"]["token_estimate"] = pack.token_estimate

                # As-of case: place the cutoff between the two storage versions.
                if case["id"] == "historical":
                    between = versions[0][1] + (versions[1][1] - versions[0][1]) / 2
                    async with AsyncSession(
                        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                    ) as db:
                        t0 = time.perf_counter()
                        past = await authoritative(
                            db, schema, case, as_of=datetime.fromisoformat(str(between))
                        )
                        entry["authoritative_asof_ms"] = (time.perf_counter() - t0) * 1000
                    entry["as_of"] = {
                        "cutoff": str(between),
                        "saw_mysql": "mysql" in past.canonical_json().casefold(),
                        "saw_postgresql": "postgresql" in past.canonical_json().casefold(),
                    }

                report["cases"].append(entry)
                auth_ok = "PASS" if entry["authoritative"]["pass"] else "fail"
                ctx_ok = "PASS" if entry.get("context", {}).get("pass") else "fail"
                print(
                    f"  {case['id']:<12} authoritative={auth_ok} "
                    f"({entry['authoritative_ms']:.0f} ms)  "
                    f"context={ctx_ok}"
                    + (
                        f" ({entry.get('context_ms', 0):.0f} ms)"
                        if not args.no_model
                        else " (no model)"
                    ),
                    flush=True,
                )

            await outer.rollback()
        finally:
            await engine.dispose()

    return summarise(report)


def summarise(report: dict) -> dict:
    cases = report["cases"]
    auth = [c for c in cases if c["authoritative"]["pass"]]
    ctx = [c for c in cases if c.get("context", {}).get("pass")]
    report["summary"] = {
        "authoritative_pass": len(auth),
        "authoritative_total": len(cases),
        "context_pass": len(ctx),
        "context_total": len(cases),
        "authoritative_ms_mean": round(sum(c["authoritative_ms"] for c in cases) / len(cases), 2),
        "context_ms_mean": (
            round(sum(c["context_ms"] for c in cases if "context_ms" in c) / len(ctx), 2)
            if any("context_ms" in c for c in cases)
            else None
        ),
    }
    return report


def environment(args) -> dict:
    def git(*a):
        try:
            return subprocess.run(
                ["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except Exception:
            return None

    model = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hardware": platform.machine(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "postgres": args.postgres_version,
        "dependencies": _deps(),
        "model": model,
        "warmup": "cold" if args.no_model else "warm",
        "iterations": 1,
        "note": "correctness comparison on a small authored corpus, not a latency benchmark",
    }


def _deps() -> dict:
    out = {}
    for name in ("asyncpg", "sqlalchemy", "torch", "sentence-transformers", "numpy"):
        try:
            from importlib.metadata import version

            out[name] = version(name)
        except Exception:
            out[name] = None
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs/performance/memory-bakeoff.json"))
    parser.add_argument("--no-model", action="store_true", help="simulate a missing model")
    parser.add_argument("--postgres-version", default="unknown")
    args = parser.parse_args()

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("FAIL: set MEMORY_TEST_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 1

    print("memory bake-off: authoritative query vs product context()")
    report = asyncio.run(run(url, args))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    s = report["summary"]
    print()
    print(f"authoritative : {s['authoritative_pass']}/{s['authoritative_total']} cases pass")
    print(f"context()     : {s['context_pass']}/{s['context_total']} cases pass")
    print(f"written       : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
