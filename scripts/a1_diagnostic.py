"""A1 diagnostic: benchmark evidence on CURRENT dev fixtures (harness-only/main).

Runs against dev_fixtures only. Does NOT touch sealed_runs.log.
Reports chunk census, C/C+ per-prompt, B/B+ per-ordering analysis.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.services.db import session_scope
from api.services.corpora import create_corpus, get_corpus_by_name
from api.services.ingestion import IngestionService
from api.services.retrieval import RetrievalService
from api.services.embedder import Embedder
from benchmarks.agency_readiness.checker import score as score_context

DEV_FIXTURE_DIR = ROOT / "benchmarks" / "agency_readiness" / "dev_fixtures"
SPEC_PATH = ROOT / "benchmarks" / "agency_readiness" / "sealed_spec.json"
BUDGET = 1200
HISTORY_ALLOWED = {"NS-05", "VE-03", "HL-04", "VE-06"}


def note(text):
    print(text)


def count_tokens(text):
    return len((text or "").split())


def truncate_words(text, budget=BUDGET):
    words = (text or "").split()
    if len(words) <= budget:
        return " ".join(words)
    return " ".join(words[:budget])


async def brand_a1(brand, spec, prompts):
    brand_dir = DEV_FIXTURE_DIR / brand
    meta_raw = json.loads((brand_dir / "_meta.json").read_text())
    meta = meta_raw.get("meta", meta_raw)
    template_of = meta_raw.get("template_of", {})

    docs = []
    for fname in sorted(brand_dir.glob("*.md")):
        if fname.name == "_meta.json":
            continue
        text = fname.read_text(encoding="utf-8")
        rel = f"{brand}/{fname.name}"
        status = (meta.get(rel) or {}).get("status", "approved")
        body = text.split("---", 2)[-1] if text.startswith("---\n") else text
        tmpl = template_of.get(rel, "?")
        docs.append((rel, body, status, tmpl, text))

    gold = spec["brands"][brand]
    all_gold = ([("prohibition", p) for p in gold["standing_prohibitions"]]
                + [("voice", p) for p in gold["required_voice_descriptors"]]
                + [("spec", p) for p in gold["approved_specs"]]
                + [("superseded", p) for p in gold["superseded_v1_markers"]]
                + [("draft", p) for p in gold["unapproved_draft_markers"]])

    note(f"\n===== {brand} (dev fixtures) =====")
    for rel, body, status, tmpl, _text in docs:
        note(f"  {rel} status={status} template={tmpl} words={count_tokens(body)}")

    async with session_scope() as db:
        existing = await get_corpus_by_name(db, brand)
        if existing is None:
            await create_corpus(db, brand, f"{brand} a1-diagnosis corpus")
            await db.commit()
        corpus_row = await get_corpus_by_name(db, brand)
        corpus_id = corpus_row["id"]
        note(f"corpus_id={corpus_id}")

        svc = IngestionService(require_embeddings=True)
        for fname in sorted(brand_dir.glob("*.md")):
            if fname.name == "_meta.json":
                continue
            await svc.ingest_file(
                fname.read_text(encoding="utf-8"),
                path=f"{brand}/{fname.name}",
                corpus_id=corpus_id,
            )
        await db.commit()

        from sqlalchemy import text as sa_text
        # Chunk census from the `chunks` table (denormalized, latest per doc).
        r = await db.execute(
            sa_text(
                "SELECT c.id, c.text, d.path, d.status "
                "FROM chunks c JOIN documents d ON d.id=c.doc_id "
                "WHERE d.corpus_id=:c ORDER BY d.path, c.order_index"
            ),
            {"c": corpus_id},
        )
        rows = r.fetchall()
        note(f"total chunks indexed: {len(rows)}")
        by_template = {}
        draft_chunks = 0
        for chunk_id, ctext, path, doc_status in rows:
            tmpl = template_of.get(path, "?")
            by_template.setdefault(tmpl, 0)
            by_template[tmpl] += 1
            if doc_status == "draft":
                draft_chunks += 1
        note("chunks by template (doc->template mapping):")
        for tmpl, n in sorted(by_template.items()):
            note(f"  {tmpl}: {n}")
        note(f"chunks from draft documents: {draft_chunks}")
        note("=> ONE version per file ingested (current only); NO separate v1 versions indexed")
        note("=> draft files: indexed as chunks (september_brainstorm.md)")
        note("=> documents.status column reflects ingestion-time status; C+ maps source_path->frontmatter status")

        # C / C+ retrieval per prompt
        embedder = Embedder()
        path_status = {rel: status for (rel, _b, status, _t, _x) in docs}
        note("\nC / C+ per prompt (top-50 retrieval, budget=1200 words):")
        for pr in prompts:
            pid = pr["id"]
            prompt = pr["prompt"]
            vector = embedder.embed_single(prompt)
            results = await RetrievalService(db, embedder=embedder).search(
                vector, k=50, hybrid=True, rrf=True, query_text=prompt, corpus_id=corpus_id,
            )
            c_kept, c_used = [], 0
            for r_ in results:
                body = (getattr(r_, "text", None) or "").strip()
                if not body:
                    continue
                if count_tokens(prompt + "\n\n---\n\n" + "\n\n---\n\n".join(c_kept + [body])) > BUDGET:
                    break
                c_kept.append(body)
            cp_kept, cp_removed = [], 0
            for r_ in results:
                source_path = getattr(r_, "source_path", None) or getattr(r_, "doc_id", None)
                status = path_status.get(source_path, "approved")
                if status in {"draft", "rejected", "superseded"}:
                    cp_removed += 1
                    continue
                body = (getattr(r_, "text", None) or "").strip()
                if not body:
                    continue
                if count_tokens(prompt + "\n\n---\n\n" + "\n\n---\n\n".join(cp_kept + [body])) > BUDGET:
                    break
                cp_kept.append(body)
            m_c = score_context(prompt + "\n\n---\n\n" + "\n\n---\n\n".join(c_kept), gold)
            m_cp = score_context(prompt + "\n\n---\n\n" + "\n\n---\n\n".join(cp_kept), gold)
            note(f"  {pid}: C keep={len(c_kept)} rule={m_c['rule_recall']:.2f} viol={m_c['violation']} | "
                 f"C+ keep={len(cp_kept)} removed={cp_removed} rule={m_cp['rule_recall']:.2f} viol={m_cp['violation']}")

    # ---- B / B+ per-ordering analysis (text-only) ----
    note("\nB / B+ per-ordering (gold word positions within first 1200 words):")
    orderings = []
    base = list(docs)
    orderings.append(("fixed", base, base))
    orderings.append(("reverse", list(reversed(base)), list(reversed(base))))
    rng = random.Random(991)
    for i in range(3):
        cur = list(base); app = list(base)
        rng.shuffle(cur); rng.shuffle(app)
        orderings.append((f"shuffle{i+1}", cur, app))

    all_gold_texts = ([("prohibition", p) for p in gold["standing_prohibitions"]]
                      + [("voice", p) for p in gold["required_voice_descriptors"]]
                      + [("spec", p) for p in gold["approved_specs"]]
                      + [("superseded", p) for p in gold["superseded_v1_markers"]]
                      + [("draft", p) for p in gold["unapproved_draft_markers"]])

    for oname, cur_docs, app_docs in orderings:
        b_dump = "\n\n".join(body for _r, body, _s, _t, _x in cur_docs)
        bp_dump = "\n\n---\n\n".join(body for _r, body, s, _t, _x in app_docs if s == "approved")
        b_words = count_tokens(b_dump)
        bp_words = count_tokens(bp_dump)
        # gold positions in B dump
        positions = []
        for cat, g in all_gold_texts:
            pos = b_dump.find(g)
            positions.append((cat, g, count_tokens(b_dump[:pos]) if pos >= 0 else None, pos >= 0))
        beyond = sum(1 for _c, _g, pw, present in positions if present and pw is not None and pw >= 1200)
        note(f"  {oname}: B words={b_words} B+ words={bp_words} gold_beyond_1200words={beyond}/{len(all_gold_texts)}")
        for cat, g, pw, present in positions:
            mark = "BEYOND" if (present and pw is not None and pw >= 1200) else (f"pos={pw}" if present else "absent")
            note(f"    {cat} '{g[:40]}': {mark}")


async def main():
    spec = json.loads(SPEC_PATH.read_text())
    for brand in spec["brands"]:
        prompts = [p for p in spec["prompts"] if p["brand"] == brand]
        await brand_a1(brand, spec, prompts)


if __name__ == "__main__":
    asyncio.run(main())
    print("\n=== A1 DIAGNOSTIC COMPLETE ===")
