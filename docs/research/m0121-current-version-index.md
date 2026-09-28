# M012.1: current-version index experiment

## Decision: REJECT

A per-document current-version pointer does not pay for itself. It was measured
against the actual cost and removed from consideration before any production
code was written.

## Hypothesis

The currency rule is a max over `version_number` per document. The closure
experiment showed the `deep` corpus shape, where one document has N versions,
had a closure that grew with N. A persisted pointer was proposed to collapse
that to O(1).

## Current implementation, traced before changing anything

`api/services/memory.py`, `_query_result`:

```python
latest = {}
for version in versions:
    identity = version["memory_document_id"]
    if identity not in latest or latest[identity]["version_number"] < version["version_number"]:
        latest[identity] = version
current_ids = {v["id"] for v in latest.values() if v["event"] != "DELETED"}
```

Answering the questions this raises:

1. **What defines current?** The version with the greatest `version_number` for
   that `memory_document_id`, among the versions the load returned.
2. **Is it max version number?** Yes, not `observed_at`. Ordering ties on
   `version_number` cannot occur, it is unique per document.
3. **Is it status-based?** No. Status is derived *after* this line, from
   `current_ids` and validity bounds.
4. **Are deletions tombstones?** Yes. A tombstone is itself a version with
   `event = 'DELETED'` and the highest `version_number`, which is exactly how a
   document becomes non-current.
5. **Can restoration make an older version current?** No. Restoration appends
   a new version, so the pointer moves forward. No version is ever made current
   by rewinding.
6. **Equal timestamps?** The rule keys on `version_number`, not time, so
   equal `observed_at` is irrelevant to currency.
7. **Concurrent writes?** The corpus advisory lock in `lock_corpus` serialises
   writers per corpus, and `version_number` is assigned under that lock.
8. **What owns the transition?** The same transaction that inserts the version,
   via `record_version` and `record_deletion`.
9. **Rebuildable?** Yes, from `memory_versions` alone, with a single aggregate.

So the pointer was well-founded. It was also, as it turns out, worthless.

## Baseline

Green on the real CI path, against a database restored from scratch:

- `e2a52ce`, suite **1199 passed**
- 202-question benchmark **194/202**, failures 3 ranking, 2 abstention, 3 relationship

## Measurement

`scripts/benchmark/m0121_scan_cost.py`, on synthetic version lists so the
arithmetic is separated from host noise. Currency computed by the shipped scan
and by a pointer, asserted to give the same answer at every size.

| shape | n | scan p50 | pointer p50 | saved |
|---|---:|---:|---:|---:|
| deep | 100 | 0.0258 ms | 0.0009 ms | 0.0249 ms |
| deep | 1,000 | 0.2586 ms | 0.0009 ms | 0.2577 ms |
| deep | 10,000 | **3.6058 ms** | 0.0027 ms | 3.6031 ms |
| wide | 10,000 | 5.7640 ms | 2.9240 ms | 2.8400 ms |
| mixed | 10,000 | 5.2220 ms | 0.4040 ms | 4.8180 ms |

The pointer is genuinely faster, by three orders of magnitude on the deep shape,
and it is correct: identical result at every size.

It is also irrelevant. The measured warm query at 10,000 claims is **20,894 ms**.
The scan it would replace is **3.6 ms**, which is **0.017%** of the query. The
`deep` closure is O(n), but the O(n) is in *materialising* n historical claims,
not in *finding* the current one, and a pointer does not change how many claims
get built.

## Where the time actually is

Splitting the `resolve` stage at 10,000 claims, real path, warm, one question
(`What is the current database?`):

| stage | ms |
|---|---:|
| project | 5,521.7 |
| **relevance gate** | **110,930.7** |
| select (gate + selection) | 111,782.6 |
| bounded_pack | 0.5 |

The relevance gate is about 95% of the query, roughly 20x the projection and
five orders of magnitude more than packing. The scan the pointer would have
removed is not in this table's top three terms.

`relevance` scores every claim in the archive against every topic, and the inner
product is a pure-Python generator expression:

```python
return sum(a * b for a, b in zip(topic_vector, cached[ids[position]]))
```

At 10,000 claims and 384 dimensions that is 3.84 million interpreted
multiply-adds per topic, and it is the whole cost.

## Why the closure experiment pointed the wrong way

It was correct about the closure and wrong about the cost. The closure measures
*how much authoritative state an answer depends on*, which is a correctness
question, and it is genuinely O(n) for deep and hotkey shapes. But a resolution
path that must touch n claims pays for n claims whichever order it discovers
them in. Narrowing the discovery does not narrow the work unless the work is
skipped, and skipping it changes the answer, which the equivalence harness
already caught at 170/202.

## What was not built

No migration, no table, no service, no rebuild command. The change was rejected
before it reached production code, which is the intended outcome of an
experiment that fails.

## The actual next blocker

The relevance gate's arithmetic, not the archive load, not the pointer, and not
the projection.

An earlier measurement exists for the obvious remedy: the identical computation
through numpy runs 2.7x faster at 1,000 claims and 12.2x at 5,000, with a maximum
absolute difference of 1.6e-16. That is fast enough to matter and small enough to
be dangerous, because a score one ULP from a threshold can flip a key in or out of
an answer.

So the next experiment is not "vectorise it". It is: vectorise it, then prove
that all 202 benchmark packs are byte-identical, and characterise the cases where
they are not. If a threshold tie ever flips, the gate has to be defined so that
ties resolve by a deterministic rule rather than by floating-point accident, and
that rule is an authority decision, not a performance one.

## Limitations

- The scan-cost table is synthetic and arithmetic-only. It is the right tool for
  "is this term material", and the answer is a factor of ~5,000, so the host
  noise does not change the conclusion.
- The stage split is a single warm question on one host with known run-to-run
  variance of about 2x. The ratio, not the absolute milliseconds, is the finding.
- 25,000 claims and above were not measured in this milestone.
