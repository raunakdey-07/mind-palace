"""M011.6: how hard is it to get trustworthy memory context in a fresh app?

Counts what a competent developer has to touch, with no prior knowledge of the
repository. Three paths are measured separately, because they are different
questions:

    python    a plain script against a running PostgreSQL
    fastapi   an HTTP service using the SDK
    cli       no code, just commands

Reported: concepts that must be understood, lines written, commands run,
required environment, and what comes back.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY_APP = """\
from mindpalace_sdk import MindPalace
from memory_pack import MemoryPack

memory = MindPalace("integration-demo", base_url=os.environ["MP_BASE_URL"])
raw = memory.memory.query(corpus="integration-demo", query=QUESTION)
pack = MemoryPack.from_json(raw.canonical_json())
print(pack.status)
print(len(pack.current), "current", len(pack.conflicts), "conflict")
"""

FASTAPI_APP = """\
from fastapi import FastAPI
from mindpalace_sdk import MindPalace
from memory_pack import MemoryPack

app = FastAPI()
memory = MindPalace("integration-demo", base_url=os.environ["MP_BASE_URL"])


@app.get("/brief")
def brief(question: str = "What is the current database?"):
    raw = memory.memory.query(corpus="integration-demo", query=question)
    pack = MemoryPack.from_json(raw.canonical_json())
    return {
        "status": pack.status,
        "current": [c.value for c in pack.current],
        "conflicts": [g.key for g in pack.conflicts],
        "evidence": len(pack.evidence),
    }
"""

REQUIRED_ENV = ("DATABASE_URL", "MEMORY_TEST_DATABASE_URL")


def count(text: str) -> dict:
    lines = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    return {"lines": len(lines), "non_blank": len(lines)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs/performance/m0116-integration.json"))
    args = parser.parse_args()

    url = os.environ.get("MEMORY_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    report = {
        "python_path": count(PY_APP),
        "fastapi_path": count(FASTAPI_APP),
        "concepts_required": [
            "corpus: a named namespace the memory is written to",
            "a question in natural language",
            "the pack: current, historical, conflicting, and the evidence behind each",
        ],
        "concepts_not_required": [
            "database schema",
            "embeddings",
            "RRF",
            "rerankers",
            "MCP",
            "claims",
            "evidence offsets",
            "snapshots",
            "budget units",
        ],
        "required_env": [],
        "optional_env": ["MEMORY_TEST_DATABASE_URL", "DATABASE_URL", "HF_HUB_OFFLINE"],
    }

    # Does the SDK import without pulling the semantic stack?
    probe = (
        "import sys, mindpalace_sdk;"
        "banned=[m for m in ('torch','sentence_transformers','transformers') if m in sys.modules];"
        "print(','.join(banned) or 'none')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=str(ROOT), timeout=120
    )
    report["sdk_import_loads"] = result.stdout.strip() or result.stderr.strip()[-120:]

    # A real end-to-end run against a live corpus.
    if url:
        report["required_env"] = ["DATABASE_URL (or MEMORY_TEST_DATABASE_URL)"]
        report["live"] = _live(url)

    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print("integration cost")
    print(f"  python script : {report['python_path']['lines']} lines")
    print(f"  fastapi app   : {report['fastapi_path']['lines']} lines")
    print(f"  sdk import loads: {report['sdk_import_loads']}")
    if "live" in report:
        live = report["live"]
        print(
            f"  live query    : status={live['status']} claims={live['claims']} "
            f"conflicts={live['conflicts']} evidence={live['evidence']}"
        )
        print(f"  wall time     : {live['ms']:.0f} ms")
    print(f"  written       : {args.out}")
    return 0


def _live(url: str) -> dict:
    import asyncio
    import hashlib
    import importlib.util
    import time
    from uuid import uuid4

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))
    from m0115_dataset import build_corpus

    question = "What is the current database?"
    doc = (
        "---\n"
        'title: "Storage"\n'
        'document_type: "design"\n'
        "claims:\n"
        "  - key: architecture.database\n"
        '    value: "PostgreSQL"\n'
        '    claim: "The primary database is PostgreSQL."\n'
        '    evidence: "The primary database is PostgreSQL."\n'
        "---\n"
        "\n# Storage\n\nThe primary database is PostgreSQL.\n"
    )

    def migrate(conn):
        for path in sorted((ROOT / "migrations/versions").glob("*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()

    async def go():
        engine = create_async_engine(
            url.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
        )
        schema = "integ_" + uuid4().hex[:10]
        corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
        out = {}
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
                await conn.run_sync(migrate)
                await conn.execute(
                    text("INSERT INTO corpora(id, name) VALUES (:i, :n)"),
                    {"i": corpus_id, "n": "integration-demo"},
                )
                from api.services.ingestion import IngestionService

                service = IngestionService()
                for path, content in [(p.path, p.content) for p in build_corpus()[:3]] + [
                    ("docs/storage.md", doc)
                ]:
                    async with AsyncSession(
                        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                    ) as db:
                        await service._ingest_content(db, content, path, corpus_id)
                from api.models.memory import MemoryRequest
                from api.services.memory_public import execute_in_session
                from memory_pack import MemoryPack

                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
                ) as db:
                    start = time.perf_counter()
                    response = await execute_in_session(
                        db, "query", MemoryRequest(corpus="integration-demo", query=question)
                    )
                    out["ms"] = (time.perf_counter() - start) * 1000
                # Status is derived by the portable reader, not stored on the
                # response. That is a real integration cost, recorded as such.
                pack = MemoryPack.from_json(response.canonical_json())
                out["status"] = pack.status
                out["claims"] = len(pack.current)
                out["conflicts"] = len(pack.conflicts)
                out["evidence"] = len(pack.evidence)
                out["response_carries_status"] = hasattr(response, "status")
            finally:
                await outer.rollback()
        await engine.dispose()
        return out

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
