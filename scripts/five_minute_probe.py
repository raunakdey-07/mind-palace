#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Measure the five product operations at a representative corpus size.

    python scripts/five_minute_probe.py
    python scripts/five_minute_probe.py --statements 500 --repetitions 20

Reports p50 and p95 for remember, recall, explain and verify: the operations a
developer actually performs. `verify` is timed separately and separately on
purpose -- it runs against a file on disk with no database, no model and no
network, which is the property being measured.

Everything happens in a random schema that is dropped afterwards, so no corpus,
artifact or output survives the run.
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
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from api.models.memory import MemoryRequest  # noqa: E402
from api.services import ingestion, memory_public  # noqa: E402
from api.services import remember as remember_service  # noqa: E402
from api.services.memory_public import execute_in_session  # noqa: E402
from api.services.remember import remember  # noqa: E402

#: Statements shaped like the decisions a team actually records, so the measured
#: relevance path is a realistic one rather than a single exact key.
TOPICS = [
    ("datastore", "The primary datastore is {value}."),
    ("cache", "The cache layer is {value}."),
    ("queue", "Work is drained from {value}."),
    ("scheduler", "Scheduled work runs on {value}."),
    ("search", "Search is served by {value}."),
]
VALUES = [
    "PostgreSQL 16 across three availability zones",
    "Redis with a fifteen minute TTL",
    "a single partitioned topic",
    "a five node cron controller",
    "OpenSearch with a nightly reindex",
    "SQLite on the edge hosts",
    "NATS JetStream with at-least-once delivery",
    "a leased Postgres cluster in eu-west-1",
]
QUESTIONS = [
    "Which datastore did we choose?",
    "What is the cache layer?",
    "Where is scheduled work run?",
    "What serves search?",
]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


async def build(url: str, schema: str):
    """A migrated schema, its corpus, and an open connection scoped to both.

    The connection is returned open: the read session below is bound to it, and a
    connection that left its `async with` would already be closed.
    """
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.commit()

    setup = await engine.connect()
    transaction = await setup.begin()
    await setup.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    for path in sorted((ROOT / "migrations" / "versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        await setup.run_sync(_upgrade(module))
    corpus = hashlib.sha256(uuid4().bytes).hexdigest()
    await setup.execute(
        text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
        {"id": corpus, "name": schema},
    )
    await transaction.commit()
    await setup.close()

    # Every connection used afterwards is scoped by server setting, so no
    # transaction is needed to keep search_path pointing at the probe schema.
    scoped = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    session = AsyncSession(scoped, join_transaction_mode="create_savepoint", expire_on_commit=False)
    return engine, scoped, corpus, session


def _upgrade(module):
    """Run one migration inside the connection's current transaction."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    def apply(sync_conn):
        with Operations.context(MigrationContext.configure(sync_conn)):
            module.upgrade()

    return apply


def scope_to(corpus: str):
    """Point every write path at the probe's schema and corpus.

    The service keeps its real transaction ownership; only the connection and the
    corpus id belong to the probe.
    """

    class SavepointSession:
        def begin(self):
            return HOLDER["db"].begin_nested()

        def __getattr__(self, name):
            return getattr(HOLDER["db"], name)

    @asynccontextmanager
    async def session_scope():
        yield SavepointSession()

    for module in (ingestion, memory_public):
        module.session_scope = session_scope

    async def ensure(name, corpus_id=None):
        return corpus

    remember_service.ensure_corpus = ensure


HOLDER: dict = {}


async def populate(corpus: str, statements: int) -> float:
    started = time.perf_counter()
    for index in range(statements):
        key, template = TOPICS[index % len(TOPICS)]
        value = VALUES[(index // len(TOPICS)) % len(VALUES)]
        await remember(
            template.format(value=value),
            corpus_id=corpus,
            key=f"decision.{key}.{index // len(TOPICS)}",
        )
    return time.perf_counter() - started


async def measure(name: str, corpus: str, repetitions: int) -> dict:
    """`name` selects the corpus on the read path; `corpus` is its id, for writes."""
    timings: dict[str, list[float]] = {"remember": [], "recall": [], "explain": []}

    for index in range(repetitions):
        started = time.perf_counter()
        await remember(
            f"Probe write {index}: the release train ships on Thursdays.",
            corpus_id=corpus,
            key=f"probe.write.{index}",
        )
        timings["remember"].append((time.perf_counter() - started) * 1000)

    for operation, receipt in (("recall", False), ("explain", True)):
        for _ in range(repetitions):
            for question in QUESTIONS:
                started = time.perf_counter()
                await execute_in_session(
                    HOLDER["db"],
                    "query",
                    MemoryRequest(corpus=name, query=question, include_receipt=receipt),
                )
                timings[operation].append((time.perf_counter() - started) * 1000)
    return timings


def measure_verify(receipt_path: Path, corpus: str, repetitions: int) -> list[float]:
    """Timed in a clean subprocess: a separate interpreter, no database URL, no
    model, no network. That is what "portable" has to mean to be worth claiming."""
    import subprocess

    snippet = """
import json, sys, time
from memory_receipt import verify_response_receipt
payload = json.load(open(sys.argv[1]))
times = []
for _ in range(int(sys.argv[2])):
    started = time.perf_counter()
    result = verify_response_receipt(payload["receipt"], payload["response"])
    times.append((time.perf_counter() - started) * 1000)
assert result["verified"], "the probe receipt must verify before it is timed"
print(json.dumps(times))
"""
    completed = subprocess.run(
        [sys.executable, "-c", snippet, str(receipt_path), str(repetitions)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT)},
    )
    if completed.returncode != 0:
        raise SystemExit(f"verify probe failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the five product operations.")
    parser.add_argument("--statements", type=int, default=200)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--keep", action="store_true", help="Keep the probe schema.")
    args = parser.parse_args()

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("set MEMORY_TEST_DATABASE_URL or DATABASE_URL")
        return 2
    url = (
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )

    schema = "mp_probe_" + uuid4().hex[:12]
    engine, scoped, corpus, session = await build(url, schema)
    HOLDER["db"] = session
    scope_to(corpus)
    print(f"schema {schema}, corpus {corpus[:12]}, {args.statements} statements")

    receipt_path = Path(tempfile.gettempdir()) / "mp-probe-receipt.json"
    try:
        populate_seconds = await populate(corpus, args.statements)
        timings = await measure(schema, corpus, args.repetitions)

        explained = await execute_in_session(
            session,
            "query",
            MemoryRequest(corpus=schema, query=QUESTIONS[0], include_receipt=True),
        )
        receipt_path.write_text(
            json.dumps(
                {"response": explained.model_dump(mode="json"), "receipt": explained.receipt}
            ),
            encoding="utf-8",
        )
        timings["verify"] = measure_verify(receipt_path, corpus, args.repetitions)
    finally:
        await session.close()
        await scoped.dispose()
        if not args.keep:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await conn.commit()
            await engine.dispose()

    print(
        f"\nbulk ingest: {args.statements} statements in {populate_seconds:.2f}s "
        f"({populate_seconds / max(args.statements, 1) * 1000:.1f} ms each)"
    )
    print("\noperation    n     p50 ms    p95 ms")
    for name in ("remember", "recall", "explain", "verify"):
        values = timings.get(name) or []
        if values:
            print(
                f"{name:<11} {len(values):>3}   {statistics.median(values):>8.2f}   "
                f"{percentile(values, 0.95):>8.2f}"
            )
    print(
        "\nWall clock for the service call on this machine, embedding model warm.\n"
        "verify runs in a separate interpreter with no database URL and no network.\n"
        "These are measurements, not a guarantee: hardware, corpus shape and\n"
        "concurrent load all move them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
