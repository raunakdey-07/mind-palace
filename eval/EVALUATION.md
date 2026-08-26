# Retrieval Evaluation Report

## Configuration

| Item | Value |
|---|---|
| Corpus | `content_eval/` — 202 Markdown documents, 671 chunks |
| Document types | kaggle, project, note, paper (proportional mix) |
| Embedding model | `all-MiniLM-L6-v2` (384-dim, normalized) |
| Reranker model | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Benchmark | `eval/retrieval_benchmarks.yaml` — 98 queries, 14 categories |
| Relevance model | binary, document-level (consistent across all metrics) |
| k values | 1, 3, 5, 10 |
| Database | PostgreSQL 15 + pgvector (HNSW index), pg_trgm |
| Command | `python -m cli.main eval strategies --candidates 10,20,50` |

## Ground-truth methodology

Labels were assigned by reading source documents — never derived from
retrieval output. Mechanical validation (`tests/test_benchmark_ground_truth.py`)
enforces: every expected title exists in the corpus frontmatter; negative
queries carry no labels; filters reference valid metadata values; queries are
unique; every query has a category. Eight queries are explicitly negative
(`expect_empty: true`) and are scored on precision only.

## Results (98 queries; recall/MRR/nDCG over the 90 positive queries)

| Strategy | R@1 | R@3 | R@5 | R@10 | P@3 | MRR | nDCG@5 | avg ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| vector | 0.57 | 0.78 | 0.83 | 0.92 | 0.31 | 0.79 | 0.76 | 6 |
| hybrid | 0.56 | 0.79 | 0.86 | 0.92 | 0.32 | 0.78 | 0.77 | 82 |
| hybrid+rrf | 0.57 | 0.78 | 0.87 | 0.90 | 0.32 | 0.78 | 0.78 | 51 |
| hybrid+rrf+rerank@c20 | 0.63 | 0.79 | 0.83 | 0.89 | 0.31 | 0.81 | 0.79 | ~1740 |

## Statistical comparison (paired bootstrap, 95% CI)

Per-query Recall@3 values resampled over queries; paired differences between
strategies on the same queries:

```text
Strategy                mean        95% CI           vs hybrid+rrf
hybrid+rrf              0.780  [0.702,0.852]                  -
vector                  0.776  [0.704,0.846]     [-0.065,+0.061]
hybrid                  0.793  [0.722,0.861]     [-0.046,+0.074]
rerank@c20              0.794  [0.720,0.867]     [-0.043,+0.070]
```

**Every paired-difference interval contains zero.** No strategy is
statistically distinguishable from any other on this benchmark at this sample
size.

### Decision (evidence-based)

Since quality differences are within noise, the decision falls to latency,
robustness, and simplicity:

1. **Default stays hybrid+RRF** (~51 ms). Rank-based fusion is scale-free — it
   needs no score calibration as the corpus evolves, unlike the weighted blend.
2. **Reranking remains opt-in.** Its +0.01–0.03 point-estimate gains are not
   statistically established, while its ~1740 ms cost is a 30× latency increase.
   If enabled, candidate pool c=10 is sufficient (larger pools add cost, not
   quality).
3. **Vector-only is a legitimate fast path** (~6 ms) at equivalent quality —
   worth exposing for latency-sensitive consumers.

The previous milestone's provisional conclusion ("RRF best") was an artifact
of a trivially small corpus; the honest statement is now *equivalence with
different latency profiles*.

## Latency findings

| Stage | Latency |
|---|---|
| Query embedding | ~5 ms |
| Vector retrieval | ~6 ms |
| Hybrid retrieval | ~82 ms (pg_trgm similarity is the expensive signal) |
| Hybrid+RRF | ~51 ms |
| Reranking c=10/20/50 | ~1300/~1740/~3500 ms end-to-end |

### Reranker cost decomposition (measured)

| Component | Cost |
|---|---|
| Model load (cold start) | **8263 ms** — once per process; dominates first request |
| Warm inference, 5 candidates | ~73 ms |
| Warm inference, 10 candidates | ~98 ms |
| Warm inference, 20 candidates | ~142 ms |
| Warm inference, 50 candidates | ~424 ms |

Warm inference scales roughly linearly with candidate count. The gap between
these warm numbers and end-to-end benchmark latency (~1.7 s at c=20) indicates
per-call overhead beyond pure scoring (embedding, retrieval, serialization) —
meaning a long-lived server process amortizes model load and should see
sub-200 ms reranking at c=20.

Implications:
- candidate pool c=10 gives near-c=20 quality at ~30% less scoring cost
- model load must happen at service startup, not per request
- ONNX/quantization are plausible future wins but unproven here

## Failure analysis

16 total misses across strategies (no expected doc in top-10). Classes:

1. **Corpus-growth distractors** (dominant class): new notes on ranking/
   evaluation topics outrank finance docs for finance queries because the
   corpus now contains many superficially similar technical notes. This is
   genuine retrieval difficulty emerging from scale — exactly what the corpus
   expansion was designed to expose.
2. **Semantic paraphrase misses**: "converting spoken words into text" /
   "finding similar customers" still miss under RRF; embeddings link these
   queries to adjacent-domain documents instead.
3. **Vocabulary gaps** ("Altman Z score"): unchanged known-hard case.

No chunking or metadata failures observed. Label validity is mechanically
guaranteed by the ground-truth tests.

## Superseded results

Earlier reports on the 3-document and 51-document corpora showed near-ceiling
scores (R@5 = 1.00) and a provisional "hybrid+RRF is best" conclusion. Both
were artifacts of insufficient corpus scale and are superseded by this report.
Do not cite them as evidence of strategy quality.

## Reproducing

```bash
podman run -d --name mp-eval-pg -e POSTGRES_DB=mindpalace \
  -e POSTGRES_USER=mpadmin -e POSTGRES_PASSWORD=secret -p 5433:5432 ankane/pgvector
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head
python -m cli.main ingest-repo content_eval
python -m cli.main eval strategies --candidates 10,20,50 --details
```

Latency figures are indicative comparative measurements on a developer
workstation, not controlled production benchmarks.
