# M012.3: projection efficiency and the release gate

## 0. Release preparation addendum

Added during final release preparation. Three bounded investigations were run to
close or bound the open items, and one documentation correction was made. No
production code changed in this addendum.

### Archive loading: what the discriminating experiment established

`EXPLAIN (ANALYZE)` reports plan and execution but **excludes serialising rows
to the client**. Comparing it against the wall clock of the real loader
separates the output path from the query, which the previous report could not do:

| claims | wall p50 | db exec | planning | serialisation | share |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 914.9 ms | 585.9 ms | 0.26 ms | 329.0 ms | 36.0% |
| 5,000 | 325.0 ms | 182.5 ms | 0.27 ms | 142.5 ms | 43.8% |

Two things are now established, and one is not.

**Established.** Output serialisation and transfer are 36% to 44% of the load's
wall clock at both sizes. The earlier report's "80% at 1k, 14% at 10k" framing
was a share artefact: it moved the denominator, not the cost. Each version row
is 1,568 bytes wide, because `v.*` carries the full document content alongside
three JSON aggregates, and shipping those rows is a large and roughly constant
share of the load.

**Established.** The sort changes strategy with size. At 1,000 versions it is
`quicksort`, 2,453 blocks, in memory. At 5,000 it is `external merge`, 12,056
blocks, on disk. The archive load spills its sort to disk on a corpus a real
deployment would recognise as ordinary.

**Not established.** The inversion persists inside the database: 585.9 ms of
database time for 1,000 rows against 182.5 ms for 5,000. JIT is ruled out
(`jit_in_plan=False`, and disabling it changes nothing) and variance is ruled out
(tight repeated samples, reproduced in a separate process). The experiment could
not distinguish the remaining explanations, so per the brief's instruction the
investigation stopped here rather than continuing into a rejected SQL rewrite.

### Relationship reasoning: the three failures classified

Each was run against the live archive and read, not inferred from the score.

| Question | Returns | Classification |
|---|---|---|
| What constraint did the March orders outage introduce? | `incident.2024-03.orders.root` (the cause) | **ranking.** `constraint.orders.pool` is authored, has evidence, and is in the *same document*, but the 90% relative band keeps only the stronger sibling |
| Which decision rationale covers the current job queue? | `architecture.queue` | **ranking, caused by vocabulary.** The rationale claim exists, but the question says "rationale" and the key says "reason"; `decision` is a stopword, so no term links them |
| Which service owns object storage decisions? | `architecture.storage`, `decision.storage.reason` | **missing representation.** No authored claim states that a service owns storage decisions. Nothing to retrieve |

None is a contract violation that can be fixed safely inside this task. The
first two would need a relevance-threshold or stemming change, and those are
exactly the changes that moved benchmark answers before; the third is a corpus
authoring gap, not a product defect. They are documented as a known gap in the
README rather than changed.

### Documentation correction

The README stated no current latency envelope, so a reader had only historical
M006.5 numbers to go on. It now carries the measured warm p50 at 100, 1,000,
5,000 and 10,000 claims, states that 25,000 and above were not measured, names
the host variance, and says plainly that projection dominates above 5,000
claims. The existing M006.5 figures were left alone; they are labelled in place
as historical evidence and are not a general latency guarantee.

## 1. Baseline and environment

| | |
|---|---|
| HEAD | `76065f9`, clean tree before and after |
| `v0.6.0` | `033c1484`, untouched |
| M006.75 frozen evidence | untouched |
| `AGENTS.md` | untracked, local copy present |
| Database | restored by `scripts/benchmark/ensure_postgres.sh`, migrated to head |
| Python / PostgreSQL / pgvector | 3.14.7 / 15.19 / 0.8.6 |
| model | `all-MiniLM-L6-v2`, cached, `HF_HUB_OFFLINE=1` |

```
venvmp/bin/python -m pytest -q                              1200 passed
flake8 api cli tests --max-line-length=100                  clean
black --check api cli tests --line-length 100                clean
scripts/release/check_versioning.py                         PASS
202-question benchmark                                       194/202
```

## 2. Absolute stage timings

Real public query path, warm, claim vector cache asserted fully populated, zero
misses permitted or the run aborts. 20 samples per size, 12 at 10,000.

| claims | total p50 | archive load | project | vector cache | select | pack |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 47.11 ms | 14.75 ms | 5.61 ms | 8.96 ms | 14.42 ms | 0.18 ms |
| 1,000 | 1,009.58 ms | 811.52 ms | 73.57 ms | 65.69 ms | 42.36 ms | 0.18 ms |
| 5,000 | 1,744.81 ms | 311.05 ms | 819.03 ms | 371.03 ms | 174.98 ms | 0.21 ms |
| 10,000 | 4,435.59 ms | 639.43 ms | 2,462.80 ms | 736.21 ms | 384.82 ms | 0.23 ms |

**Pack construction is 0.0% to 0.4% at every size.** It is not worth
optimising, and this milestone did not touch it.

## 3. Archive-load scaling: explanation

The previous report said the load's *share* moves from about 80% at 1,000 claims
to 14% at 10,000. That framing was wrong, and this is the correction.

**The shares moved because the denominator moved, not because the load got
relatively cheaper.** The absolute load times are:

```
   100 claims   14.75 ms
 1,000 claims  811.52 ms
 5,000 claims  311.05 ms
10,000 claims  639.43 ms
```

Five times the data loads in 38% of the time. That is not a scaling curve, and I
am no longer presenting it as one.

I tested two candidate explanations and can rule both in or out:

- **JIT**: ruled out. The plan reports `jit_in_plan=False` at both sizes, and
  `SET LOCAL jit = off` changes nothing (1,000 claims: 627.5 ms with JIT on,
  659.4 ms off; 5,000 claims: 326.2 ms on, 317.2 ms off).
- **Noise**: ruled out. Six samples per size, tight spread, reproduced in a
  separate process: `[611.1, 613.3, 614.4, 646.7, 640.5, 648.0]` at 1,000
  against `[334.6, 322.8, 320.6, 328.5, 323.9, 358.1]` at 5,000.

What the plans actually show is one visible difference. At 1,000 versions the
outer scan is a plain `Index Scan`; at 5,002 it is a `Bitmap Heap Scan` over the
same nested-loop shape. In both cases essentially all reported time sits in the
top `Sort` node and the three correlated aggregates cost under 0.4 ms combined:

```
1,000:  Sort 609.974 ms   Aggregate 0.009 / 0.21 / 0.383
5,002:  Sort 193.687 ms   Aggregate 0.008 / 0.012 / 0.005
```

So the load is dominated by sorting wide rows that each carry three JSON
aggregates, and the plan shape for that scan changes with row count in a way that
does not scale monotonically. **I could not isolate the mechanism further inside
this milestone, and I am not going to guess at it.** The honest statement is that
the archive load is not a clean linear function of archive size at these sizes,
the plan shape changes between 1,000 and 5,000, and the cause is not JIT and not
variance.

## 4. The projection optimisation, and its decision

### What `project` builds

Traced from `api/services/memory_public.py`:

1. `_query_result(versions, ...)` resolves the whole archive: currency,
   supersession, validity, conflicts, change records.
2. An `Evidence` Pydantic model is built for **every** evidence row.
3. A `Claim` Pydantic model is built for **every** claim.
4. Only then is `matched_ids` applied, and `select()` keeps a handful.
5. `_attach` rewrites evidence and sources down to the survivors.

At 5,000 claims the archive resolves to 5,000 `Claim` objects and 5,000
`Evidence` objects, and the pack retains on the order of one claim. The ratio of
built to kept is above 99%, which is consistent with `project` being 47% and 55%
of warm time at 5,000 and 10,000 claims.

### The candidate

Construct `Claim` and `Evidence` models only for claims that survive
`select()`, running relevance over lightweight row views in between. `relevance`
already consumes only `id`, `claim`, `key` and `path`, and a row view over those
four was exercised successfully during M012.

### Why it is not implemented

The work is not a cache and not a micro-optimisation. It is a restructuring of
the authoritative read path, because `select()` currently operates on Pydantic
models and would have to operate on resolved rows instead, and because conflict
closure, change records and evidence attachment all read from the fully
constructed response.

The specific risk is already known and measured. M012 narrowed the archive
before projection and lost 32 of 202 answers, because narrowing drops state that
currency, conflicts and change records depend on. The same closure problem
applies here: deciding which claims can be built lazily requires the conflict
and change closure to be computed first, and that closure is the expensive part.

Proving equivalence would therefore require the full 202-question byte-identity
check plus adversarial fixtures for cross-document changes, conflicting keys,
historical validity and dependency chains, all re-run after the refactor. That is
a milestone's work, not a closing experiment, and shipping an unproven change to
the authoritative read path is precisely what this repository's history says not
to do.

**Decision: retain the current implementation.** The brief's stop condition
covers this case: projection cannot be made materially cheaper without risking
authority, so the limitation is documented and the release gate is finished
instead of opening another optimisation milestone.

## 5. Canonical and adversarial equivalence

| check | result |
|---|---|
| Full suite, exact CI path | 1200 passed |
| 202-question benchmark | 194/202, failures unchanged: 3 ranking, 2 abstention, 3 relationship |
| L2 destroy, pack digest | `47c24c28...`, 12/12 identical |
| L2 rebuild, pack digest | `47c24c28...`, 12/12 identical |
| L2 destroy including the claim cache | 202 packs, `identical: True` |
| Model unavailable | 202/202 answered, 163 correct |
| Retrieval unavailable | 202/202 answered, 163 correct |
| Poisoning, 202 questions | 0 untrusted claims, 0 injection markers in claims or evidence |

No production code changed in this milestone, so there is no new equivalence
claim to make. The gate is re-run, not re-proven.

## 6. Known limitations and the tested operating envelope

**Tested envelope, warm, real public path:**

| corpus | p50 | measured as |
|---|---:|---|
| 100 claims | 47 ms | 20 samples |
| 1,000 claims | 1.01 s | 20 samples |
| 5,000 claims | 1.74 s | 20 samples |
| 10,000 claims | 4.44 s | 12 samples |
| 25,000 claims and above | not measured | — |

**Open, sized, reproducible:**

1. `project` is 47% of warm time at 5,000 claims and 55% at 10,000. It builds a
   Pydantic model per claim and keeps roughly one. The fix requires solving the
   dependency closure first, which is exactly what M012 showed cannot be done
   naively.
2. The archive load is not a clean linear function of archive size between 1,000
   and 5,000 versions. Plan shape changes there; JIT and variance are both ruled
   out; the mechanism is not identified.
3. Relationship questions remain 3/34 on the authored benchmark.
4. Host run-to-run variance is roughly 2x, so single measurements are not cited.

**Not claimed:** speed at 25,000 claims or above, any improvement to the
194/202 answer set, or resolution of the relationship failures.

## 7. Final decision

**READY WITH DOCUMENTED LIMITATIONS.**

Supported by: a real production defect found and fixed last milestone
(5.83x at 5,000 claims, neutral at 100), every authoritative invariant re-verified
byte-for-byte on this commit, 1200 tests on the exact CI path, 194/202 unchanged,
degradation and poisoning clean across 202 questions, a reproducible database
setup, and a clean-room install confirming the Memory Pack reader still depends on
nothing.

The projection investigation produced a precise, correct finding and no change,
which is the outcome the brief explicitly allows.

## 8. Release version and tag

Determined from the repository's own conventions, not assumed. `pyproject.toml`
is at `0.6.0`; the tag series is `v0.4.0`, `v0.4.1`, `v0.5.0`, `v0.5.1`,
`v0.6.0`; and `scripts/release/check_versioning.py` pins the package version,
the changelog heading, the README release link, and the milestone-to-release
rows in `docs/release-map.md`.

The work since `v0.6.0` is backward compatible: new L2 tables and caches, a
query fix, and a new public reader, with no change to the Memory Pack schema,
which stays at version 1, and no change to any public response contract. That is
a minor release.

**Version `0.7.0`, tag `v0.7.0`.**

A release would require, and none of this is done yet:

1. `pyproject.toml` to `version = "0.7.0"`.
2. `scripts/release/check_versioning.py` updated to expect `0.7.0` and the
   matching `## [v0.7.0]` changelog entry, plus the release-map row.
3. A `## [v0.7.0]` entry in `CHANGELOG.md` covering the claim-representation
   cache, the prepared-statement fix, the dependency-free reader, and the
   measured envelope.
4. The tag itself.

Steps 1 to 3 are mechanical and CI checks them. None has been performed, and
none should be, without authorisation.

Blocking items for correctness: none.

The smallest concrete action left is to tag the release. The single next
engineering action, when it is taken, is to solve the dependency closure so
`project` can build models for surviving claims only.
