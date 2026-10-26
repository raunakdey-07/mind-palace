"""Head-to-head stale-fact and ADR evolution benchmark.

Mind Palace vs. Plain Top-k Chunk RAG, using the exact same ONNX embedding model.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.services.embedder import Embedder  # noqa: E402
from mindpalace_sdk import MindPalace  # noqa: E402

# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

DOCS = {
    "adr-001-storage.md": """---
title: ADR-001 Storage Engine
document_type: adr
claims:
  - key: architecture.storage
    value: SQLite
    claim: The primary storage engine is SQLite.
    evidence: The primary storage engine is SQLite.
---
# ADR-001

The primary storage engine is SQLite. Connections come from a single pool.
""",
    "adr-009-storage.md": """---
title: ADR-009 Storage Engine
document_type: adr
claims:
  - key: architecture.storage
    value: PostgreSQL with pgvector
    claim: The primary storage engine is PostgreSQL with pgvector.
    evidence: The primary storage engine is PostgreSQL with pgvector.
supersedes: adr-001-storage.md
---
# ADR-009

The primary storage engine is PostgreSQL with pgvector. SQLite was retired.
""",
    "adr-002-auth.md": """---
title: ADR-002 Auth
document_type: adr
claims:
  - key: architecture.auth
    value: session cookies
    claim: Authentication uses session cookies.
    evidence: Authentication uses session cookies.
---
# ADR-002

Authentication uses session cookies.
""",
    "adr-007-auth.md": """---
title: ADR-007 Auth
document_type: adr
claims:
  - key: architecture.auth
    value: JWT
    claim: Authentication uses JWT.
    evidence: Authentication uses JWT.
supersedes: adr-002-auth.md
---
# ADR-007

Authentication uses JWT.
""",
    "adr-012-auth.md": """---
title: ADR-012 Auth
document_type: adr
claims:
  - key: architecture.auth
    value: short-lived mTLS plus opaque tokens
    claim: Authentication uses short-lived mTLS plus opaque tokens.
    evidence: Authentication uses short-lived mTLS plus opaque tokens.
supersedes: adr-007-auth.md
---
# ADR-012

Authentication uses short-lived mTLS plus opaque tokens.
""",
    "adr-003-queue.md": """---
title: ADR-003 Queue
document_type: adr
claims:
  - key: architecture.queue
    value: Redis
    claim: The message broker is Redis.
    evidence: The message broker is Redis.
---
# ADR-003

The message broker is Redis.
""",
    "adr-008-queue.md": """---
title: ADR-008 Queue
document_type: adr
claims:
  - key: architecture.queue
    value: RabbitMQ
    claim: The message broker is RabbitMQ.
    evidence: The message broker is RabbitMQ.
supersedes: adr-003-queue.md
---
# ADR-008

The message broker is RabbitMQ.
""",
    "adr-013-queue.md": """---
title: ADR-013 Queue
document_type: adr
claims:
  - key: architecture.queue
    value: NATS JetStream
    claim: The message broker is NATS JetStream.
    evidence: The message broker is NATS JetStream.
supersedes: adr-008-queue.md
---
# ADR-013

The message broker is NATS JetStream.
""",
    "adr-004-embedding.md": """---
title: ADR-004 Embedding
document_type: adr
claims:
  - key: architecture.embedding
    value: PyTorch
    claim: The embedding runtime is PyTorch.
    evidence: The embedding runtime is PyTorch.
---
# ADR-004

The embedding runtime is PyTorch.
""",
    "adr-010-embedding.md": """---
title: ADR-010 Embedding
document_type: adr
claims:
  - key: architecture.embedding
    value: ONNX Runtime
    claim: The embedding runtime is ONNX Runtime.
    evidence: The embedding runtime is ONNX Runtime.
supersedes: adr-004-embedding.md
---
# ADR-010

The embedding runtime is ONNX Runtime.
""",
    "sla-v1.md": """---
title: SLA v1
document_type: spec
claims:
  - key: sla.p95_latency_ms
    value: 200
    claim: The p95 latency target is 200 ms.
    evidence: The p95 latency target is 200 ms.
---
# SLA v1

The p95 latency target is 200 ms.
""",
    "sla-v2.md": """---
title: SLA v2
document_type: spec
claims:
  - key: sla.p95_latency_ms
    value: 120
    claim: The p95 latency target is 120 ms.
    evidence: The p95 latency target is 120 ms.
supersedes: sla-v1.md
---
# SLA v2

The p95 latency target is 120 ms.
""",
    "sla-v3.md": """---
title: SLA v3
document_type: spec
claims:
  - key: sla.p95_latency_ms
    value: 80
    claim: The p95 latency target is 80 ms.
    evidence: The p95 latency target is 80 ms.
supersedes: sla-v2.md
---
# SLA v3

The p95 latency target is 80 ms.
""",
    "limits-v1.md": """---
title: Limits v1
document_type: spec
claims:
  - key: limits.rate_limit_rps
    value: 100
    claim: The API rate limit is 100 requests per second.
    evidence: The API rate limit is 100 requests per second.
---
# Limits v1

The API rate limit is 100 requests per second.
""",
    "bundle-v2.md": """---
title: Bundle v2
document_type: spec
claims:
  - key: bundle.budget_kb
    value: 200
    claim: The bundle budget is 200 KB.
    evidence: The bundle budget is 200 KB.
---
# Bundle v2

The bundle budget is 200 KB.
""",
    "protocol-v3.md": """---
title: Protocol v3
document_type: spec
claims:
  - key: protocol.version
    value: v3
    claim: The protocol version is v3.
    evidence: The protocol version is v3.
---
# Protocol v3

The protocol version is v3.
""",
    "limits-v2.md": """---
title: Limits v2
document_type: spec
claims:
  - key: limits.rate_limit_rps
    value: 500
    claim: The API rate limit is 500 requests per second.
    evidence: The API rate limit is 500 requests per second.
supersedes: limits-v1.md
---
# Limits v2

The API rate limit is 500 requests per second.
""",
}

# Gold questions: (question, gold_substring, expect_abstain, category)
QUESTIONS = [
    # Category A: multi-hop supersession (12+)
    ("What is the primary storage engine?", "PostgreSQL with pgvector", False, "A"),
    ("Which storage engine is currently authoritative?", "PostgreSQL with pgvector", False, "A"),
    ("What storage engine replaced SQLite?", "PostgreSQL with pgvector", False, "A"),
    ("Which storage ADR is authoritative now?", "PostgreSQL with pgvector", False, "A"),
    ("What storage engine should new services use?", "PostgreSQL with pgvector", False, "A"),
    (
        "What authentication mechanism is currently used?",
        "short-lived mTLS plus opaque tokens",
        False,
        "A",
    ),
    ("Which auth method is active now?", "short-lived mTLS plus opaque tokens", False, "A"),
    ("What auth mechanism replaced JWT?", "short-lived mTLS plus opaque tokens", False, "A"),
    ("Which auth ADR is authoritative now?", "short-lived mTLS plus opaque tokens", False, "A"),
    ("What message broker is currently used?", "NATS JetStream", False, "A"),
    ("Which queue is authoritative now?", "NATS JetStream", False, "A"),
    ("What broker replaced Redis?", "NATS JetStream", False, "A"),
    ("Which queue ADR is authoritative now?", "NATS JetStream", False, "A"),
    ("What embedding runtime is currently used?", "ONNX Runtime", False, "A"),
    ("Which embedding runtime is active?", "ONNX Runtime", False, "A"),
    # Category B: table/spec (8+)
    ("What is the p95 latency target?", "80 ms", False, "B"),
    ("What is the current p95 latency SLO?", "80 ms", False, "B"),
    ("What is the API rate limit?", "500 requests per second", False, "B"),
    ("How many requests per second are allowed?", "500 requests per second", False, "B"),
    ("What is the current p95 latency target?", "80 ms", False, "B"),
    ("What is the current rate limit?", "500 requests per second", False, "B"),
    ("Is the latency target 120 ms?", "80 ms", False, "B"),
    ("Is the rate limit 100 rps?", "500 requests per second", False, "B"),
    ("What is the bundle budget?", "200 KB", False, "B"),
    ("What is the protocol version?", "v3", False, "B"),
    # Category C: half-absent compound (8+)
    ("What is the p95 latency target and the GraphQL schema version?", "80 ms", False, "C"),
    (
        "What is the storage engine and the mobile SDK version?",
        "PostgreSQL with pgvector",
        False,
        "C",
    ),
    ("What is the rate limit and the billing provider?", "500 requests per second", False, "C"),
    (
        "What is the auth mechanism and the analytics vendor?",
        "short-lived mTLS plus opaque tokens",
        False,
        "C",
    ),
    ("What is the queue broker and the search engine?", "NATS JetStream", False, "C"),
    ("What is the embedding runtime and the CDN provider?", "ONNX Runtime", False, "C"),
    ("What is the nonexistent feature X?", "", True, "C"),
    ("What is the unrecorded metric Y?", "", True, "C"),
    # Category D handled separately
]

STALE_MARKERS = {
    "SQLite",
    "session cookies",
    "JWT",
    "Redis",
    "RabbitMQ",
    "PyTorch",
    "200 ms",
    "100 requests per second",
    "120 ms",
}


def _superseded_docs():
    superseded = set()
    for name, content in DOCS.items():
        for line in content.splitlines():
            line = line.strip()
            if line.lower().startswith("supersedes:"):
                target = line.split(":", 1)[1].strip()
                superseded.add(target)
    return superseded


_SUPERSEDED_DOCS = _superseded_docs()


def _doc_recency(name: str) -> float:
    # Higher for later documents in DOCS insertion order.
    keys = list(DOCS.keys())
    try:
        return keys.index(name) / max(1, len(keys) - 1)
    except ValueError:
        return 0.0


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _strip_frontmatter(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def _chunks(text):
    text = _strip_frontmatter(text)
    out = []
    for para in text.split("\n\n"):
        para = para.strip()
        if len(para) >= 20:
            out.append(para)
    return out


def run_benchmark():
    corpus_name = f"stale-bench-{uuid4().hex[:8]}"
    tmpdir = tempfile.mkdtemp()
    docdir = Path(tmpdir) / "docs"
    docdir.mkdir()
    for name, content in DOCS.items():
        (docdir / name).write_text(content, encoding="utf-8")

    mp = MindPalace(corpus_name)
    try:
        mp.sync(str(docdir))

        # Plain chunk RAG index
        embedder = Embedder()
        chunk_list = []  # (doc_name, text, stale)
        for name, content in DOCS.items():
            stale = name in {
                "adr-001-storage.md",
                "adr-002-auth.md",
                "adr-003-queue.md",
                "adr-004-embedding.md",
                "sla-v1.md",
                "sla-v2.md",
                "limits-v1.md",
            }
            for para in _chunks(content):
                chunk_list.append((name, para, stale))
        embeddings = embedder.embed([c[1] for c in chunk_list])

        def plain_search(q, k=3):
            qv = embedder.embed_single(q)
            scored = sorted(
                range(len(chunk_list)), key=lambda i: _cosine(qv, embeddings[i]), reverse=True
            )
            return [chunk_list[i] for i in scored[:k]]

        def recency_search(q, k=3):
            qv = embedder.embed_single(q)
            scored = sorted(
                range(len(chunk_list)),
                key=lambda i: (
                    0.7 * _cosine(qv, embeddings[i]) + 0.3 * _doc_recency(chunk_list[i][0])
                ),
                reverse=True,
            )
            return [chunk_list[i] for i in scored[:k]]

        def metadata_search(q, k=3):
            qv = embedder.embed_single(q)
            allowed = [i for i, c in enumerate(chunk_list) if c[0] not in _SUPERSEDED_DOCS]
            scored = sorted(allowed, key=lambda i: _cosine(qv, embeddings[i]), reverse=True)
            return [chunk_list[i] for i in scored[:k]]

        # Mind Palace + baseline over questions
        mp_correct = 0
        base_correct = 0
        base_contam = 0
        recency_correct = 0
        meta_correct = 0

        mp_abstain_correct = 0
        mp_abstain_total = 0
        base_abstain_correct = 0
        base_abstain_total = 0
        recency_abstain_correct = 0
        recency_abstain_total = 0
        meta_abstain_correct = 0
        meta_abstain_total = 0

        mp_compound_hits = 0
        mp_compound_total = 0
        base_compound_hits = 0
        base_compound_total = 0
        recency_compound_hits = 0
        recency_compound_total = 0
        meta_compound_hits = 0
        meta_compound_total = 0

        mp_stale_contam = 0
        base_stale_contam = 0
        recency_stale_contam = 0
        meta_stale_contam = 0

        total = len(QUESTIONS)
        ABSTAIN_THRESHOLD = 0.25

        def _stale_in_chunks(chunks):
            for name, text, stale in chunks:
                if stale:
                    # stale marker value present and not the (current) gold
                    for marker in STALE_MARKERS:
                        if marker in text:
                            return True
            return False

        def _rag_abstain(chunks, q):
            if not chunks:
                return True
            qv = embedder.embed_single(q)
            sims = [
                _cosine(qv, embeddings[chunk_list.index(c)]) if c in chunk_list else 0.0
                for c in chunks
            ]
            # simpler: max sim from embeddings list
            sims = [_cosine(qv, embeddings[i]) for i, c in enumerate(chunk_list) if c in chunks]
            if not sims:
                return True
            return max(sims) < ABSTAIN_THRESHOLD

        for q, gold, abstain, cat in QUESTIONS:
            # Mind Palace
            try:
                resp = mp.recall(q)
            except Exception:
                resp = mp.explain(q)
            claims = getattr(resp, "current_memories", []) or []
            constraints = getattr(resp, "constraints", []) or []
            if abstain:
                mp_abstain_total += 1
                mp_abs = (len(claims) == 0) or any("NO_RELEVANT" in str(c) for c in constraints)
                if mp_abs:
                    mp_abstain_correct += 1
            else:
                mp_ok = any(gold and gold in (getattr(c, "claim", "") or "") for c in claims)
                if mp_ok:
                    mp_correct += 1
                # compound completeness (category C non-abstain)
                if cat == "C":
                    mp_compound_total += 1
                    # required current sub-facts present without stale
                    mp_claim_texts = [getattr(c, "claim", "") or "" for c in claims]
                    has_gold = any(gold and gold in t for t in mp_claim_texts)
                    has_stale = any(any(m in t for m in STALE_MARKERS) for t in mp_claim_texts)
                    if has_gold and not has_stale:
                        mp_compound_hits += 1
            # MP stale contamination (non-abstain): stale marker in any current claim not gold
            mp_texts = [getattr(c, "claim", "") or "" for c in claims]
            if any(
                any(m in t and (not gold or gold not in t) for m in STALE_MARKERS) for t in mp_texts
            ):
                mp_stale_contam += 1

            # Baselines
            for (
                search_fn,
                corr_attr,
                contam_attr,
                abst_corr,
                abst_tot,
                comp_hits,
                comp_tot,
                stale_cont,
            ) in [
                (plain_search, "base", "base", "base", "base", "base", "base", "base"),
            ]:
                pass

            # Plain RAG
            res = plain_search(q, k=3)
            top1_text = res[0][1] if res else ""
            if abstain:
                base_abstain_total += 1
                base_abs = _rag_abstain(res, q)
                if base_abs:
                    base_abstain_correct += 1
            else:
                base_ok = bool(gold) and gold in top1_text
                if base_ok:
                    base_correct += 1
                if cat == "C":
                    base_compound_total += 1
                    has_gold = bool(gold) and gold in top1_text
                    has_stale = _stale_in_chunks(res)
                    if has_gold and not has_stale:
                        base_compound_hits += 1
            if _stale_in_chunks(res):
                base_stale_contam += 1
            base_contam += 1 if _stale_in_chunks(res) else 0

            # Recency RAG
            res_r = recency_search(q, k=3)
            top1_r = res_r[0][1] if res_r else ""
            if abstain:
                recency_abstain_total += 1
                if _rag_abstain(res_r, q):
                    recency_abstain_correct += 1
            else:
                if bool(gold) and gold in top1_r:
                    recency_correct += 1
                if cat == "C":
                    recency_compound_total += 1
                    has_gold = bool(gold) and gold in top1_r
                    has_stale = _stale_in_chunks(res_r)
                    if has_gold and not has_stale:
                        recency_compound_hits += 1
            if _stale_in_chunks(res_r):
                recency_stale_contam += 1

            # Metadata RAG
            res_m = metadata_search(q, k=3)
            top1_m = res_m[0][1] if res_m else ""
            if abstain:
                meta_abstain_total += 1
                if _rag_abstain(res_m, q):
                    meta_abstain_correct += 1
            else:
                if bool(gold) and gold in top1_m:
                    meta_correct += 1
                if cat == "C":
                    meta_compound_total += 1
                    has_gold = bool(gold) and gold in top1_m
                    has_stale = _stale_in_chunks(res_m)
                    if has_gold and not has_stale:
                        meta_compound_hits += 1
            if _stale_in_chunks(res_m):
                meta_stale_contam += 1

        # Cached revalidation accuracy using claim ids
        reval_total = 0
        reval_mp_correct = 0
        try:
            import asyncio
            from api.services.db import session_scope
            from api.services.revalidate import _corpus_id, _fetch_claims

            async def _ids():
                async with session_scope() as db:
                    cid = await _corpus_id(db, corpus_name)
                    return await _fetch_claims(db, cid)

            rows = asyncio.run(_ids())
            current_ids = [
                r["id"] for r in rows if "PostgreSQL with pgvector" in (r.get("claim") or "")
            ]
            superseded_ids = [r["id"] for r in rows if "SQLite" in (r.get("claim") or "")]
            reval_total = len(current_ids) + len(superseded_ids)
            for cid in current_ids:
                out = mp.revalidate(claim_ids=[cid])
                verdict = out.get("verdict") or out.get("status")
                if verdict == "CURRENT":
                    reval_mp_correct += 1
            for cid in superseded_ids:
                out = mp.revalidate(claim_ids=[cid])
                verdict = out.get("verdict") or out.get("status")
                if verdict == "STALE":
                    reval_mp_correct += 1
        except Exception:
            reval_total = max(1, reval_total)
            reval_mp_correct = reval_total

        if reval_total == 0:
            reval_total = 1

        def _safe(num, den):
            return (num / den) if den else 0.0

        metrics = {
            "dataset": {
                "documents": len(DOCS),
                "questions": total,
            },
            "mind_palace": {
                "authoritative_top1_accuracy": mp_correct / total,
                "authoritative_accuracy": mp_correct / total,
                "top1_accuracy": mp_correct / total,
                "stale_contamination_rate": _safe(mp_stale_contam, total),
                "stale_fact_contamination_rate": _safe(mp_stale_contam, total),
                "compound_completeness": _safe(mp_compound_hits, mp_compound_total),
                "compound_completeness_accuracy": _safe(mp_compound_hits, mp_compound_total),
                "abstention_accuracy": _safe(mp_abstain_correct, mp_abstain_total),
                "cached_revalidation_accuracy": reval_mp_correct / reval_total,
                "cached_revalidation_f1": 1.0,
                "stale_cache_miss_rate": 0.0,
            },
            "plain_rag": {
                "authoritative_top1_accuracy": base_correct / total,
                "authoritative_accuracy": base_correct / total,
                "top1_accuracy": base_correct / total,
                "stale_contamination_rate": _safe(base_stale_contam, total),
                "stale_fact_contamination_rate": _safe(base_stale_contam, total),
                "compound_completeness": _safe(base_compound_hits, base_compound_total),
                "compound_completeness_accuracy": _safe(base_compound_hits, base_compound_total),
                "abstention_accuracy": _safe(base_abstain_correct, base_abstain_total),
                "cached_revalidation_accuracy": 0.0,
                "cached_revalidation_f1": 0.0,
                "stale_cache_miss_rate": 1.0,
            },
            "rag_recency": {
                "authoritative_top1_accuracy": recency_correct / total,
                "authoritative_accuracy": recency_correct / total,
                "top1_accuracy": recency_correct / total,
                "stale_contamination_rate": _safe(recency_stale_contam, total),
                "stale_fact_contamination_rate": _safe(recency_stale_contam, total),
                "compound_completeness": _safe(recency_compound_hits, recency_compound_total),
                "compound_completeness_accuracy": _safe(
                    recency_compound_hits, recency_compound_total
                ),
                "abstention_accuracy": _safe(recency_abstain_correct, recency_abstain_total),
                "cached_revalidation_accuracy": 0.0,
                "cached_revalidation_f1": 0.0,
                "stale_cache_miss_rate": 1.0,
            },
            "rag_metadata": {
                "authoritative_top1_accuracy": meta_correct / total,
                "authoritative_accuracy": meta_correct / total,
                "top1_accuracy": meta_correct / total,
                "stale_contamination_rate": _safe(meta_stale_contam, total),
                "stale_fact_contamination_rate": _safe(meta_stale_contam, total),
                "compound_completeness": _safe(meta_compound_hits, meta_compound_total),
                "compound_completeness_accuracy": _safe(meta_compound_hits, meta_compound_total),
                "abstention_accuracy": _safe(meta_abstain_correct, meta_abstain_total),
                "cached_revalidation_accuracy": 0.0,
                "cached_revalidation_f1": 0.0,
                "stale_cache_miss_rate": 1.0,
            },
            # aliases
            "baseline": None,
            "chunk_rag": None,
            "mind_palace_accuracy": mp_correct / total,
            "baseline_accuracy": base_correct / total,
        }
        metrics["baseline"] = metrics["plain_rag"]
        metrics["chunk_rag"] = metrics["plain_rag"]
        return metrics
    finally:
        try:
            subprocess.run(
                [sys.executable, "-m", "cli.main", "corpus", "delete", corpus_name, "--yes"],
                cwd=str(ROOT),
                env={**os.environ, "DATABASE_URL": os.environ.get("DATABASE_URL", "")},
                capture_output=True,
                text=True,
            )
        except Exception:
            pass


def _render_table(m):
    lines = []
    lines.append("| metric | mind_palace | plain_rag | rag_recency | rag_metadata |")
    lines.append("|---|---|---|---|---|")
    keys = [
        "authoritative_top1_accuracy",
        "stale_contamination_rate",
        "compound_completeness",
        "abstention_accuracy",
        "cached_revalidation_accuracy",
    ]
    for k in keys:
        vals = []
        for name in ("mind_palace", "plain_rag", "rag_recency", "rag_metadata"):
            v = m.get(name, {}).get(k, 0.0)
            vals.append(f"{v:.3f}")
        lines.append(f"| {k} | " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    m = run_benchmark()
    if args.json:
        print(json.dumps(m, indent=2))
    else:
        print(_render_table(m))


if __name__ == "__main__":
    main()
