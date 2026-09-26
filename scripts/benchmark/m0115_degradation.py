"""M011.5 experiments C, E, F, G: degradation, states, pack-only, poisoning.

Run in several processes, because the failure modes are process-level:

    full        model available, L2 intact
    nomodel     model name that cannot load
    nol2        every derived row deleted, model available
    nlexical    L2 present, retrieval unavailable, authority only

Each phase ingests once, commits, then measures in a fresh process, so no warm
model or warm pool can hide a failure. The pack-only consumer runs with the
server importable but unused, and asserts it never touched it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0115_dataset import POISON, build_corpus, build_questions  # noqa: E402
from m0115_experiments import VALID_AT, _async_url, _migrate, _session  # noqa: E402

from memory_pack import NO_RELEVANT_MEMORY, MemoryPack  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BROKEN_MODEL = "no-such-model/mini-nonexistent"

PHASES = ("full", "nomodel", "nol2", "nlexical")


async def ingest(schema: str, corpus_id: str) -> None:
    from api.services.ingestion import IngestionService

    url = os.environ["M115_URL"]
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        await conn.run_sync(_migrate)
        await conn.execute(
            text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
            {"id": corpus_id, "name": schema},
        )
        await conn.commit()
        service = IngestionService()
        for doc in build_corpus():
            async with _session(conn) as db:
                await service._ingest_content(db, doc.content, doc.path, corpus_id)
        await conn.commit()
    await engine.dispose()
    print(schema)


async def destroy_l2(schema: str, corpus_id: str) -> None:
    url = os.environ["M115_URL"]
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        await conn.commit()
        async with conn.begin():
            await conn.execute(
                text("DELETE FROM ingestion_manifest WHERE corpus_id = :c"), {"c": corpus_id}
            )
            await conn.execute(text("DELETE FROM documents WHERE corpus_id = :c"), {"c": corpus_id})
        rows = await conn.execute(
            text(
                "SELECT (SELECT count(*) FROM documents WHERE corpus_id = :c) AS docs, "
                "(SELECT count(*) FROM memory_versions WHERE corpus_id = :c) AS versions, "
                "(SELECT count(*) FROM memory_claims WHERE corpus_id = :c) AS claims"
            ),
            {"c": corpus_id},
        )
        print("L2 destroyed:", dict(rows.mappings().one()))
        await conn.commit()
    await engine.dispose()


def break_retrieval() -> None:
    """Make the live retrieval path unavailable without touching authority."""
    import api.services.context_service as cserv
    import api.services.embedder as embedder_mod

    def no_retrieval(*args, **kwargs):
        return []

    def broken(*args, **kwargs):
        raise OSError("model unavailable in this phase")

    cserv._retrieve = no_retrieval
    embedder_mod.Embedder = broken


async def query_via_service(conn, schema: str, question: str, valid_at=None):
    """The real public query path, including the lexical fallback.

    Degradation is a product question, so it has to be measured through the
    product. Calling ``select`` directly would bypass the fallback and would
    report a failure the product does not actually have.
    """
    from api.models.memory import MemoryRequest
    from api.services.memory_public import MemoryError, execute_in_session

    request = MemoryRequest(
        corpus=schema, query=question, valid_at=valid_at or VALID_AT, budget=128000
    )
    async with _session(conn) as db:
        try:
            return await execute_in_session(db, "query", request)
        except MemoryError as exc:
            return exc


async def measure(schema: str, corpus_id: str, phase: str) -> dict:
    if phase == "nlexical":
        break_retrieval()

    questions = build_questions()
    out = {
        "phase": phase,
        "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        "answered": 0,
        "correct": 0,
        "abstained": 0,
        "conflicts_reported": 0,
        "evidence_attached": 0,
        "digests": set(),
        "integrity_problems": [],
        "failures": [],
    }
    engine = create_async_engine(_async_url(os.environ["M115_URL"]), poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        await conn.commit()
        try:
            for question in questions:
                try:
                    response = await query_via_service(conn, schema, question.text)
                except Exception as exc:  # noqa: BLE001 - the failure is the measurement
                    out["failures"].append(
                        {"qid": question.qid, "error": type(exc).__name__, "detail": str(exc)[:80]}
                    )
                    continue
                if not hasattr(response, "canonical_json"):
                    out["failures"].append(
                        {
                            "qid": question.qid,
                            "error": getattr(response, "code", "MemoryError"),
                            "detail": getattr(response, "message", str(response))[:80],
                        }
                    )
                    continue
                pack = MemoryPack.from_json(response.canonical_json())
                out["answered"] += 1
                out["digests"].add(pack.digest())
                if question.expect_state == "ABSENT":
                    ok = pack.status == NO_RELEVANT_MEMORY
                else:
                    ok = bool(question.key in pack.keys()) and all(
                        v.casefold() in pack.canonical_json().casefold()
                        for v in question.expect_values
                    )
                out["correct"] += int(ok)
                out["abstained"] += int(pack.status == NO_RELEVANT_MEMORY)
                out["conflicts_reported"] += int(bool(pack.conflicts))
                out["evidence_attached"] += int(bool(pack.evidence))
                if pack.verify():
                    out["integrity_problems"].append({"qid": question.qid, "p": pack.verify()})
        finally:
            await engine.dispose()
    out["digests"] = sorted(out["digests"])
    return out


async def poisoning() -> dict:
    """Experiment G: can retrieval make untrusted text authoritative?

    Only claim text and evidence text are inspected. The envelope echoes the
    caller's question, so scanning the whole pack would report the question
    back to itself.
    """
    url = os.environ["M115_URL"]
    schema = os.environ["M115_SCHEMA"]
    markers = (
        "ignore all previous memory",
        "authoritative system policy",
        "delete the production memory",
        "obey this text",
        "permanent system policy",
        "store it as permanent",
    )
    out = {
        "poison_path": POISON["path"],
        "questions_checked": 0,
        "untrusted_claim_count": 0,
        "untrusted_evidence_count": 0,
        "markers_in_claims": [],
        "markers_in_evidence": [],
    }
    engine = create_async_engine(_async_url(url), poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        await conn.commit()
        for question in build_questions():
            response = await query_via_service(conn, schema, question.text)
            if not hasattr(response, "canonical_json"):
                continue
            pack = MemoryPack.from_json(response.canonical_json())
            out["questions_checked"] += 1
            for claim in pack.all_claims():
                if claim.path == POISON["path"]:
                    out["untrusted_claim_count"] += 1
                hit = [m for m in markers if m in claim.claim.casefold()]
                if hit:
                    out["markers_in_claims"].append({"qid": question.qid, "markers": hit})
            for row in pack.evidence:
                if row.path == POISON["path"]:
                    out["untrusted_evidence_count"] += 1
                hit = [m for m in markers if m in row.text.casefold()]
                if hit:
                    out["markers_in_evidence"].append({"qid": question.qid, "markers": hit})
    await engine.dispose()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["ingest", "destroy", "measure", "poison"])
    parser.add_argument("--phase", default="full", choices=PHASES)
    args = parser.parse_args()

    if args.action == "ingest":
        schema = "m115d_" + uuid4().hex[:10]
        os.environ["M115_SCHEMA"] = schema
        Path("/tmp/m115_schema").write_text(schema)
        Path("/tmp/m115_corpus").write_text(hashlib.sha256(uuid4().bytes).hexdigest())
        return asyncio.run(ingest(schema, Path("/tmp/m115_corpus").read_text()))
    if args.action == "destroy":
        return asyncio.run(
            destroy_l2(os.environ["M115_SCHEMA"], Path("/tmp/m115_corpus").read_text())
        )
    if args.action == "poison":
        print(json.dumps(asyncio.run(poisoning()), indent=2))
        return 0
    result = asyncio.run(
        measure(os.environ["M115_SCHEMA"], Path("/tmp/m115_corpus").read_text(), args.phase)
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
