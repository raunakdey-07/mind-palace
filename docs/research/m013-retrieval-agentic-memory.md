# M013 — Measured Retrieval & Agentic Memory Engineering

**Truth Ledger.** Written before any feature work, per the milestone rule that no
change is justified by intuition. Every claim below is either backed by a tracked
artifact or explicitly labelled unverified.

Branch `milestone/v0.8.0-dev`. Baseline commit `d825f06`.

---

## Verified facts

Backed by tracked evidence. Each names its artifact.

| Fact | Evidence |
|---|---|
| Authoritative output is byte-identical across the ANALYZE fix: 202/202 packs, set digest `5a3b8049…` | `evidence/195-verify-packs-after-commit.txt` |
| The benchmark harness is deterministic: two captures at `dbdc35f` reproduce `5a3b8049…` exactly | `evidence/200-harness-determinism.txt` |
| The pack comparator is not vacuous: mutated claim text, validity, conflict record and ordering each produce a non-zero exit naming the question | `evidence/04-mutation-M1..M4.txt` |
| Full suite passes: **1206 tests** | `evidence/` + suite runs |
| Dev benchmark: semantic 178/202, lexical 164/202, lexical→semantic **194/202** | `m0115-experiments.json` |
| Held-out v1: **131/157 (83.4%)**, failures `{F2:5, F7:19, F9:2}` | `m0130-heldout.json` |
| Held-out v1 is scored once and then analysed; its failures have shaped diagnosis | `docs/research/m009-retrieval-protocol.md` |
| The L2 rebuild **invariant** holds (12/12 at all three phases) but the **absolute digest is not run-stable** | `evidence/197-l2-digest-instability.txt` |
| The archive-load plan cliff is caused by *absent* planner statistics, not stale ones; ANALYZE gives 58.1x at 500 versions, 104.6x at 1000, ~1.3x at 1500/2000 | `evidence/183-cliff-reverified-summary.txt`, `196-scale-check.txt` |
| The regression test fails for the intended reason when the hook is stubbed out | `evidence/194-mutation-proof.txt` |
| Every artifact cited by STATUS.md is tracked; the checker is non-vacuous and passes | `scripts/check_evidence.py`, `evidence/171-…` |

### Two structural findings that shape this milestone

Read from source, not assumed:

1. **`rank_claims` in `api/services/memory_query.py` is dead code.** It implements
   `lexical`, `embedding` and `hybrid` (RRF, k=60), but its only callers are
   `tests/test_memory_query.py` and `memory_query_benchmark.py`. Production
   `select()` calls `relevance()` directly, which has exactly two modes —
   `lexical=True` (token overlap) or embedding. **Fused hybrid retrieval does not
   exist in the production path today.** Anything called "hybrid" in prior
   experiments was the unused helper.

2. **There is no PostgreSQL full-text search.** `grep -ri "tsvector\|tsquery"
   migrations/` returns zero hits. Keyword matching is pure-Python set intersection
   in `memory_relevance.relevance()`, scored per candidate in Python, not in the
   database. So "keyword retrieval" today is neither PostgreSQL-native nor index-
   assisted.

These mean the milestone's headline comparison (vector / keyword / hybrid / RRF /
rerank) is **not** measuring existing configurations for most rows — it is
building them. That is the honest framing.

---

## Measured: the retrieval matrix

`evidence/210-retrieval-matrix.txt`, artifacts `m013-retrieval-matrix-{dev,heldout}.json`.
Gold is the authored key; ABSENT questions are scored on abstention only.

| Configuration | R@1 | R@5 | R@10 | MRR | nDCG@5 | abstention | mean candidate keys |
|---|---:|---:|---:|---:|---:|---:|---:|
| **dev (175 keyed, 26 abstention)** | | | | | | | |
| current | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| semantic | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| lexical | 0.500 | 0.790 | 0.795 | 0.621 | 0.663 | 0.885 | 2.57 |
| hybrid (RRF) | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| semantic→lexical | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| **held-out v1 (140 keyed, 16 abstention)** | | | | | | | |
| current | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |
| semantic | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |
| lexical | 0.291 | 0.660 | 0.709 | 0.454 | 0.500 | 0.938 | 3.77 |
| hybrid (RRF) | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |
| semantic→lexical | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |

Bootstrap 95% CIs (2000 iterations, seed 12345). For dev `current`, R@1
CI = [0.653, 0.784]; for held-out R@1 CI = [0.497, 0.667]. The dev-to-held-out
drop is large and the intervals do not overlap, which is the generalisation gap in
its clearest form.

**Retrieval quality, not just answer accuracy:** semantic retrieval finds the gold
key 87.4% of the time on dev and 81.4% on held-out, ahead of lexical at 80.0% and
71.4%. Held-out R@1 of 0.582 against dev 0.722 is the number that matters.

### Two findings that redirect the milestone

**1. Fusion as implemented is inert, but fusion in principle is not.**
`hybrid` and `semantic→lexical` are *bit-identical* to `semantic` on both datasets.
My first hypothesis was that the gate prunes to ~2 keys so RRF has nothing to
reorder. **That hypothesis was wrong**, and the diagnostic
(`m013_fusion_diagnostic.py`) disproved it:

| | dev | held-out |
|---|---:|---:|
| semantic misses (gold absent) | 22 | 26 |
| **recoverable by fusion** (gold present as a *lexical-only* key) | **16** | **7** |
| mean semantic candidates | 1.81 | 2.35 |
| mean lexical candidates | 2.52 | 3.80 |
| mean union candidates | 3.03 | 4.28 |

So the union is meaningfully larger than either gate alone, and on **16 of 22**
dev misses (and 7 of 26 held-out misses) the gold key is already in the lexical
set but was pruned by the semantic gate. **Fusion cannot help while it is applied
*after* the gate has discarded the candidates it would recover.** The measurement
identifies a real, bounded opportunity: fuse *before* pruning, or union the two
gates' candidate sets and then rank. That is the next experiment.

**2. Semantic retrieval costs ~1,400 ms p50; lexical costs ~1 ms.** Same corpus,
same process, same warm cache. The embedding of uncached claims dominates. This is
consistent with prior findings that the relevance gate, not the inner product, is
the cost. Any fusion design must account for this: naive fusion roughly doubles an
already-slow path.

### Not established

- Whether pre-gate fusion actually improves end-to-end quality. The diagnostic
  establishes *headroom*, not *achievability*. No fusion change has been made.
- Graded relevance for nDCG@5. With one gold key, nDCG@5 reduces to a rank
  discount. That is the honest form; no graded labels were invented.
- Cross-encoder reranking has not been measured. No reranker exists in the tree.

---

## Known limitations

- **Held-out v1 is no longer blind.** Its 26 failures drove diagnosis. Any number
  produced by tuning against it is a development number. Held-out v2 protocol
  required before any generalisation claim.
- **34 known failures are unclassified**: 26 held-out v1 + 8 dev. They are *not*
  all retrieval failures; several are representation gaps (no authored relation
  exists) or benchmark ambiguity.
- **L2 absolute digest instability** — harness defect, predates this work. Quote
  the invariant, never the digest.
- **Retrieval is not separated from authority by an interface.** `relevance()`
  returns key scores that `select()` consumes; the boundary is real but implicit.
  There is no test asserting that a retrieval change cannot alter authority.
- **Latency envelope is NOT ESTABLISHED.** The cause of ~2.5x run-to-run wall-clock
  variance has never been identified. No number should be called a latency
  guarantee. Prefer deterministic metrics (shared buffers, call counts, rank).
- **Extraction does not exist.** Claims are authored in document front matter
  (`m0115_dataset._claim`) or by the ingestion parser. No automated extraction,
  no LLM claim proposal. Consequence: arbitrary real-world prose cannot become
  authoritative knowledge without an authoring step.
- **No FTS index, no reranker, no router, no OTel** in the current tree.
- **Ingestion is synchronous and per-document transactional.** Idempotence and
  retry behaviour have not been measured.

---

## Rejected hypotheses — preserved, not erased

| Candidate | Result | Why it matters |
|---|---|---|
| Vectorised relevance scorer | 202/202 packs identical, **1.00x–1.07x** end to end | The inner product was never the cost. Kept behind a flag for the equivalence proof |
| Set-based archive-load rewrite | Buffer reads fell 1,057,320 → 2,475, but a **generic-plan cliff made it 12x worse** | Proves plans must be measured through the production driver, not psql |
| Naive candidate narrowing before projection | **170/202** equivalent answers | Currency, conflict and change records depend on state narrowing discards |
| Per-document current-version pointer | **3.6 ms of a 20,894 ms** query | Addressed a symptom that was not the bottleneck |
| Omitting chunk text (`chunk_text=False`) | Payload reduction only | The precedent `version_text=False` followed: kept for payload, **not claimed as a speedup** |
| Bloom-filter pushdown | Folded into the narrowing work | Superseded by the narrowing result |
| Graph storage for relationships | Not justified | Relationship failures are representation gaps, not a missing graph |
| Rewriting the archive-load query to dodge the plan cliff | Seven variants, all byte-identical, **none removes it** | With the search space narrowed to index scans only, the planner still chose wrong. Estimation failure, not search failure |
| `default_statistics_target` to fix the cliff | Byte-identical plan at every size | Inert: it only applies at the next collection, and nothing collected |

**Explicitly retracted:** the earlier claim that a "loaded host" or "2.4x–2.8x
background load" explained latency variance. A diagnostic showed 21% utilisation
and 87–94% idle CPU. The cause remains **not established**. Do not resurrect it.

---

## The separation this milestone must preserve

```
L0  source / verbatim          immutable
L1  authoritative knowledge    claims, evidence, validity,
                               supersession, conflicts, snapshots
L2  derived retrieval          embeddings, caches, indexes, ranking
```

Retrieval may **reorder** candidates. It may never create a claim, alter validity,
erase a conflict, rewrite provenance, or suppress a contradiction. A reranker
reorders. It does not decide truth.

The test for this milestone is `202/202` byte-identical packs across every change
that touches ranking.
