"""Inspect a single question's authoritative result against its ground truth.

Used to attribute a benchmark failure to the system or to the label, rather than
guessing from the failure code.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0115_dataset import build_corpus, build_questions  # noqa: E402
from m0115_experiments import (  # noqa: E402
    _async_url,
    _migrate,
    _session,
    select_with,
)

from memory_pack import MemoryPack  # noqa: E402  # noqa: E402


async def main(qids: list[str]) -> int:
    from api.services.ingestion import IngestionService

    url = os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    schema = "insp_" + uuid4().hex[:12]
    corpus_id = hashlib.sha256(uuid4().bytes).hexdigest()
    wanted = {q.qid: q for q in build_questions() if q.qid in qids}

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

            for qid, question in wanted.items():
                _full, response = await select_with(conn, schema, question.text, lexical=False)
                pack = MemoryPack.from_json(response.canonical_json())
                print("=" * 76)
                print(
                    f"{qid}  [{question.category}]  expect={question.expect_state} "
                    f"key={question.key} values={question.expect_values}"
                )
                print(f"Q: {question.text}")
                print(
                    f"status={pack.status} keys={list(pack.keys())} "
                    f"conflicts={len(pack.conflicts)} changes={len(pack.changes)}"
                )
                for claim in pack.all_claims():
                    print(f"   {claim.key} = {claim.value!r} [{claim.status}] {claim.path}")
                for group in pack.conflicts:
                    print(
                        f"   CONFLICT {group.key}: "
                        + " vs ".join(f"{c.value!r}" for c in group.claims)
                    )
                for change in pack.changes:
                    prev = change.previous[0].value if change.previous else None
                    cur = change.current[0].value if change.current else None
                    print(
                        f"   CHANGE {change.observed_at[:10]} {change.path} "
                        f"{change.relationship} {prev!r} -> {cur!r}"
                    )
        finally:
            await outer.rollback()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
