# Query engine architecture and scale

The P0 list from the previous phase is now closed except for the scale measurement:
the claim representation cache shipped in migration `007`, the lexical-first fallback
is the shipped adapter, authority regression is proven, and here is the scale curve
that was previously missing.

## Starting state

`faf3d9d`, `v0.6.0` at `033c1484`, PostgreSQL 15.19, 1192 tests passing against a
freshly migrated database. The cache had only been measured to 88 claims, and a
result measured on a small corpus is not a result.

## Scale, measured

`scripts/benchmark/m0117_scale.py`, real PostgreSQL, real ingestion, real cached
`all-MiniLM-L6-v2` on the query path. Offline embedder only while building a corpus,
because a latency number from a fake embedder would mean nothing.

| claims | load p50 | warm p50 | cold p50 | cold ÷ warm |
|---:|---:|---:|---:|---:|
| 100 | 15.3 ms | 59.7 ms | 745.8 ms | 12.5x |
| 1,000 | 654.0 ms | 997.7 ms | 7,584.2 ms | 7.6x |
| 5,002 | 372.5 ms | 2,103.3 ms | 32,816.7 ms | 15.6x |
| 10,000 | 665.4 ms | 4,661.0 ms | 61,221.7 ms | 13.1x |

Two findings, one comfortable and one not.

**The cache is worth its keep everywhere.** Cold is 7.6 to 15.6 times warm at every
size, and the gap does not close as the archive grows. At ten thousand claims the
uncached path takes about a minute per question.

**Warm latency is not flat.** It goes from about 60 ms at 100 claims to about 4.7 s at
ten thousand. That is a real product limit: a coding agent calling
`memory.recall(...)` once per step would spend seconds per call.

## Where the warm time goes

Profiled with `cProfile` at 1,000 claims, three warm queries:

| | cumulative |
|---|---:|
| whole profiled run | 5.999 s |
| `memory._load` | 4.845 s |
| **`epoll` wait, i.e. time spent waiting for PostgreSQL** | **4.401 s** |
| `project` | 0.770 s |
| `select` including relevance | 0.447 s |

About 90% of a warm query is the database socket wait on the archive load. The Python
work, which is the part most likely to be optimised carelessly, is about 1.2 s of 6 s.

The cause is that `memory._load` materialises the whole archive: every version, every
claim, every evidence row and every chunk, aggregated as JSON, for a query that
typically needs a handful of keys. It is O(archive) in both directions, and the
per-query work does not shrink as the question gets narrower.

## What was changed

The public projection reads exactly two things from a chunk: `id` and `heading_path`.
`_load` was shipping the chunk `text` anyway, which is the bulk of a real document.

`_load` now takes `chunk_text=True`, and the public boundary passes `False`. The raw
`history()` surface keeps the text, because a developer asking for history wants the
source, and the legacy `_query_result` path keeps the full chunk because it attaches
it to evidence.

Measured effect on a corpus with realistic document bodies, 300 documents:

```
chunk text included:  1006.7 KB
id and heading only:   855.6 KB
reduction:              15.0%
```

Latency effect at 1,000 claims was 743.6 to 706.4 ms of load, which is **inside this
host's run-to-run noise** and must not be reported as a win. The change is kept
because it is provably strictly less data with a correctness argument that does not
depend on the corpus, and because the benefit scales with document length, which the
synthetic scale corpus understates.

## What was not changed, and why

**Server-side candidate filtering.** This is the fix the profile points at, and it is
not a Python optimisation: the query would have to select candidate claims in
PostgreSQL before they are materialised, which changes the authoritative read path.
Conflict closure, change records and validity windows all reason across the whole
archive, so narrowing what is loaded is a correctness question, not a query-tuning
question. It needs its own equivalence proof, and shipping it unproven would risk the
one thing this project has spent a year protecting.

## Three harness defects found while measuring this

Recorded because each would have produced a confident wrong answer.

**The measurement ran nothing.** The fast path in `time_query` returned before calling
`run_query`, so "warm" and "cold" both measured the archive load and came out equal.
The cache appeared to do nothing. Fixed by always resolving, and the first corrected
run showed the real 7.6 to 15.6x.

**The destructive step was not asserted.** `clear_cache` used a session without
`join_transaction_mode`, so the delete rolled back and the "cold" measurement ran
against a warm cache. It now returns the surviving row count and the run aborts if it
is not zero.

**A doubled key prefix archived nothing.** The synthetic anchor documents were
generated as `key: architecture.database` and the writer prepended `key: ` again,
producing `key: key: ...`. No claims were stored, and every probe question returned
nothing. Caught by asserting that the probe question resolves something.

## The numbers are noisy, and here is how much

Two runs of the same 1,000-claim corpus on this host produced warm medians of 1,276 ms
and 997 ms, and individual samples within one run moved between 897 ms and 1,517 ms.
At 10,000 claims, warm medians ranged from 4,661 ms to 13,947 ms across runs before
and after the narrowing.

So the order of magnitude is robust and the precise value is not. Anything quoted here
to better than about 2x should be treated as noise on this machine. The trend, the
7.6-to-15.6x cold penalty and the 90%-database-wait profile are the reliable parts.

## Decision

**ADOPT** the chunk narrowing: strictly less data, no correctness risk, 1192 tests
unchanged.

**REVISE** the scale posture. The claim representation cache removed the model from the
cost curve, and the next dominant term is a database-bound load that scales with the
whole archive. That is now the product's scale limit, and it is not solved.
