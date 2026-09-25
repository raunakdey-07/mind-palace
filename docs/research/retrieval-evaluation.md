# Retrieval evaluation

Three separate evaluations exist in this repository. They measure different
things, use different corpora, and are not interchangeable. This document keeps
them apart and records what each one does not establish. Nothing here was
re-run for this document; every number cites an existing artifact.

## The three evaluations

| Evaluation | Corpus | Question set | Metric | Artifact |
|---|---|---|---|---|
| Live retrieval strategies | `content_eval/`, 202 documents, 671 chunks | 98 labelled queries, 14 categories | Recall, Precision, MRR, nDCG over distinct documents | `eval/EVALUATION.md`, `eval/baseline.json` |
| Memory semantics, M006 | 11 documents evolving through 7 stages | 39 tailored lexical cases | exact sets, presence checks, safety invariants | `docs/evaluation/m006.md` |
| Memory question retrieval | same evolving corpus | 64 natural-language questions | exact current sets and safety | `docs/evaluation/m0065.md`, `docs/evaluation/m00675-reproducibility.md` |

The live index has no claim, status, or supersession model, so its
semantic-state metrics are not applicable rather than scored zero. That is why
a live-retrieval number and a memory number never appear in the same column.

## Live retrieval strategies

Ground truth was labelled by reading source documents, and
`tests/test_benchmark_ground_truth.py` mechanically enforces that every expected
title exists in the corpus, that negative queries carry no labels, that filters
reference valid metadata, that queries are unique, and that every query has a
category. Eight queries are explicitly negative and are scored on precision only.

| Strategy | R@1 | R@3 | R@5 | R@10 | P@3 | MRR | nDCG@5 | avg ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| vector | 0.57 | 0.78 | 0.83 | 0.92 | 0.31 | 0.79 | 0.76 | 6 |
| hybrid | 0.56 | 0.79 | 0.86 | 0.92 | 0.32 | 0.78 | 0.77 | 82 |
| hybrid+rrf | 0.57 | 0.78 | 0.87 | 0.90 | 0.32 | 0.78 | 0.78 | 51 |
| hybrid+rrf+rerank@c20 | 0.63 | 0.79 | 0.83 | 0.89 | 0.31 | 0.81 | 0.79 | about 1740 |

Paired bootstrap over per-query Recall@3 at 95% confidence:

```text
hybrid+rrf              0.780  [0.702,0.852]                  -
vector                  0.776  [0.704,0.846]     [-0.065,+0.061]
hybrid                  0.793  [0.722,0.861]     [-0.046,+0.074]
rerank@c20              0.794  [0.720,0.867]     [-0.043,+0.070]
```

Every interval contains zero, so at this sample size no strategy is
distinguishable from another on quality. The default is hybrid+RRF because rank
fusion needs no score calibration as the corpus grows; vector-only at about 6 ms
is an equivalent-quality fast path; reranking stays opt-in because its point
gains are not statistically established against roughly 30 times the latency.

Reranker cost decomposes as an 8,263 ms cold model load once per process, then
warm inference of about 73, 98, 142, and 424 ms at 5, 10, 20, and 50 candidates.
The gap between the warm numbers and the end-to-end benchmark latency indicates
per-call overhead beyond scoring, so a long-lived server should amortize the load
and see sub-200 ms reranking at c=20. That is an inference from the gap, not a
measured server result.

The 16 total misses are classified in `eval/EVALUATION.md`: corpus-growth
distractors dominate, semantic paraphrase misses are second, and vocabulary gaps
such as "Altman Z score" are a known-hard case. No chunking or metadata failure
was observed. Earlier reports on 3-document and 51-document corpora showed
near-ceiling R@5 and a provisional "hybrid+RRF is best" conclusion; both were
artifacts of corpus scale and are superseded.

## M006 memory semantics

39 tailored lexical cases pass, including 33/33 current exact sets (nine of them
expected-empty), 100% historical recall over eight nonempty cases, 7/7 temporal
expectations, 3/3 supersession, 3/3 conflict preservation, 4/4 deletion and
restoration, 51/51 returned-claim provenance appearances, and 7/7 snapshot
replay reproducibility. Live text recall on the same corpus is 79.17% for
current, 12.50% for historical, and 71.43% for evidence quotes, and
natural-language current exact sets score 0/24.

The 0/24 is the finding that motivated M006.5. It is a lexical-lookup
weakness, not a state-resolution weakness: the same corpus resolves current,
historical, supersession, and conflict state correctly when given lookup terms.

Pack budgets were measured at 1,000, 2,000, 4,000, 8,000, and 16,000 Unicode
characters over 195 pack/state/budget combinations, with all safety checks
passing at every budget and mean claim retention of 0.00%, 49.36%, 91.24%,
99.36%, and 100.00%. A 1,000-character pack is safe and useless on those
requests, which is a developer-ergonomics finding rather than a defect.

Generation was not run. No Ollama service was reachable and no external provider
was contacted, so generated-answer quality, unsupported generated assertions,
citation correctness, and conflict omission in generated prose are unmeasured.

## M006.5 and M006.75 question retrieval

The unchanged original natural-language diagnostic moved from 0/24 to 17/24 exact
current sets. On 40 added questions, 30/40 match all labelled sets exactly:
current 13/19, historical 4/4, temporal 3/6, change 3/4, conflict 4/4,
provenance 3/3. Seventeen exact-set failures remain across the 64 questions and
no labels were changed to improve them.

Safety was measured separately: 0 failures over 264 output cases, 64 full-budget
plus 200 budget-sweep outputs, and no execution failures. Budget correctness and
retrieval completeness are distinct. Exact sets at 1,000, 2,000, 4,000, 8,000,
and 16,000 characters were 3/40, 3/40, 26/40, 30/40, and 30/40, which is the
clearest evidence that a bounded, correctly attributed pack can still omit
relevant claims.

M006.75 is the frozen held-out run of the same service on
`eval/memory_questions_heldout.yaml` with `eval/memory_query_policy.json`:

| Category | Exact / full |
|---|---:|
| Overall | 47/60 |
| Current | 6/10 |
| Historical | 5/10 |
| Temporal | 10/10 |
| Multi-topic | 9/10 |
| Conflict / provenance | 7/10 |
| Abstention | 10/10 |

Safety on the frozen run: provenance, budget, conflict closure, current
authority, and temporal scope are 60/60 each; false-current promotions and
execution failures are 0.

Reproducibility has one recorded correction worth reading.
`docs/evaluation/m00675-reproducibility.md` explains that an earlier 30-versus-47
discrepancy was an evaluator and policy-suite selection error: the 30 number
reads diagnostic policy `A` rows, the 47 reads frozen-policy rows. The frozen
policy and held-out dataset hashes are checked by the runner, and the canonical
result excludes timing fields from its equality hash. A second narrower
`SOURCE_REVISION` difference means the stored raw artifact is historical
evidence rather than the current canonical artifact.

Timing from `docs/evaluation/m0065.md`: one warmed fixed query over 20
repetitions measured 124.2/131.3 ms p50/p95, against 4.0/5.1 ms for preembedded
live search. The comparison is not fair end to end, because the query path
includes per-call embedding and archive projection while live search excludes
query embedding.

## Relevance is a heuristic, not authority

`api/services/memory_relevance.py` selects authored keys using the configured
sentence-transformer, with a 0.30 cosine floor, a 0.90 top-score band, a 0.65
strong threshold, and at most 4 topics. Scores propagate only within the same
authored key. The module docstring says the scores never establish authority, and
`api/services/memory_query.py` projects the complete time-scoped archive before
ranking, so relevance can include or exclude a key but never change a claim's
status.

The vectors are transient, recomputed per query, with no persistent cache or
index, and inference runs off the event loop via `asyncio.to_thread`.

## Quality gate

`python -m cli.main eval strategies --candidates 10,20,50` produces
machine-readable per-strategy metrics, and `python -m cli.main eval gate
--baseline baseline.json` computes per-query paired differences against a
committed baseline. The gate exits non-zero only when a paired-difference
confidence interval excludes zero on the negative side, so noise never fails the
build. `eval/baseline.json` is the committed baseline and carries per-query
samples, not only aggregates.

## Blocked research

M007.1 has a 118-item blind review package and zero adjudicated decisions.
`eval/m007/research-manifest.json` reports `DATA_BLOCKED` with
`independently_adjudicated_rows: 0` and a null dataset hash, because no
adjudication records exist. `eval/m008/research-manifest.json` reports
`BLOCKED_BY_M007_DATA_GATE` and states that no M008 temporal benchmark has been
created and no causal or longitudinal claim is made. The M006.75 artifacts are
frozen and were not modified by either program.

## What none of this establishes

- No result here compares Mind Palace with another retrieval or memory system.
  The comparisons in `eval/EVALUATION.md` are between Mind Palace's own
  strategies.
- 47/60 is a project-specific authored evaluation on one frozen corpus. It is
  not a claim of universal memory-system superiority or production readiness.
- A passing safety or execution gate does not certify relevance.
  `truncated=false` does not prove no relevant claim was missed.
- The live-baseline text-coverage figures measure literal recoverability of
  expected strings, not semantic accuracy or temporal framing.
- The synthetic oracle suites in `api/services/memory_evaluation_lab.py` derive
  expectations from explicit semantics. They are synthetic semantic validation,
  never human truth.

## Reproducing

```bash
# Live retrieval strategies and gate
python -m cli.main eval strategies --candidates 10,20,50 --details
python -m cli.main eval gate --baseline baseline.json

# Memory semantics with real cached embeddings
HF_HUB_OFFLINE=1 python -m cli.main eval memory --embeddings cached \
  --repetitions 20 --sizes 100,500,1000 \
  --save eval/results/memory-cached-manual.json

# Frozen held-out benchmark, dedicated database
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/benchmark \
  ./scripts/benchmark/m00675.sh
```

The memory benchmark uses an isolated random schema inside one rolled-back
transaction and requires a dedicated `DATABASE_URL` so it cannot silently fall
back to a developer database. The 5,000-document size in the CLI is a safety cap,
not a measured result. Latency figures in `eval/EVALUATION.md` are indicative
comparative measurements on a developer workstation, not controlled production
benchmarks.
