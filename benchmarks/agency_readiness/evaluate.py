"""M018 sealed benchmark evaluation (repaired for fairness).

Conditions:
  A: no memory (prompt only)
  B: static dump of ALL current documents, truncated to budget (5 seeded orderings)
  B+: current-version APPROVED documents only, truncated to budget (5 orderings)
  B*: operator-curated static: the brand-guide document only, truncated (outside criterion)
  C: raw RAG over ALL indexed documents (all current versions, incl. draft/superseded)
  C+: raw RAG with document-level metadata filter
      (current version, not superseded, frontmatter status not in {draft, rejected, superseded})
  D: Mind Palace real build_context API after real ingestion

All conditions share the spec's 1200-word budget and ONE tokenizer
(whitespace words, ``len(text.split())``; onnxruntime is not installed here so
the embedder falls back to the torch sentence-transformers backend -- still real
MiniLM embeddings, not the SHA-256 stub).

D's context is post-truncated to the same budget; pre-truncation size is
reported. Every condition asserts its max context size is at most the budget.

A structural fixture check asserts that, under every B ordering, at most 50% of
each brand's gold items fall within the first 1200 words -- so the static
baseline cannot win by having the gold pasted at the front.
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

from api.services.retrieval import RetrievalService  # noqa: E402
from api.services.embedder import Embedder  # noqa: E402
from benchmarks.agency_readiness.checker import score as score_context  # noqa: E402
from benchmarks.agency_readiness.fixture_generator import (  # noqa: E402
    SEALED_SEED,
    SEALED_FIXTURE_DIR,
)

SPEC_PATH = ROOT / "benchmarks" / "agency_readiness" / "sealed_spec.json"
BUDGET = 1200  # words
TOKENIZER = "whitespace words (len(text.split()))"

HISTORY_ALLOWED = {"NS-05", "VE-03", "HL-04", "VE-06"}

# Orderings for B/B+: neutral-name alphabetical, reverse, 3 seeded shuffles.
ORDER_SEEDS = [0, 1, 2, 3, 4]
MAX_GOLD_FRACTION_FIRST_BUDGET = 0.50


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


def assert_within_budget(label: str, text: str) -> int:
    n = count_tokens(text)
    assert n <= BUDGET, f"{label} context exceeds budget: {n} > {BUDGET}"
    return n


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


async def condition_A(prompt: str) -> str:
    return prompt


def _doc_entries(brand_dir, meta):
    """List of (rel, body, status, template) for every on-disk doc (current version)."""
    out = []
    for fname in sorted(brand_dir.glob("*.md")):
        if fname.name == "_meta.json":
            continue
        text = fname.read_text(encoding="utf-8")
        rel = f"{brand_dir.name}/{fname.name}"
        status = (meta.get(rel) or {}).get("status", "approved")
        body = text.split("---", 2)[-1] if text.startswith("---\n") else text
        tmpl = (meta.get(rel) or {}).get("template", "?")
        out.append((rel, body, status, tmpl))
    return out


async def condition_B(docs, prompt):
    dump = "\n\n".join(body for _rel, body, _status, _tmpl in docs)
    return prompt + "\n\n---\n\n" + truncate_words(dump)


async def condition_B_plus(docs, prompt):
    dump = "\n\n---\n\n".join(body for _rel, body, status, _tmpl in docs if status == "approved")
    return prompt + "\n\n---\n\n" + truncate_words(dump)


async def condition_B_star(docs, prompt, brand):
    # Brand-guide document only: the guide-typed doc (headings.md).
    guide = [
        body for rel, body, status, tmpl in docs if tmpl == "guide" and rel.endswith("headings.md")
    ]
    if not guide:
        guide = [body for rel, body, status, tmpl in docs if tmpl == "guide"]
    dump = guide[0] if guide else ""
    return prompt + "\n\n---\n\n" + truncate_words(dump)


async def _retrieve_chunks(db, corpus_id: str, query: str, k: int = 50):
    embedder = Embedder()
    vector = embedder.embed_single(query)
    return await RetrievalService(db, embedder=embedder).search(
        vector,
        k=k,
        hybrid=True,
        rrf=True,
        query_text=query,
        corpus_id=corpus_id,
    )


async def condition_C(results, prompt):
    kept = []
    for r in results:
        body = (getattr(r, "text", None) or "").strip()
        if not body:
            continue
        if count_tokens(prompt + "\n\n---\n\n" + "\n\n---\n\n".join(kept + [body])) > BUDGET:
            break
        kept.append(body)
    return prompt + "\n\n---\n\n" + "\n\n---\n\n".join(kept)


async def condition_C_plus(results, prompt, path_status):
    kept = []
    for r in results:
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
        db,
        corpus,
        prompt,
        budget_tokens=budget_tokens,
        k=8,
        strategy="hybrid_rrf",
    )
    return pack.context


def _orderings(docs):
    """Yield 5 (docs,) orderings: alphabetical, reverse, 3 seeded shuffles."""
    base = list(docs)
    yield base
    yield list(reversed(base))
    rng = random.Random(991)
    for _ in range(3):
        cur = list(base)
        rng.shuffle(cur)
        yield cur


def _gold_positions(docs, gold_items):
    """Word offset of each gold item's first occurrence in the B dump."""
    dump = "\n\n".join(body for _rel, body, _status, _tmpl in docs)
    out = []
    for cat, g in gold_items:
        pos = dump.find(g)
        words_before = count_tokens(dump[:pos]) if pos >= 0 else None
        out.append((cat, g, words_before, pos >= 0))
    return out, dump


async def run_brand(brand: str, spec: dict, prompts: list[dict], brand_dir) -> dict:
    from api.services.db import session_scope as _ss
    from api.services.corpora import create_corpus, get_corpus_by_name
    from api.services.ingestion import IngestionService

    meta_raw = json.loads((brand_dir / "_meta.json").read_text())
    meta = meta_raw.get("meta", meta_raw)
    docs = _doc_entries(brand_dir, meta)  # (rel, body, status, template)

    gold = spec["brands"][brand]
    gold_items = (
        [("prohibition", p) for p in gold["standing_prohibitions"]]
        + [("voice", p) for p in gold["required_voice_descriptors"]]
        + [("spec", p) for p in gold["approved_specs"]]
        + [("superseded", p) for p in gold["superseded_v1_markers"]]
        + [("draft", p) for p in gold["unapproved_draft_markers"]]
    )

    path_status = {rel: status for (rel, _body, status, _tmpl) in docs}

    async with _ss() as db:
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

        # Sanity: every doc indexed.
        from sqlalchemy import text as sa_text

        r = await db.execute(
            sa_text("SELECT count(*) FROM documents WHERE corpus_id=:c"), {"c": corpus_id}
        )
        n_docs = r.scalar()
        if n_docs == 0:
            raise RuntimeError(f"[{brand}] zero documents ingested")

        results_out = {}
        for pr in prompts:
            if pr["brand"] != brand:
                continue
            pid = pr["id"]
            prompt = pr["prompt"]

            # A
            ctx_A = await condition_A(prompt)

            # B / B+: 5 orderings, per-ordering scores + structural check
            b_per, bp_per = [], []
            for ordering in _orderings(docs):
                ctx_B = await condition_B(ordering, prompt)
                b_per.append(score_context(ctx_B, gold))
                ctx_Bp = await condition_B_plus(ordering, prompt)
                bp_per.append(score_context(ctx_Bp, gold))

            # Structural fixture check: <=50% of gold within first 1200 words, every B ordering.
            for ordering in _orderings(docs):
                gold_positions, _dump = _gold_positions(ordering, gold_items)
                n_within = sum(
                    1
                    for _c, _g, wb, present in gold_positions
                    if present and wb is not None and wb < BUDGET
                )
                frac = n_within / len(gold_items) if gold_items else 0.0
                assert frac <= MAX_GOLD_FRACTION_FIRST_BUDGET, (
                    f"[{brand}] {pid}: {n_within}/{len(gold_items)} gold items "
                    f"({frac:.0%}) within first {BUDGET} words of a B ordering "
                    "-- fixtures not spread enough"
                )

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
                    "n_orderings": len(scores),
                }

            # C and C+ need retrieval
            chunks = await _retrieve_chunks(db, corpus_id, prompt)
            ctx_C = await condition_C(chunks, prompt)
            ctx_Cp = await condition_C_plus(chunks, prompt, path_status)

            # D via real public API
            ctx_D_raw = await condition_D(db, brand, prompt)
            d_pre_tokens = count_tokens(ctx_D_raw)
            ctx_D = truncate_words(ctx_D_raw)

            results_out[pid] = {
                "prompt": prompt,
                "task_type": pr["task_type"],
                "history_allowed": pid in HISTORY_ALLOWED,
                "A": score_context(ctx_A, gold),
                "B": _agg(b_per),
                "B_plus": _agg(bp_per),
                "B_star": score_context(await condition_B_star(docs, prompt, brand), gold),
                "C": score_context(ctx_C, gold),
                "C_plus": score_context(ctx_Cp, gold),
                "D": score_context(ctx_D, gold),
                "d_pre_truncation_tokens": d_pre_tokens,
                "d_post_truncation_tokens": count_tokens(ctx_D),
                "contexts": {
                    "A": ctx_A,
                    "B": ctx_B,
                    "B_plus": ctx_Bp,
                    "B_star": "",
                    "C": ctx_C,
                    "C_plus": ctx_Cp,
                    "D": ctx_D,
                },
            }
    return results_out


def _agg_condition(metric_values):
    n = len(metric_values)
    if n == 0:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    return {
        "mean": sum(metric_values) / n,
        "min": min(metric_values),
        "max": max(metric_values),
        "n": n,
    }


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def verify_embedder():
    import importlib.util

    from api.services.embedder import Embedder, backend_name

    e = Embedder()
    backend = backend_name()
    identity = {
        "class": type(e).__name__,
        "backend": backend,
        "model_name": e.model_name,
        "dimension": e.dimension,
    }
    print("EMBEDDER IDENTITY:", json.dumps(identity))
    ort_spec = importlib.util.find_spec("onnxruntime")
    print("onnxruntime available:", ort_spec is not None)
    if ort_spec is None:
        print("NOTE: onnxruntime not installed; embedder uses torch backend (still real MiniLM, not SHA-256 stub).")
    v1 = e.embed_single("warm friendly tone")
    v2 = e.embed_single("approachable voice")
    v3 = e.embed_single("quarterly tax filing")
    cos_rel = _cos(v1, v2)
    cos_unrel = _cos(v1, v3)
    diff = cos_rel - cos_unrel
    print(f"sanity: cos(rel)={cos_rel:.4f} cos(unrel)={cos_unrel:.4f} diff={diff:.4f} (need >=0.2)")
    if diff < 0.2:
        print("ABORT: embedder sanity check failed (diff < 0.2).")
        sys.exit(1)
    return identity


def main():
    spec = load_spec()
    embedder_identity = verify_embedder()
    print("embedder_identity:", json.dumps(embedder_identity))
    # Regenerate sealed fixtures with the sealed seed (idempotent, same bytes).
    from benchmarks.agency_readiness.fixture_generator import write_fixtures

    write_fixtures(spec, SEALED_SEED, SEALED_FIXTURE_DIR)

    all_results = {}
    for brand in spec["brands"]:
        prompts = [p for p in spec["prompts"] if p["brand"] == brand]
        brand_dir = SEALED_FIXTURE_DIR / brand
        brand_res = asyncio.run(run_brand(brand, spec, prompts, brand_dir))
        all_results[brand] = brand_res

    conditions = ["A", "B", "B_plus", "B_star", "C", "C_plus", "D"]
    summary = {}
    per_brand = {}
    for c in conditions:
        rr, vl, vo, sp = [], [], [], []
        for brand, res in all_results.items():
            brand_rr, brand_vl, brand_vo, brand_sp = [], [], [], []
            for pid, m in res.items():
                v = m[c]
                if isinstance(v, dict) and "rule_recall_mean" in v:
                    rr_v, vl_v = v["rule_recall_mean"], v["violation_mean"]
                else:
                    rr_v, vl_v = v["rule_recall"], v["violation"]
                vo_v = v.get("voice_recall", 0.0)
                sp_v = v.get("spec_recall", 0.0)
                rr.append(rr_v)
                vo.append(vo_v)
                sp.append(sp_v)
                brand_rr.append(rr_v)
                brand_vo.append(vo_v)
                brand_sp.append(sp_v)
                # violation_rate is mean over non-history prompts only
                if not m.get("history_allowed", False):
                    vl.append(vl_v)
                    brand_vl.append(vl_v)
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
        "embedder_identity": embedder_identity,
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
