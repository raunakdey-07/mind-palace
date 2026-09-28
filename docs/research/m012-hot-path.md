# M012 architecture break: from archive-scale to answer-scale resolution

## Decision

**HOLD.** No production architecture changed. The central hypothesis of this
milestone is disproven, with numbers, and the exact remaining bottleneck is
identified.

## 1. Starting commit

`3b0c5de1b6d1b1d1b77621364b81e8c272fa9a84`, `v0.6.0` at `033c1484`, PostgreSQL
15.19, 1198 tests passing, `flake8` and `black` clean at the CI line length,
`check_versioning.py` PASS. The database container was gone at session start and
was rebuilt from scratch, migrated to head.

## 2. Release baseline

No release candidate. Nothing user-visible changed. `AGENTS.md` was removed from
tracking as instructed, after confirming nothing in CI, scripts, docs, the
package config or the Makefile referenced it, and preserving a copy outside the
repository.

## 3. The hot path, measured

`scripts/benchmark/m012_hot_path.py`, 10,000 claims, median of a warm query:

| stage | ms | share |
|---|---:|---:|
| archive load | 601.80 | 2.9% |
| projection | 2,280.92 | 10.9% |
| resolution | 17,761.12 | 85.0% |
| total | 20,894.60 | |

What one query builds to answer one question:

| materialised | count | in the answer |
|---|---:|---:|
| versions | 10,000 | |
| claims | 13,332 | 1 |
| evidence rows | 10,000 | 1 |
| sources | 10,000 | |
| changes | 10,000 | 0 |
| **conflict groups** | **55,573** | 0 |

**13,332 claims and 55,573 conflict groups to return one claim.** The hypothesis
that the system materialises far more than the answer needs is confirmed, and the
database is not where the cost is.

## 4. Why the previous phase's database numbers under-reported the problem

The 601.80 ms load is 2.9%, not the 14% the earlier run suggested, because that
run measured a different stage boundary. The profiling conclusion of the previous
milestone, that roughly 90% of a warm query is database wait, holds at 1,000
claims and does **not** hold at 10,000. Resolution overtakes the database by
5,000 claims. Optimising the load further would have been optimising 2.9% of the
problem.

## 5. The experiment

`scripts/benchmark/m012_candidate.py`. The claim tested: relevance consumes only
four fields of a claim, `id`, `claim`, `key` and `path`, and those four already
exist as plain values in the rows the archive load returns. So the relevance gate
should be able to run before any Pydantic model exists, and the expensive
projection should be built only for the keys the gate selected.

The gate was the production `relevance` function with the production scorer, on a
stand-in response holding lightweight row views, so key selection is identical to
the shipped path by construction. Equivalence is `canonical_json()` equality of
the resulting pack, over the frozen 202-question benchmark.

## 6. Results

| narrowing strategy | equivalent answers |
|---|---:|
| lexical gate, narrow by key | 137/202 |
| embedding gate, narrow by key | 169/202 |
| embedding gate, narrow by document | **170/202** |

Narrowing is extremely effective at reduction: a median of **2 versions kept out
of 72**, 24 questions falling back to the full path.

It is not safe. 32 answers differ.

## 7. Failure analysis

The first result, 137/202, was my error and worth recording: the cheap gate used
the lexical scorer while the baseline used the embedding scorer, so it selected
different keys. A cheap stage is only admissible if it is a provable superset of
the real gate, and a lexical scorer is not. That is Experiment A in the brief, and
it fails.

The real result is the other two. Even with the gate made identical, key-level
narrowing breaks 33 answers and document-level narrowing still breaks 32.

The cause is not key selection. It is that `project` and `select` derive three
things from the whole archive:

- **currency**, from the full version list, so dropping a document's older
  version makes a superseded claim look current
- **conflicts**, grouped per key across every document that authored it
- **change records**, which link claims in one document to claims in another

Narrowing by document fixed currency inside a document and moved only one answer.
What remains is change records: a change held in a dropped document can reference
a claim in a kept one, and `select` keeps any change that touches a selected
claim. Making narrowing correct therefore requires a transitive closure over
documents reachable through change records, and that closure grows toward the
whole archive.

Mismatches by prefix: relationship 14, provenance 7, historical 6, temporal 3,
abstention 1, multi-topic 1. Every category that depends on change or history is
affected, which is the signature of the cause above rather than of bad
candidates.

## 8. What this says about the architecture

The authoritative projection is irreducibly archive-global. Currency, conflict
and change are properties of the whole ledger, not of a key. That is not an
implementation weakness to be tuned away; it is a direct consequence of the
guarantees the product is for. An archive where a superseded claim could be
isolated away from its successor would be a cheaper memory system, and it would
also be one that could answer "what is true now" wrongly.

The honest consequence: **query cost cannot currently be made proportional to
answer size**, because the answer is a function of the whole archive.

## 9. What was not measured

- **25,000 claims**: not measured. Ingestion at 10,000 claims took 493 s with a
  deterministic embedder.
- **Memory and model behaviour under the narrow path**: not applicable, the narrow
  path was not shipped.
- **Concurrent execution**: not measured this phase.
- **25k to 100k**: not measured and not extrapolated.

## 10. Complexity class

| stage | class today | class if narrowing worked |
|---|---|---|
| load | O(n) rows, O(n²) buffers | O(n) |
| projection | O(n) claims, O(n) conflict groups per key | O(k) |
| resolution | O(n) scored | O(k) |
| total | O(n) with a large constant | O(k) |

The rewrite would have moved the system from O(n) to O(k). It cannot be done
without changing what "current" and "changed" mean.

## 11. What should be built next

**Make the archive-global relation explicit and queryable, so narrowing has a
bounded closure.** The concrete form: a persisted, rebuildable index from claim to
the change records and documents that can affect its status, so a candidate
narrowing can pull the transitive set directly instead of loading the archive to
discover it. That is a derived structure, L2, rebuildable from L0 and L1, and it
is the smallest thing that makes O(k) reachable without weakening authority.

Until that exists, the correct engineering position is that a memory substrate
which reasons over its entire history costs O(n) per question, and that is the
price of the guarantee.

## 12. What should not be built

No graph, no vector store, no cache layer, no new retrieval subsystem, no trust
classes, no LLM extraction. A graph would not fix this: the problem is that
currency is global, and a graph over the same relations has the same closure.

## 13. Invariants

All re-checked and unchanged: 1198 tests passing, 202-question benchmark at
194/202 with 3 ranking, 2 abstention and 3 relationship failures, frozen M006.75
evidence untouched, `v0.6.0` untouched, CI lint clean.
