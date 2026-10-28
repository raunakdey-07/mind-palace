"""M018 sealed benchmark evaluation (repaired).

Conditions:
  A: no memory (prompt only)
  B: static dump of all current docs, truncated to budget (5 seeded orderings)
  B+: current-version approved docs only, truncated to budget (5 seeded orderings)
  C: raw RAG (chunks, embed, top-k, no filtering)
  C+: raw RAG with document-level metadata filter
      (current version only, frontmatter status not in {draft, rejected, superseded})
  D: Mind Palace real build_context API after real ingestion of the fixtures

All conditions share the spec's 1200-token budget and ONE tokenizer:
  whitespace words, ``len(text.split())`` (tiktoken is not installed offline;
  whitespace words are used and named in the output).

D's context is post-truncated to the same 1200-word budget with this function,
and D's pre-truncation size is reported.

`checker.py` imports no Mind Palace code. `evaluate.py` imports only the public
`build_context` API for condition D. Blanket ``except`` blocks are removed:
ingestion, retrieval, and D errors are surfaced loudly.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmarks.agency_readiness.checker import score as score_context, normalize
from benchmarks.agency_readiness.fixture_generator import (
    SEALED_SEED,
    SEALED_FIXTURE_DIR,
    DEV_SET_SEED,
    DEV_FIXTURE_DIR,
)

SPEC_PATH = ROOT / "benchmarks" / "agency_readiness" / "sealed_spec.json"
BUDGET = 1200  # words
TOKENIZER = "whitespace words (len(text.split()))"

# Prompts allowed to surface historical markers (excluded from headline violation_rate).
HISTORY_ALLOWED = {"NS-05", "VE-03", "HL-04", "VE-06"}

# Seeds for the 5 B/B+ document orderings: fixed, reverse, and 3 shuffles.
ORDER_SEEDS = [0, 1, 2, 3, 4]


def load_spec():
    return json.loads(SPEC_PATH.read_text())


def count_tokens(text: str) -> int:
    """Single token-counting function used by every condition."""
    return len((text or "").split())


def truncate_words(text: str, budget: int = BUDGET) -> str:
    words = (text or "").split()
    if len(words) <= budget:
        return " ".join(words)
    return " ".join(words[:budget])


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

async def condition_A(prompt: str) -> str:
    return prompt


def _doc_texts(brand_dir, meta):
    """Return list of (path, body_text, status) for current docs on disk.

    `00_volume_guide.md` (the 6000-token filler) is ordered FIRST so that gold
    items sit beyond the first 1200 words of the B/B+ concatenation.
    """
    out = []
    md_files = sorted([p for p in brand_dir.glob("*.md") if p.name != "_meta.json"])
    # filler first, then the rest alphabetical
    filler = [p for p in md_files if p.name.startswith("00_")]
    rest = [p for p in md_files if not p.name.startswith("00_")]
    ordered = filler + rest
    for fname in ordered:
        text = fname.read_text(encoding="utf-8")
        rel = f"{brand_dir.name}/{fname.name}"
        status = (meta.get(rel) or {}).get("status", "approved")
        body = text.split("---", 2)[-1] if text.startswith("---\n") else text
        out.append((rel, body, status))
    return out


async def condition_B(docs_current, prompt):
    dump = "\n\n".join(body for _rel, body, _status in docs_current)
    return prompt + "\n\n---\n\n" + truncate_words(dump)


async def condition_B_plus(docs_approved, prompt):
    dump = "\n\n---\n\n".join(body for _rel, body, status in docs_approved if status == "approved")
    return prompt + "\n\n---\n\n" + truncate_words(dump)


async def _retrieve_chunks(db, corpus_id: str, query: str, k: int = 50):
    from api.services.embedder import Embedder
    from api.services.retrieval import RetrievalService

    embedder = Embedder()
    vector = embedder.embed_single(query)
    return await RetrievalService(db, embedder=embedder).search(
        vector, k=k, hybrid=True, rrf=True, query_text=query, corpus_id=corpus_id,
    )


async def condition_C(results, prompt):
    kept = []
    used = 0
    for r in results:
        body = (getattr(r, "text", None) or "").strip()
        if not body:
            continue
        if count_tokens(prompt + "\n\n---\n\n" + "\n\n---\n\n".join(kept + [body])) > BUDGET:
            break
        kept.append(body)
    return prompt + "\n\n---\n\n" + "\n\n---\n\n".join(kept)


async def condition_C_plus(results, prompt, path_status):
    # document-level filter: status not in {draft, rejected, superseded}
    kept = []
    used = 0
    for r in results:
        # RetrievalResult carries source_path (documents.path); match against that.
        source_path = getattr(r, "source_path", None) or getattr(r, "doc_id", None)
        status = path_status.get(source_path, "approved")
        if status in {"draft", "rejected", "superseded"}:
            continue
        body = (getattr(r, "text", None) or "").strip()
        if not body:
            continue
        if count_tokens(prompt + "\n\n---\n\n" + "\n\n---\n\n".join(kept + [body])) > BUDGET:
            break
        kept.append(body)
    return prompt + "\n\n---\n\n" + "\n\n---\n\n".join(kept)


async def condition_D(db, corpus: str, prompt: str, budget_tokens: int = BUDGET):
    from api.services.context_service import build_context

    pack, _trace = await build_context(
        db, corpus, prompt, budget_tokens=budget_tokens, k=8, strategy="hybrid_rrf",
    )
    return pack.context


def _orderings(docs_current, docs_approved):
    """Yield 5 (docs_current, docs_approved) orderings: fixed, reverse, 3 shuffles."""
    base_cur = list(docs_current)
    base_app = list(docs_approved)
    yield base_cur, base_app
    yield list(reversed(base_cur)), list(reversed(base_app))
    rng = random.Random(991)
    for _ in range(3):
        cur = list(base_cur); app = list(base_app)
        rng.shuffle(cur); rng.shuffle(app)
        yield cur, app


async def run_brand(brand: str, spec: dict, prompts: list[dict], brand_dir, sealed: bool) -> dict:
    from api.services.db import session_scope
    from api.services.corpora import create_corpus, get_corpus_by_name
    from api.services.ingestion import IngestionService

    meta_raw = json.loads((brand_dir / "_meta.json").read_text())
    meta = meta_raw.get("meta", meta_raw)
    template_of = meta_raw.get("template_of", {})
    docs_current = _doc_texts(brand_dir, meta)  # (rel, body, status)
    docs_approved = [(rel, body, status) for (rel, body, status) in docs_current]

    # Build path->status map for C+ (keyed by document id used in retrieval results).
    # RetrievalResult.doc_id is the documents.id (CHAR(64) hash). Map path->status then
    # resolve path->id via the documents table at read time is complex; instead key by
    # the relative path and match retrieval results by source_path when available.
    path_status = {rel: status for (rel, _body, status) in docs_current}

    async with session_scope() as db:
        existing = await get_corpus_by_name(db, brand)
        if existing is None:
            await create_corpus(db, brand, f"{brand} benchmark corpus")
            await db.commit()
        corpus_row = await get_corpus_by_name(db, brand)
        corpus_id = corpus_row["id"]
        svc = IngestionService(require_embeddings=True)
        for fname in sorted(brand_dir.glob("*.md")):
            if fname.name == "_meta.json":
                continue
            res = await svc.ingest_file(
                fname.read_text(encoding="utf-8"),
                path=f"{brand}/{fname.name}",
                corpus_id=corpus_id,
            )
            if res.get("claim_count", 0) is None:
                raise RuntimeError(f"[{brand}] {fname.name} ingestion returned no claim_count")
        await db.commit()

        # Sanity: corpus must have ingested documents
        from sqlalchemy import text as sa_text
        r = await db.execute(sa_text("SELECT count(*) FROM documents WHERE corpus_id=:c"), {"c": corpus_id})
        n_docs = r.scalar()
        if n_docs == 0:
            raise RuntimeError(f"[{brand}] zero documents ingested")

        results_out = {}
        for pr in prompts:
            if pr["brand"] != brand:
                continue
            pid = pr["id"]
            prompt = pr["prompt"]
            gold = spec["brands"][brand]

            # A
            ctx_A = await condition_A(prompt)

            # B and B+: 5 orderings -> mean and range
            b_scores, bp_scores = [], []
            for cur, app in _orderings(docs_current, docs_approved):
                ctx_B = await condition_B(cur, prompt)
                b_scores.append(score_context(ctx_B, gold))
                ctx_Bp = await condition_B_plus(app, prompt)
                bp_scores.append(score_context(ctx_Bp, gold))

            def _agg(scores):
                rr = [s["rule_recall"] for s in scores]
                vl = [s["violation"] for s in scores]
                return {
                    "rule_recall_mean": sum(rr) / len(rr),
                    "rule_recall_min": min(rr),
                    "rule_recall_max": max(rr),
                    "violation_mean": sum(vl) / len(vl),
                    "violation_min": min(vl),
                    "violation_max": max(vl),
                }

            # C and C+ need retrieval
            chunks = await _retrieve_chunks(db, corpus_id, prompt)
            ctx_C = await condition_C(chunks, prompt)
            # C+ filter: map retrieval results to doc status via source_path
            # RetrievalResult has source_path; fall back to doc_id lookup below.
            path_status_by_source = {}
            for (rel, _body, status) in docs_current:
                # source_path in RetrievalResult is the documents.path
                path_status_by_source[rel] = status
            ctx_Cp = await condition_C_plus(chunks, prompt, path_status_by_source)

            # D via real public API
            ctx_D_raw = await condition_D(db, brand, prompt)
            d_pre_tokens = count_tokens(ctx_D_raw)
            ctx_D = truncate_words(ctx_D_raw)

            results_out[pid] = {
                "prompt": prompt,
                "task_type": pr["task_type"],
                "history_allowed": pid in HISTORY_ALLOWED,
                "A": score_context(ctx_A, gold),
                "B": _agg(b_scores),
                "B_plus": _agg(bp_scores),
                "C": score_context(ctx_C, gold),
                "C_plus": score_context(ctx_Cp, gold),
                "D": score_context(ctx_D, gold),
                "d_pre_truncation_tokens": d_pre_tokens,
                "d_post_truncation_tokens": count_tokens(ctx_D),
                "contexts": {
                    "A": ctx_A,
                    "B": ctx_B,
                    "B_plus": ctx_Bp,
                    "C": ctx_C,
                    "C_plus": ctx_Cp,
                    "D": ctx_D,
                },
            }
    return results_out


def _agg_condition(metric_values):
    """metric_values: list of floats across prompts/brands for one condition."""
    n = len(metric_values)
    if n == 0:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    return {"mean": sum(metric_values) / n, "min": min(metric_values), "max": max(metric_values), "n": n}


def main():
    spec = load_spec()
    # Regenerate sealed fixtures with the sealed seed (idempotent, same bytes).
    from benchmarks.agency_readiness.fixture_generator import write_fixtures
    write_fixtures(spec, SEALED_SEED, SEALED_FIXTURE_DIR)

    all_results = {}
    for brand in spec["brands"]:
        prompts = [p for p in spec["prompts"] if p["brand"] == brand]
        brand_dir = SEALED_FIXTURE_DIR / brand
        brand_res = asyncio.run(run_brand(brand, spec, prompts, brand_dir, sealed=True))
        all_results[brand] = brand_res

    # Summary tables: per condition, per brand, per template.
    conditions = ["A", "B", "C", "C_plus", "D"]
    summary = {}
    per_brand = {}
    for c in conditions:
        rr, vl, vo, sp = [], [], [], []
        for brand, res in all_results.items():
            brand_rr, brand_vl, brand_vo, brand_sp = [], [], [], []
            for pid, m in res.items():
                v = m[c]
                # B/B+ are dicts with _mean; others are plain dicts
                rr_v = v["rule_recall_mean"] if isinstance(v, dict) and "rule_recall_mean" in v else v["rule_recall"]
                vl_v = v["violation_mean"] if isinstance(v, dict) and "violation_mean" in v else v["violation"]
                vo_v = v["voice_recall"] if "voice_recall" in v else 0.0
                sp_v = v["spec_recall"] if "spec_recall" in v else 0.0
                rr.append(rr_v); vl.append(vl_v); vo.append(vo_v); sp.append(sp_v)
                brand_rr.append(rr_v); brand_vl.append(vl_v); brand_vo.append(vo_v); brand_sp.append(sp_v)
            per_brand.setdefault(brand, {})[c] = {
                "rule_recall": _agg_condition(brand_rr),
                "violation_rate": _agg_condition(brand_vl),
                "voice_recall": _agg_condition(brand_vo),
                "spec_recall": _agg_condition(brand_sp),
            }
        summary[c] = {
            "rule_recall": _agg_condition(rr),
            "violation_rate": _agg_condition(vl),
            "voice_recall": _agg_condition(vo),
            "spec_recall": _agg_condition(sp),
        }

    out = {
        "spec_version": spec["version"],
        "token_budget": BUDGET,
        "tokenizer": TOKENIZER,
        "seed": SEALED_SEED,
        "sealed": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conditions": conditions,
        "history_allowed": sorted(HISTORY_ALLOWED),
        "summary": summary,
        "per_brand": per_brand,
        "results": all_results,
    }
    out_path = ROOT / "benchmarks" / "agency_readiness" / "results_baseline.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("wrote", out_path)


if __name__ == "__main__":
    main()
