"""Diagnose the M018 baseline harness defects (dev set only; not frozen).

Runs against dev_fixtures on main. Reports ingestion counts, retrieval behaviour,
C+ filter removals, B concatenation gold positions, and full D contexts for
VE-04 and NS-01. Fails loudly on swallowed exceptions.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.services.db import session_scope
from api.services.corpora import create_corpus, get_corpus_by_name
from api.services.ingestion import IngestionService
from api.services.context_service import build_context
from api.services.retrieval import RetrievalService
from api.services.embedder import Embedder

FIXTURE_DIR = ROOT / "benchmarks" / "agency_readiness" / "dev_fixtures"
BUDGET = 1200

FINDINGS = []


def note(text: str):
    FINDINGS.append(text)
    print(text)


async def brand_diag(brand: str, spec: dict):
    brand_dir = FIXTURE_DIR / brand
    if not brand_dir.exists():
        note(f"[{brand}] FIXTURE DIR MISSING: {brand_dir}")
        return
    meta = json.loads((brand_dir / "_meta.json").read_text())
    md_files = sorted([p for p in brand_dir.glob("*.md") if p.name != "_meta.json"])
    note(f"[{brand}] fixture dir={brand_dir.name} md_files={len(md_files)}")

    async with session_scope() as db:
        existing = await get_corpus_by_name(db, brand)
        if existing is None:
            await create_corpus(db, brand, f"{brand} diagnosis corpus")
            await db.commit()
        corpus_row = await get_corpus_by_name(db, brand)
        corpus_id = corpus_row["id"]
        note(f"[{brand}] corpus_id={corpus_id}")

        svc = IngestionService(require_embeddings=True)
        ingested = 0
        total_claims = 0
        total_chunks = 0
        warnings = []
        for fname in md_files:
            try:
                res = await svc.ingest_file(
                    fname.read_text(encoding="utf-8"),
                    path=f"{brand}/{fname.name}",
                    corpus_id=corpus_id,
                )
                ingested += 1
                total_claims += res.get("claim_count", 0)
                total_chunks += res.get("chunk_count", 0)
                for w in res.get("warnings", []):
                    warnings.append((fname.name, w))
            except Exception as exc:  # noqa: BLE001 - surface loudly
                note(f"[{brand}] INGESTION EXCEPTION on {fname.name}: {type(exc).__name__}: {exc}")
                raise
        await db.commit()
        note(f"[{brand}] ingested_docs={ingested} total_claims={total_claims} total_chunks={total_chunks}")
        if warnings:
            note(f"[{brand}] ingestion warnings ({len(warnings)}):")
            for fn, w in warnings[:10]:
                note(f"  {fn}: {w}")

        from sqlalchemy import text as sa_text
        r = await db.execute(sa_text(
            "SELECT count(*), count(embedding) FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE corpus_id=:c)"
        ), {"c": corpus_id})
        chunk_rows = r.fetchone()
        note(f"[{brand}] chunks_total={chunk_rows[0]} chunks_with_embedding={chunk_rows[1]}")

        r = await db.execute(sa_text("SELECT count(*), count(*) FILTER (WHERE event='DELETED') FROM memory_versions WHERE corpus_id=:c"), {"c": corpus_id})
        vr = r.fetchone()
        note(f"[{brand}] versions={vr[0]} deleted={vr[1]}")

        r = await db.execute(sa_text(
            "SELECT info_state, count(*) FROM memory_claims WHERE corpus_id=:c GROUP BY info_state ORDER BY info_state"
        ), {"c": corpus_id})
        note(f"[{brand}] claims by info_state: {dict(r.fetchall())}")

        gold = spec["brands"][brand]

        # --- D contexts for VE-04 and NS-01 ---
        prompts = [p for p in spec["prompts"] if p["brand"] == brand]
        for pr in prompts:
            pid = pr["id"]
            if pid not in ("VE-04", "NS-01"):
                continue
            prompt = pr["prompt"]
            try:
                pack, trace = await build_context(
                    db, brand, prompt, budget_tokens=BUDGET, k=8, strategy="hybrid_rrf",
                )
                ctx = pack.context
                note(f"[{brand}] {pid} D context_len={len(ctx)} empty={ctx==''} status={pack.status}")
                note(f"[{brand}] {pid} D context preview: {ctx[:700]!r}")
                note(f"[{brand}] {pid} D memories={len(pack.memories)} chunks={len(pack.chunks)} truncated={pack.truncated}")
            except Exception as exc:  # noqa: BLE001
                note(f"[{brand}] {pid} D EXCEPTION: {type(exc).__name__}: {exc}")
                raise

        # --- C and C+ on VE-04: top-10 chunks before/after filter ---
        if brand == "voltedge":
            ve04 = [p for p in prompts if p["id"] == "VE-04"]
            if ve04:
                prompt = ve04[0]["prompt"]
                embedder = Embedder()
                vector = embedder.embed_single(prompt)
                results = await RetrievalService(db, embedder=embedder).search(
                    vector, k=10, hybrid=True, rrf=True, query_text=prompt, corpus_id=corpus_id,
                )
                note(f"[{brand}] VE-04 C top-10 retrieved chunks:")
                for i, r_ in enumerate(results):
                    note(f"  [{i}] score={getattr(r_, 'score', None):.4f} doc_id={getattr(r_, 'doc_id', None)} heading={getattr(r_, 'heading_path', None)!r} text={r_.text[:90]!r}")

                # C+ filter: list chunks with owning doc path + status from _meta
                r2 = await db.execute(sa_text("""
                    SELECT d.path, count(c.id)
                    FROM chunks c
                    JOIN documents d ON d.id = c.doc_id
                    WHERE d.corpus_id = :c
                    GROUP BY d.path
                    ORDER BY d.path
                """), {"c": corpus_id})
                path_status = {}
                for p in md_files:
                    rel = f"{brand}/{p.name}"
                    path_status[rel] = (meta.get(rel) or {}).get("status", "approved")
                kept = 0
                removed = 0
                for path, nchunks in r2.fetchall():
                    status = path_status.get(path, "approved")
                    if status in {"draft", "rejected", "superseded"}:
                        removed += nchunks
                    else:
                        kept += nchunks
                    note(f"  {path} status={status} chunks={nchunks}")
                note(f"[{brand}] VE-04 C+: chunks_kept={kept} chunks_removed_by_doc_status={removed}")

        # --- B: gold position in concatenation ---
        docs_current = []
        for fname in md_files:
            text = fname.read_text(encoding="utf-8")
            status = (meta.get(f"{brand}/{fname.name}") or {}).get("status", "approved")
            if status == "approved":
                body = text.split("---", 2)[-1] if text.startswith("---\n") else text
                docs_current.append(body)
        dump = "\n\n".join(docs_current)
        note(f"[{brand}] B concatenation: docs={len(docs_current)} total_chars={len(dump)} total_words={len(dump.split())}")
        all_gold = []
        all_gold += gold.get("standing_prohibitions", [])
        all_gold += gold.get("required_voice_descriptors", [])
        all_gold += gold.get("approved_specs", [])
        all_gold += gold.get("superseded_v1_markers", [])
        all_gold += gold.get("unapproved_draft_markers", [])
        beyond_1200 = 0
        for g in all_gold:
            pos = dump.find(g)
            if pos >= 0:
                words_before = len(dump[:pos].split())
                if words_before >= 1200:
                    beyond_1200 += 1
            note(f"[{brand}] B gold '{g[:50]}' first_char_pos={pos} words_before={len(dump[:pos].split()) if pos>=0 else 'N/A'} present={pos>=0}")
        note(f"[{brand}] B gold items beyond first 1200 words: {beyond_1200}/{len(all_gold)}")


async def main():
    spec = json.loads((ROOT / "benchmarks" / "agency_readiness" / "sealed_spec.json").read_text())
    for brand in spec["brands"]:
        await brand_diag(brand, spec)


if __name__ == "__main__":
    asyncio.run(main())
    print("\n=== DIAGNOSIS COMPLETE ===")
