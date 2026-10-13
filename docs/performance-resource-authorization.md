# Performance: what resource-level authorization costs

Measured, not estimated. Every number here comes from `EXPLAIN (ANALYZE)` against
the development database (PostgreSQL 15.19, podman, warm cache), run seven times
with the median and maximum reported. The disputed M006.75 result was not
regenerated and no benchmark score was pursued as an objective.

## The finding

Resource-level authorization is **not free**, and on this workload it costs about
an order of magnitude on a full table scan.

`SELECT count(*) FROM memory_claims`, corpus in scope, as the least-privileged
role, 256 claims:

| Policy | Median | Max |
|---|---|---|
| Corpus-only (the pre-`011` shape) | 2.22 ms | 3.17 ms |
| Resource-scoped (`011`) | 23.29 ms | 23.95 ms |

**~10x, or about 21 ms per scan.**

## Why

Row-level security evaluates its predicate **once per row**. The pre-`011`
predicate was `mindpalace_allowed_corpus(corpus_id)`, which reads one session
setting and compares a string. The new predicate is
`mindpalace_allowed_document(corpus_id, memory_document_id)`, which is
`SECURITY DEFINER` — and has to be, because a plain function would re-enter
`memory_documents`' own policy and PostgreSQL raises *infinite recursion detected
in policy* instead of answering.

Per row, that means: a `SECURITY DEFINER` call, an indexed lookup into
`memory_documents`, a comparison against the acting principal, and — only when the
owner differs — a second lookup into `memory_resource_grants`. At 256 rows that
is ~256 function calls, which is the whole of the 21 ms.

The correctness alternative — `ALTER POLICY ... AS RESTRICTIVE`, keeping
migration `008`'s policy rather than replacing it — costs the same per-row
evaluation and adds a concept. It is not a cheaper design.

## What was *not* done, and why

Denormalising `owner_principal_id` onto `memory_claims`, `memory_versions` and the
rest would turn the per-row lookup into a per-row column comparison, and would
recover most of the 21 ms. It is not done here because of the failure direction:

> A write path that forgets to maintain the denormalised copy leaves the column
> `NULL`, and `NULL` in this design means **shared**.

So a missing copy is a silent cross-tenant disclosure, not a visible error. That
is the same reasoning that made ownership non-transferable and made the archive
append-only. Buying 21 ms with a new silent-disclosure path is a bad trade, and it
is a decision to make with the full picture rather than mid-milestone.

## What it costs in practice

* **Writes are unaffected in kind.** The write path reads one document row, not
  one per row written.
* **Reads that resolve few rows are nearly unaffected.** A lookup by document id
  touches one row and therefore pays the predicate once — the 21 ms is a *scan*
  cost, and the queries a request actually issues are usually keyed.
* **Full scans and aggregations pay it.** Semantic recall, `history`, and
  conflict closure all scan a corpus.
* **The cost is bounded by corpus size**, not by the size of the answer.

Before acting on this, measure the *end-to-end* recall path rather than a bare
scan: the predicate's share of a real request is unknown from this measurement,
and optimising against a synthetic `count(*)` risks trading a real property —
defence against a forgotten predicate — for a number that is not what a user
waits for.

## The honest summary

Resource-level isolation costs roughly 10x on a full-corpus scan in this
configuration. That is a real price for a real guarantee, it is not hidden, and
the optimisation that would remove it has a failure mode that makes it a decision
rather than a chore.