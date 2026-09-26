# Scaling

Every figure here comes from an existing artifact. Nothing was measured for this
document, and no figure is a capacity promise. Recorded results describe one
run on one machine; the M009 release record states that explicitly and calls the
cold sample sensitive to PostgreSQL and OS cache and to fixture setup.

## What scales differently

The repository has two read paths with different cost shapes.

The M009 feed is keyset-paginated over `memory_versions`. Page cost is roughly
flat in archive size, and total traversal time grows with the number of pages.

Ordinary public memory operations are not paginated. `memory._load` reads the
whole time-scoped archive in one statement and `memory_public.project` then
applies selectors in Python, so a single `current`, `history`, or `changes` call
costs more as the archive grows. The response budget bounds output, not read
cost. `README.md` and `examples/MEMORY_API.md` both state this.

## Feed scale

Fixtures were exact relational inserts of 100, 1,000, and 10,000
`memory_versions` rows at page size 50, with two warm-up traversals and 20
measured iterations. The service path, keyset query, cursor handling, and
PostgreSQL connection and session overhead were exercised; generated database
contents were not committed.

Wall-clock milliseconds, p50/p95:

| Versions | First | Middle | Tail | Full traversal | Cold first full traversal |
|---:|---:|---:|---:|---:|---:|
| 100 | 37.944 / 38.724 | 37.708 / 39.752 | 37.390 / 39.072 | 42.216 / 43.937 | 43.896 |
| 1,000 | 39.691 / 42.443 | 42.903 / 44.767 | 45.079 / 49.511 | 198.554 / 206.126 | 200.718 |
| 10,000 | 55.183 / 56.767 | 65.896 / 68.028 | 38.026 / 40.030 | 808.565 / 834.543 | 812.645 |

SQL execution time for a full traversal, p50/p95: 6.938/7.834 ms at 100,
133.588/138.689 ms at 1,000, and 502.867/516.644 ms at 10,000. The full
traversal is 1, 20, and 200 pages respectively at 2 SQL statements per page, one
corpus lookup plus one bounded keyset query.

The tail page is cheaper than the middle page because the index range scan has
less of the table left to walk, which is the shape a keyset design should show.
The cold first traversal is recorded separately from the warm full traversal
because it is sensitive to cache and fixture state.

A 100,000-version feed fixture was attempted in a disposable database and
exceeded the 20-minute bound during fixture and query work. It was terminated,
the temporary database was removed, and
[`docs/performance/feed-100k-attempt.json`](../performance/feed-100k-attempt.json)
records `status: deferred` with no result claimed. Feed behavior above 10,000
versions is unmeasured.

## Query plans

[`docs/m009/query-plans.md`](../m009/query-plans.md) holds real
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` output for first, middle, and tail
queries on the 10,000-row fixture. The plans show no `OFFSET` in the SQL or the
plan, the tuple keyset predicate on middle and tail queries,
`idx_memory_versions_observed` on the `memory_versions` access path, the
expected primary-key lookup for `memory_documents`, and no full-history Python
load.

Execution times inside the captured plans were approximately 0.519 ms, 0.554 ms,
and 0.633 ms. These are lower than the benchmark's per-page SQL figures because
the plan timings exclude the driver round trip that the benchmark includes. Both
are recorded here so the two are not mistaken for a contradiction.

## Memory projection cost

The post-release audit measured ordinary operations on a 1,000-version fixture
(one unique claim per document, with exact evidence) in a warm PostgreSQL
environment, as median wall time over three measured iterations after one
warm-up.

| Operation | Before ms | After ms | Response characters | SQL statements |
|---|---:|---:|---:|---:|
| current | 1,831.301 | 722.755 | 1,408,863 | 4 |
| history | 1,769.214 | 670.126 | 2,132,532 | 4 |
| changes | 1,762.545 | 674.490 | 1,674,863 | 4 |
| evidence | 1,698.795 | 655.854 | 1,812 | 4 |
| as-of | 1,717.028 | 663.686 | 1,408,888 | 4 |
| snapshot | 1,830.774 | 710.233 | 2,199,786 | 11 |
| replay | 1,772.593 | 680.227 | 2,199,786 | 5 |
| pack | 937.535 | 927.356 | 7,407 | 4 |
| feed | 38.000 | 39.963 | not applicable | 4 |

Two things are worth reading off this table.

SQL time stayed between roughly 2.2 and 2.4 ms while wall time fell by about
60%, so the saving was CPU and Python projection work, not fewer queries. The
statement counts are unchanged.

The `evidence` operation returns 1,812 characters and still costs about 656 ms
and four statements, because the archive load is not narrowed to the requested
claim. That is a confirmed architectural cost, recorded in
`docs/evaluation/m006.md` and in `docs/performance/REPORT.md`, not evidence that
a different database is needed.

Response size tracks archive size for the unbounded operations: `history` and
`replay` return more than two million characters at 1,000 versions. `pack` is the
exception, because the budget bounds it.

Raw artifacts: `docs/performance/operations-baseline-1000.json` and
`docs/performance/operations-after-1000.json`. The reusable harness is
`venvmp/bin/python scripts/performance_probe.py operations`, which requires
`DATABASE_URL` and `MIND_PALACE_CURSOR_SECRET`.

## Where the earlier scaling sweep stopped

`docs/evaluation/m006.md` records the larger memory scaling sweep as unfinished.
The combined 100, 500, and 1,000-document run with 20 repetitions exceeded 600
seconds and produced no report; a separate 500-document run after a copy
optimization exceeded 180 seconds; a 100-document run with one repetition
succeeded with intentionally null percentiles. No completed 500, 1,000, or
5,000-document percentile result is claimed, and the timeouts are not attributed
to a specific statement without a completed profile.

The 1,000-version figures in the table above come from a different harness with
a different fixture and supersede nothing in that record.

## Startup and the semantic boundary

`docs/performance/REPORT.md` records the lazy-loading change:

| Probe | Before | After |
|---|---:|---:|
| `api.main` import wall time | 5,534.272 ms | 589.137 ms |
| `api.main` process wall time | 6,783.009 ms | 798.901 ms |
| Imported modules | 4,221 | 682 |
| Peak RSS | 861,428 KB | 80,696 KB |
| `mindpalace_sdk` import | 63.876 ms | 63.876 ms |

After the change, `api.main` imports without Torch, `sentence_transformers`, or
`transformers`. The public SDK import was already model-free. The CLI root help
moved from 265.914 ms to 336.643 ms, which the report records as noise-level
variation rather than a regression.

A claim representation cache followed in migration `007_claim_embedding_cache`.
It is L2: every row is derived from immutable claim text, it is filled by
ingestion and by `mindpalace reindex`, and the read path only reads. Measured on
the 202-question corpus, warm p50 falls from 502.94 ms to 43.24 ms at 88 claims
and every authoritative pack digest is unchanged. See
[`docs/research/retrieval-routing.md`](../research/retrieval-routing.md).

The cost did not disappear; it moved to first use. `Embedder()` construction is
0.015 ms, the first embedding is 5,110.624 ms, a warm embedding is 10.388 ms, and
the dimension is 384. The model then stays resident, and `_model_lock` keeps
concurrent first use from loading it twice.

For capacity planning the consequence is simple: a deployment that only serves
the feed, health checks, CLI help, and database-only paths does not pay the
5.1 second model load. A deployment that answers semantic queries pays it once
per process, on the first request, and needs that much headroom on a cold start.

## Container and dependencies

The API image is about 1.29 GB, based on `python:3.11-slim`, with CPU-only Torch
`2.14.0+cpu` from `requirements-docker.txt`, running as `10001:10001` and
exposing only port 8000. It contains the API runtime, migrations, and mounted
content, and not the CLI, MCP server, tests, evaluation data, research service
modules, reviewer files, or local virtual environments. Model downloads use a
writable temporary `HF_HOME` inside the image.

Most of the size is the CPU Torch dependency, which is the price of keeping the
semantic extension available inside the same image. The base image is
tag-pinned rather than digest-pinned; a digest policy is deferred until the
repository has a multi-architecture update process.

The API image uses a `NullPool` connection pool, because the SDK and CLI create
short-lived event loops per operation and pooled connections would fail across
loops. The API server runs one long-lived loop, so a deployment that only serves
HTTP is not paying for connection reuse today.

## Concurrency and locking

Every writer and snapshot creator takes a transaction-scoped
`pg_advisory_xact_lock` keyed by corpus before touching live or archive rows.
Writes to one corpus therefore serialize. That is what makes the snapshot
capture coherent, and it also means ingestion throughput per corpus is bounded by
that lock rather than by the database.

`migrations/versions/005_multiple_evidence.py` takes the same lock in its
evidence-freeze trigger, so a snapshot capture and a concurrent evidence insert
cannot interleave. The post-release follow-up
`006_snapshot_membership_seal.py` extends that same lock discipline to snapshot
membership. It is not part of the immutable `v0.6.0` release. Its database-backed tests
passed against the local PostgreSQL 15.4 service.

The feed itself takes no writer lock. It is a single bounded read per page, so
feed traffic does not contend with ingestion on the corpus advisory lock.

The `reindex` operator command does take the corpus lock and writes only the
live projection. It is intended for recovery and controlled maintenance, not for
a request path or a second writer racing ingestion.

## Deferred work, with the reason

`docs/performance/REPORT.md` lists these and they are unchanged here:

- A projection-specific archive loader that avoids raw content, metadata, and
  unrelated child rows while preserving conflict closure, provenance,
  snapshots, and historical semantics. Deferred because the existing frozen
  conflict, provenance, pack, and Unicode tests are the correctness gate for any
  rewrite.
- Incremental pack accounting instead of re-serializing the accumulated response
  for each candidate, guarded by the frozen reference algorithm in
  `tests/test_memory_pack_performance.py`. Deferred because the budget counts
  Unicode characters of the exact canonical JSON, so any shortcut risks
  changing an output that is currently byte-stable.
- Replay through a snapshot-version join instead of loading and filtering the
  full archive.
- Bounding RRF branch candidates, deferred until retrieval-quality measurements
  exist to compare against `eval/EVALUATION.md`.
- A complete dependency lock and a base-image digest policy.
- A reproducible 100k feed benchmark with a faster fixture loader or a dedicated
  database-only benchmark instance.
- Moving CPU inference off the event loop beyond the current
  `asyncio.to_thread` call, deferred until a concurrency profile shows
  event-loop blocking is material.

The report's decision is to land the two measured changes and observe before
starting any of this, and not to add Redis, a second vector database, a queue, an
autonomous graph layer, or an MCP feed surface on the strength of these
measurements.

## Post-release follow-up

The follow-up after `bb8ce0b` adds live corpus scoping for the retrieval routes
and a snapshot membership seal. Neither changes the M009 feed or its released
evidence. The corpus scoping change affects the live search, context, query, and
legacy document routes, which read the `documents` and `chunks` tables, not the
archive read path measured above, so the 1,000-version figures do not describe
it either way. The full regression for the follow-up is recorded in the final
validation section of this audit.
