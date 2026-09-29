# M012.2: resource efficiency and release readiness

## Decision: READY WITH DOCUMENTED LIMITATIONS

One real defect found and fixed: a prepared-statement cliff in the cached-vector
load that made every warm query after the sixth on a connection about 22x
slower. It was present in production, not only in benchmarks. It explained the
13x discrepancy that the previous milestone could not account for.

## 1. State at start and finish

| | start | finish |
|---|---|---|
| commit | `2deebae` | this milestone's two commits |
| working tree | clean | clean |
| `v0.6.0` | `033c1484` | `033c1484`, untouched |
| M006.75 frozen evidence | untouched | untouched |
| `AGENTS.md` | untracked, local copy present | untracked, local copy present |

## 2. Baseline commands and environment

Database restored with `scripts/benchmark/ensure_postgres.sh`, which is now the
reproducible path: bring up the container, assert the three extensions, migrate
to head, fail loudly if the database is unreachable.

```
Python 3.14.7   numpy 2.5.2   torch 2.13.0+cu130
PostgreSQL 15.19   pgvector 0.8.6
model all-MiniLM-L6-v2 (cached, HF_HUB_OFFLINE=1)
venvmp/bin/python -m pytest -q            1199 passed
flake8 api cli tests --max-line-length=100  clean
black --check api cli tests --line-length 100  clean
scripts/release/check_versioning.py        PASS
202-question benchmark                     194/202
```

## 3. The 13x discrepancy: cause found

It was a real product defect, not a measurement artifact.

`claim_embeddings.load_cached` passed the wanted claim ids and hashes as two SQL
array parameters. Those arrays are nearly redundant: the index
`idx_claim_embeddings_lookup` already scopes the rows to
`(corpus_id, embedding_model, embedding_dimension)`, and the function already
filtered in Python against `wanted`.

Isolated, three consecutive executions per trial, 5,002 claims:

```
trial 0: [369.1, 405.2, 377.8] ms
trial 1: [380.9, 391.6, 8384.7] ms
trial 2: [8416.7, 8405.1, 8410.4] ms
trial 3: [8415.3, 8405.5, 8386.3] ms
```

It flips on the sixth execution and never recovers. That is asyncpg's prepare
threshold. `EXPLAIN` before and after is byte-identical, so it is not a plan
switch: it is the cost of pushing two five-thousand-element arrays through the
prepared-statement path.

Why the earlier harnesses disagreed:

- the cProfile run made 2 calls and never crossed the threshold;
- the end-to-end harness made 24 calls and crossed it;
- the plan-cliff experiment I ran to test this hypothesis set
  `plan_cache_mode` explicitly in all three arms, which **disables automatic
  plan selection and therefore hides the very cliff I was looking for.** That
  test concluded "no cliff" and was wrong.

The production consequence is the serious part: a running server holds one
pooled connection and issues this query for every warm request, so every caller
after the fifth pays the slow path for the life of the connection.

## 4. The fix

Select by the indexed prefix and filter in Python, which the function already
did:

```sql
SELECT claim_id, representation_hash, embedding::text
FROM memory_claim_embeddings
WHERE corpus_id = :corpus AND embedding_model = :model AND embedding_dimension = :dimension
```

```python
if wanted.get(claim_id) != stored_hash:
    continue
```

This is not merely equivalent, it is stricter. The old SQL asked that the stored
hash be a member of the wanted set; the new check asks that it equal the wanted
hash for that claim. A row whose text has since changed is now rejected rather
than admitted.

No schema change, no new dependency, no new index.

## 5. Measurements

Real public path, warm, claim vector cache asserted fully populated, zero
misses permitted or the run aborts. 20 samples per size, 12 at 10k.

| claims | total before | total after | speedup | vector cache before | after |
|---:|---:|---:|---:|---:|---:|
| 100 | 50.25 ms | 47.11 ms | 1.07x | 13.16 ms | 8.96 ms |
| 1,000 | 1,358.59 ms | 1,009.58 ms | 1.35x | 402.30 ms | 65.69 ms |
| 5,000 | 10,167.58 ms | 1,744.81 ms | **5.83x** | 8,841.44 ms | **371.03 ms** |
| 10,000 | not measured | 4,435.59 ms | — | not measured | 736.21 ms |

The cached-vector load itself is 23.8x faster at 5,000 claims, which matches the
23x cliff removed. The win grows with corpus size and is neutral at 100 claims,
so the common small case does not regress.

Stage share of p50 after the fix:

| claims | archive load | project | vector cache | select | pack |
|---:|---:|---:|---:|---:|---:|
| 100 | 31.3% | 11.9% | 19.0% | 30.6% | 0.4% |
| 1,000 | 80.4% | 7.3% | 6.5% | 4.2% | 0.0% |
| 5,000 | 17.8% | 46.9% | 21.3% | 10.0% | 0.0% |
| 10,000 | 14.4% | 55.5% | 16.6% | 8.7% | 0.0% |

## 6. Authority and equivalence

| check | result |
|---|---|
| Full suite, exact CI path | 1199 passed |
| 202-question benchmark | 194/202, failures unchanged: 3 ranking, 2 abstention, 3 relationship |
| Scorer equivalence, canonical packs | 202/202 byte-identical |
| L2 destroy, pack digest | unchanged, 12/12 identical |
| L2 rebuild, pack digest | unchanged, 12/12 identical |
| L2 destroy incl. claim cache | 202 packs identical, `identical: True` |
| Model unavailable | 202/202 answered, 163 correct |
| Retrieval unavailable | 202/202 answered, 163 correct |
| Poisoning, 202 questions | 0 untrusted claims, 0 injection markers in claims or evidence |

The benchmark score is unchanged at 194/202. That is the correct outcome for a
latency fix: same answers, less time. No label was touched.

## 7. Packaging and distribution

Clean-room check in a fresh virtualenv with the project installed editable,
run from outside the repository:

```
memory_pack imported; heavy modules pulled in: none
status: no_relevant_memory | digest len: 64
root modules in wheel: ['mcp_server.py', 'memory_pack.py', 'mindpalace_sdk.py']
```

## 8. Files changed, and why

| file | reason |
|---|---|
| `api/services/claim_embeddings.py` | removed the two large array parameters from `load_cached`; the fix |
| `api/services/memory_relevance.py` | carried over `claim_terms` memoisation and the hoisted common-term difference, both unproven and labelled as such |
| `api/services/memory_public.py`, `memory_query.py` | `vectorized` switch plumbing, default off, kept for the equivalence harness |
| `scripts/benchmark/ensure_postgres.sh` | reproducible database restore; the container died three times this session and a missing database was producing partial artifacts that read as product behaviour |
| `scripts/benchmark/m0122_instrumented.py` | the single trustworthy measurement, with cache-hit assertion and raw samples |
| `scripts/benchmark/m0122_plan_cliff.py` | plan comparison; retained because the negative result is the evidence that made the cause visible |

## 9. Rejected, with the measurement that rejected them

| candidate | result |
|---|---|
| Vectorised relevance scorer | 1.00x to 1.07x end to end. Rejected, off by default. |
| Hoisting the common-term set difference | no measurable end-to-end effect. Kept, not claimed. |
| Memoising claim tokenisation | no measurable end-to-end effect. Kept, not claimed. |
| Current-version pointer (M012.1) | 3.6 ms of a 20,894 ms query. Rejected. |
| Set-based archive load (M011) | removed O(n²) but a plan cliff made it 12x worse. Rejected. |
| Candidate narrowing (M012) | 170/202 equivalent. Rejected. |

## 10. Known issues and limitations

1. **`project` is now the dominant term from 5,000 claims upward**, at 47% and
   55% of warm time. It materialises a Pydantic model for every claim in the
   archive and then discards all but a handful. M012 established that narrowing
   it naively changes 32 of 202 answers, so this needs the dependency closure
   solved first, not another cache.
2. `archive_load` is 80% at 1,000 claims but 14% at 10,000, which is not a
   shape I can explain. Both are stable within a size and reproduce across runs,
   but the non-monotonicity is unexplained.
3. 25,000 and above were not measured.
4. The two cleanups in section 9 are retained on the grounds that they are
   strictly less work, not because they were shown to help.
5. Relationship questions remain 3/34 on the authored benchmark. Unchanged and
   unaddressed.

## 11. Release decision

**READY WITH DOCUMENTED LIMITATIONS.**

Justified by: a real production defect found and fixed, measured at 5.83x on
5,000 claims and neutral at 100, with every authoritative invariant intact,
202/202 pack byte-identity, 1199 tests on the exact CI path, a reproducible
database setup, and a clean-install check confirming the reader still depends on
nothing.

Not claimed: speed at 25,000 claims or above, any improvement to the 194/202
answer set, or resolution of the relationship failures. The remaining `project`
cost is documented above with the reason it has not been attacked.

Blocking items for a later release: none that affect correctness. The two
performance items above are open, sized, and reproducible.
