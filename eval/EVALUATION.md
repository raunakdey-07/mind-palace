# Retrieval Evaluation Report

## Configuration

| Item | Value |
|---|---|
| Corpus | `content_eval/` — 51 Markdown documents, 171 chunks |
| Document types | 12 kaggle, 18 project, 13 note, 8 paper |
| Embedding model | `all-MiniLM-L6-v2` (384-dim, normalized) |
| Reranker model | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Benchmark | `eval/retrieval_benchmarks.yaml` — 54 queries, 14 categories |
| Relevance model | binary, document-level (consistent across all metrics) |
| k values | 1, 3, 5, 10 |
| Database | PostgreSQL 15 + pgvector (HNSW index), pg_trgm |
| Command | `python -m cli.main eval strategies --candidates 10,20,50` |

## Ground-truth methodology

Labels were assigned by **reading each source document and marking which
document(s) contain the answer**, independent of any retrieval output. A
verification script cross-checks every `expected` title against the corpus
frontmatter — a label referencing a non-existent title is treated as a bug in
the benchmark, not a retrieval failure. Four queries are explicitly negative
(`expect_empty: true`): no relevant document exists anywhere in the corpus,
and they are scored on precision only.

## Results (54 queries; recall/MRR/nDCG over the 50 positive queries)

| Strategy | R@1 | R@3 | R@5 | R@10 | P@3 | MRR | nDCG@5 | avg ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| vector | 0.67 | 0.87 | 0.91 | 0.95 | 0.36 | 0.87 | 0.86 | 6 |
| hybrid | 0.69 | 0.87 | 0.91 | 0.96 | 0.36 | 0.88 | 0.87 | 22 |
| hybrid+rrf | 0.66 | 0.85 | 0.89 | 0.92 | 0.35 | 0.84 | 0.85 | 15 |
| hybrid+rrf+rerank@c20 | 0.70 | 0.86 | 0.91 | 0.95 | 0.37 | 0.88 | 0.88 | ~1750 |

P@3 ≈ 0.36 for all strategies reflects the metric's definition, not weakness:
most positive queries have exactly one relevant document out of 51, so P@3 is
capped near 1/3 by construction.

## Reranker candidate-size comparison

| candidate_k | R@1 | R@3 | R@10 | MRR | nDCG@5 | avg ms |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0.70 | 0.86 | 0.97 | 0.88 | 0.88 | ~1300 |
| 20 | 0.70 | 0.86 | 0.95 | 0.88 | 0.88 | ~1750 |
| 50 | 0.70 | 0.86 | 0.95 | 0.88 | 0.87 | ~3500 |

## Findings

1. **The expanded corpus changes the picture materially.** On the previous
   3-document corpus every strategy scored R@5 = 1.00 and differences were
   noise. Here real separation exists — and **no strategy dominates**.
2. **Reranking now shows a genuine but small quality gain**: best or tied-best
   on R@1 (0.70), P@3 (0.37), MRR (0.88), nDCG@5 (0.88). The gain over plain
   hybrid is +0.01–0.02 across metrics — within plausible sampling noise at
   N=50 queries (see statistics note below).
3. **Reranking costs ~1750 ms vs 15–23 ms** — roughly two orders of magnitude.
   It also *hurts* R@3 slightly (0.86 vs 0.87) by re-ordering relevant docs out
   of the top-3 on some queries.
4. **hybrid (weighted blend) is now marginally better than hybrid+rrf**
   (MRR 0.88 vs 0.84, R@10 0.96 vs 0.92). The earlier small-corpus result that
   favored RRF did not fully survive corpus expansion. The gap is small enough
   that both remain defensible defaults; hybrid+RRF stays the API default
   pending confirmation on an even larger corpus, since its score-free fusion
   is more robust to score-scale drift as the corpus evolves.
5. **Candidate pool size**: c=10 matches c=20/c=50 quality at lower latency.
   If reranking is enabled, c=10 is the sensible setting.
6. **Negative-query handling**: all strategies correctly returned few/no
   results for the four negative queries; precision penalties are included in
   the P@3 column.

### Statistics caveat

With 50 positive queries, one query flipping changes Recall@k by ±0.02. The
differences between hybrid, hybrid+RRF, and reranked retrieval are of exactly
this magnitude. The honest conclusion is that **all three are statistically
comparable on this benchmark**; only their latency differs decisively.

## Failure analysis (representative)

| Query (category) | Failure mode | Diagnosis |
|---|---|---|
| "Sharpe ratio max drawdown" (lexical, vector/hybrid miss) | Distractor confusion: Portfolio Optimizer outranks Finalysis | Retrieval works; both docs legitimately discuss Sharpe ratio. Label treats Finalysis as sole-relevant — arguably too strict. |
| "converting spoken words into text" (semantic, RRF miss at rank 15) | Semantic mismatch: speech-commands doc wins | The paraphrase "spoken words → text" embeds closer to keyword-spotting than to transcription prose. Reranker recovers it to rank 6 — its clearest win. |
| "finding similar customers..." (semantic, RRF miss) | Distractor confusion: Santander doc wins | Both docs are about customer similarity; label ambiguity again. |
| "Altman Z score bankruptcy risk" (terminology-mismatch, RRF/rerank miss) | Vocabulary gap: term absent from corpus | Known-hard case retained deliberately; measures generalization, not lookup. |

Failure classes observed: distractor confusion (labels arguably too strict),
vocabulary gaps (expected), semantic embedding limits (reranker helps).
No chunking or metadata failures were observed.

## Earlier small-corpus results (superseded)

On the original 3-document/13-chunk corpus all strategies scored R@5 = 1.00;
hybrid+RRF reached R@3 = 1.00 at ~2 ms while reranking cost ~200 ms+. Those
numbers reflected a trivially easy retrieval task and should not be cited as
evidence of strategy quality.

## Reproducing

```bash
# start pgvector and load schema + corpus
podman run -d --name mp-eval-pg -e POSTGRES_DB=mindpalace \
  -e POSTGRES_USER=mpadmin -e POSTGRES_PASSWORD=secret -p 5433:5432 ankane/pgvector
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head
python -m cli.main ingest-repo content_eval
python -m cli.main eval strategies --candidates 10,20,50 --details
```

Latency figures are indicative comparative measurements on a developer
workstation, not controlled production benchmarks.
