# M012: fast durable memory substrate

## 1. Executive summary

The headline finding of this phase is a rejection, not a ship. The authoritative
archive load was measured to be genuinely quadratic, a set-based rewrite was built
and proved byte-identical and 6.7x faster in the database, and it was then reverted
because the end-to-end trade is not favourable at the sizes a memory product actually
serves, and because the rewrite depends on a planner setting that other engines in
this repository do not have.

Two things shipped: an equivalence test that pins the most correctness-sensitive read
in the system against the statement it replaced, and a measurement record.

## 2. Starting commit

`b15a023af8d4ec7ae3c74718cd1ac40414dd5353`, `v0.6.0` at `033c1484`, PostgreSQL 15.19
from a fresh `pgvector/pgvector:pg15`, 1192 tests passing against a database migrated
from scratch to head including `007_claim_embedding_cache`.

The environment had to be rebuilt: the container was gone at the start of the
session. Every number below comes from that fresh database.

## 3. Release baseline

| check | result |
|---|---|
| `flake8 api cli tests --max-line-length=100` | clean |
| `black --check api cli tests --line-length 100` | clean |
| `scripts/release/check_versioning.py` | PASS |
| `pytest` against fresh database | 1192 passed |
| `v0.6.0` | untouched |
| M006.75 frozen evidence | untouched |

## 4. Current architecture

L0 immutable versions, L1 authored claims with evidence, validity, supersession,
conflicts and snapshots, L2 the live index, the claim representation cache, and
retrieval structures. The public boundary is `memory_public.execute_in_session`, which
loads the archive and projects it.

## 5. Competitive findings

Not re-derived. The pinned source review in `docs/competitive/` was re-checked against
live remotes earlier this cycle and all four pins still match, so it stands. Nothing
this phase learned changes it: the work was database-side, and no competitor
decision was adopted or rejected on the basis of a benchmark number.

## 6. Product hypothesis

A memory substrate that an agent can put on its critical path needs a warm query that
does not scan the whole archive. The previous phase measured warm latency growing from
about 60 ms at 100 claims to about 4.7 s at 10,000, with roughly 90% of the time in a
PostgreSQL socket wait.

## 7. Benchmark corpus

`scripts/benchmark/m0117_scale.py` builds corpora at 100, 1,000, 5,000 and 10,000
claims. Ingestion uses a deterministic offline embedder so building ten thousand
claims is feasible; the query path uses the real cached model, because a latency
number from a fake embedder would mean nothing.

The semantic question set is the frozen 202-question benchmark, dataset hash
`9cdecda73cbdc8ae055926320b85402d47dd7eed0cb28c96b9b191e5bed154a7`.

## 8. Question taxonomy

current 30, historical 30, provenance 26, abstention 25, multi-topic 23, adversarial
19, relationship 34, temporal 13, conflict 2.

## 9. Benchmark integrity checks

Three harness defects were found and fixed while measuring this phase, each of which
would have produced a confident wrong answer.

**The measurement ran nothing.** The fast path returned before calling the query, so
warm and cold both measured the archive load and came out equal, making the claim
cache look worthless. The first corrected run showed the real 7.6x to 15.6x.

**The destructive step was not asserted.** The cache clear ran in a session without
`join_transaction_mode`, rolled back, and the "cold" measurement ran against a warm
cache. It now returns the surviving row count and the run aborts if it is not zero.

**A doubled key prefix archived nothing.** The synthetic anchor documents were written
as `key: architecture.database` and the writer prepended `key: ` again, so no claims
were stored and every probe returned nothing. Caught by asserting that the probe
question resolves something.

Every destructive step now asserts its own effect.

## 10. Existing baseline

| claims | load p50 | warm p50 | cold p50 |
|---:|---:|---:|---:|
| 100 | 15.3 ms | 59.7 ms | 745.8 ms |
| 1,000 | 654.0 ms | 997.7 ms | 7,584.2 ms |
| 5,002 | 372.5 ms | 2,103.3 ms | 32,816.7 ms |
| 10,000 | 665.4 ms | 4,661.0 ms | 61,221.7 ms |

## 11. The candidate-narrowing hypothesis, and what was actually wrong

The hypothesis was that PostgreSQL could select a candidate set before the
application materialises the archive. What the plan showed instead was simpler and
worse.

`EXPLAIN (ANALYZE, BUFFERS)` at 1,000 versions:

```
Nested Loop   rows=1004  loops=1  cost=115.81  hit=1079615
  Index Scan  rows=339   loops=1  cost=8.29    hit=302
  Aggregate   rows=1     loops=1004  cost=8.31  hit=3012
  Aggregate   rows=1     loops=1004  cost=8.19  hit=56224  removed=1003
```

Three correlated subqueries run once per version. Each child lookup walks the corpus
and discards 999 of 1,000 rows. That is O(n^2), and it is why 1,000 versions cost
1,057,320 shared buffer reads.

So the defect was not that too much data was selected. It was that the same data was
read a thousand times.

## 12. SQL strategy

Each child table is aggregated once for the whole corpus and the three aggregates are
joined onto the versions:

```sql
WITH claims AS (
    SELECT mc.version_id, jsonb_agg(to_jsonb(mc) ORDER BY mc.key, mc.id) AS j
    FROM memory_claims mc WHERE mc.corpus_id = :c GROUP BY mc.version_id
), evidence AS ( ... ), chunks AS ( ... )
SELECT v.*, d.path, COALESCE(cj.j, '[]'::jsonb) AS chunks, ...
FROM memory_versions v
JOIN memory_documents d ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
LEFT JOIN claims cl ON cl.version_id = v.id AND v.corpus_id = :c
...
```

A `LEFT JOIN LATERAL` variant was also built and measured.

## 13. Query plans

| statement | exec p50 | plan | shared buffer hits | rows |
|---|---:|---:|---:|---:|
| correlated (shipping) | 639.93 ms | 0.27 ms | 1,057,320 | 1000 |
| CTE set-based | 95.91 ms | 0.44 ms | 2,475 | 1000 |
| LATERAL set-based | ~580 ms | — | — | 1000 |

427x fewer buffer reads, 6.7x faster execution, same rows, same order.

## 14. Equivalence methodology

`tests/test_memory_load_equivalence.py` keeps the previous statement as an oracle and
compares `str()` of every returned row, for both chunk modes and for an unfiltered and
a path-filtered read, over a corpus containing a supersession, a live conflict, a
tombstone, multiple claims per version, multiple evidence rows per claim, and unicode.

Additional assertions cover tombstones remaining loadable as history, ordering by
`(observed_at, path, version_number)`, `as_of` narrowing, and that `chunk_text`
changes only the chunk payload and nothing else.

**Byte-identical: true.** The set-based statement was verified equal before it was
timed, and the benchmark refuses to time a non-equivalent rewrite.

## 15. Adversarial cases

The equivalence corpus deliberately contains a conflict whose two sides share an
authored key but not their wording, a superseded predecessor, a deleted document that
must remain historically resolvable, and evidence on more than one chunk. All of it is
in the equivalence test, which passes.

## 16-20. Accuracy, abstention, conflict, historical, provenance

Unchanged. 202-question benchmark before and after the rewrite and the revert:
semantic 178/202, lexical 164/202, lexical-then-semantic **194/202**. Failures: 3
ranking, 2 abstention, 3 relationship. Provenance, conflict closure, validity and
supersession are unaffected because none of them moved.

## 21. Latency results

End to end, warm p50, rewrite applied:

| claims | before | rewrite | reverted |
|---:|---:|---:|---:|
| 100 | 59.7 ms | 131.6 ms | 59.7 ms |
| 1,000 | 997.7 ms | 283.4 ms | 997.7 ms |
| 5,002 | 2,103.3 ms | 1,728.8 ms | 2,103.3 ms |
| 10,000 | 4,661.0 ms | 4,249.8 ms | 4,661.0 ms |

## 22. Scale results

Measured to 10,000 claims. **25,000, 50,000 and 100,000 were not measured.** Ingestion
alone at 10,000 claims took 493 s with a deterministic embedder, so the larger sizes
were not attempted within this phase. That is a real gap in the exit criteria and is
recorded as such rather than estimated.

## 23. Database results

The `EXPLAIN (ANALYZE, BUFFERS)` output in section 11 is the whole database result.
Index use, rows scanned, rows returned, buffer counts, loops and sort behaviour are all
in `docs/performance/m012-load-plan.json` and `m012-narrowing.json`.

## 24. Memory and model results

Not measured this phase. The claim representation cache from the previous phase is
untouched and its 7.6x to 15.6x cold penalty still holds.

## 25. Router results

Unchanged. 148 of 202 questions need no semantic work, 26 do, 16 are answered better
without the model than with it. No router was changed this phase.

## 26. Failure analysis

Two things went wrong, and both were caught by measurement rather than by inspection.

**A 6.7x database win would have been a 12x production regression.** The CTE form is
only fast under a custom plan. With `plan_cache_mode = force_generic_plan` it takes
6.6 s at 1,000 claims. asyncpg prepares a statement after five executions, and
PostgreSQL then switches on its own: the first five samples were 140, 125, 125, 122,
122 ms and the next three were 6,803, 6,620 and 6,920 ms. A benchmark that measured
only `EXPLAIN ANALYZE`, as the brief suggested, would have shipped this.

**The obvious fix is not a good trade.** Even with the plan forced, the rewrite is 3.5x
faster at 1,000 claims and 2.2x slower at 100, because three hash aggregates and three
joins cost more than a few index lookups on a small corpus. The absolute numbers are a
714 ms win and a 72 ms loss, but the relative regression lands on the case an agent's
hot loop actually hits. Setting `plan_cache_mode` on the engine instead of per query
did not fix it, because the benchmark and the test fixtures build their own engines,
so the property would have held only for code that happens to use `async_engine`.

## 27. Security analysis

No change to poisoning, cross-corpus isolation or provenance. The equivalence test
adds coverage for tombstones and multi-evidence rows, which are the shapes most likely
to be mishandled by a narrowing change.

## 28. Developer experience analysis

Not re-measured this phase. The seven-line integration script from the previous phase
is unchanged and still passes.

## 29. Competitive capability matrix

Unchanged from `docs/competitive/capability-matrix.md`, whose pins were re-verified
against live remotes this cycle. This phase produced no capability change.

## 30. Architecture decision

**HOLD.** Not because the architecture is wrong, but because a critical measurement is
missing: the 25k to 100k scale range was not measured, and the 10k point shows the
archive load is only about 14% of warm time, so the database is no longer the
dominant term where the product actually needs to work.

## 31. Accepted changes

`tests/test_memory_load_equivalence.py`: six tests pinning the archive load against the
statement it replaced, on a corpus built from the failure shapes. Suite 1192 to 1198.

## 32. Rejected changes

**The set-based archive load.** Reverted, with the numbers in section 26.

**`plan_cache_mode = force_custom_plan` on the engine.** Reverted with it. It would
have protected the rewrite from the generic-plan cliff, and it would have silently
failed to apply to every engine in this repository other than the production one.

**True candidate narrowing.** Not attempted. It is the genuinely correct fix, and it
changes which archive rows the application can see, which is a correctness question
about conflict closure and validity, not a query-tuning question. It needs its own
equivalence proof across the adversarial cases in section 15.

## 33. Remaining limitations

- 25k, 50k and 100k claim corpora were not measured.
- The 10k warm path is 4.7 s, and after the database wait the dominant term becomes
  `project` and `select`, which build pydantic models for every claim before relevance
  has narrowed anything. That is the next target and it is not the database.
- The 202-question corpus is synthetic and small. It measures correctness, not
  retrieval quality at scale.
- Run-to-run variance on this host is about 2x, so the tables above are order of
  magnitude, not precise values.

## 34. Release scope

Nothing to release. No user-visible capability changed, and the only production
reverted was reverted. The 202-question benchmark is unchanged at 194/202.

## 35. Next milestone

**Profile the warm path at 10,000 claims and attack `project` and `select`.**

The load is now about 14% of warm time at that size, so the database is no longer the
bottleneck. `project` builds a `MemoryResponse` for every claim in the archive, and
`select` then discards all but a handful. The question to answer is whether relevance
can be computed over a cheap column projection before any pydantic model exists, which
would make the materialisation proportional to the answer rather than the archive.

That is genuinely candidate narrowing, done at the right layer: narrow before
materialise, not narrow in the database, and with the same equivalence proof this
phase built.
