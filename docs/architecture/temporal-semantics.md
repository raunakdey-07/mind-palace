# Temporal semantics

This document covers the two clocks Mind Palace keeps separate, how selectors
resolve them, and what the M009 feed does and does not promise about ordering.
Evidence: `api/services/memory.py`, `api/services/memory_public.py`,
`api/services/memory_query.py`, `api/services/memory_feed.py`, and
[`docs/operations.md`](../operations.md).

## Two clocks

`observed_at` is when the archive recorded a version. It is
`clock_timestamp()` taken inside the corpus writer lock at insert time, so it
records the service's observation, not a date the author wrote down.

`valid_from` and `valid_until` are authored validity bounds on a claim. Both are
nullable and a null bound means unknown. They are never derived from ingestion
time. The window is half-open, `[valid_from, valid_until)`, and
`004_memory.py` enforces `valid_until >= valid_from` when both are present.

The two clocks answer different questions. `observed_at` answers "when did we
see this", and `valid_from`/`valid_until` answer "when did the author say this
was true". A document that says a fact is true from 2020 and is edited in 2026
produces a 2026 `observed_at` with a 2020 `valid_from`, and both are correct.

## Selectors

| Selector | Meaning |
|---|---|
| `as_of` | inclusive observation cutoff, `observed_at <= as_of` |
| `valid_at` | the instant used to evaluate authored validity |
| `snapshot_id` | replaces both; fixed to the saved `as_of` |

`as_of` is inclusive and filters on observation time only. Request timestamps
must be timezone-aware; naive datetimes are rejected by
`MemoryRequest.as_of` (`AwareDatetime`).

`valid_at` is fixed once per response in
`memory_public.execute_in_session`:

```python
valid_at = cutoff if saved else request.valid_at or cutoff or datetime.now(timezone.utc)
```

Fixing it once matters because conflict detection compares every pair of active
claims against the same instant. Letting the clock advance mid-response would
make conflict membership depend on how long the query took.

Snapshot replay sets `cutoff = saved["as_of"]` and then uses that same value for
validity, so replay evaluates the state as the snapshot saw it rather than as of
replay time. `snapshot_id` cannot be combined with `as_of` or `valid_at`; that
combination is rejected as `422 invalid_request` because it would be ambiguous.
`snapshot.observed_at` is capture time and is not a second validity selector.

A snapshot cutoff later than the database clock is rejected, since a snapshot
cannot contain changes that have not been observed.

## Status from the clocks

In `memory._query_result`, for each claim at a fixed `at`:

```python
claim["status"] = (
    "SUPERSEDED" if claim["id"] in superseded_ids
    else "UNCERTAIN" if claim["version_id"] not in current_ids
        or (start is not None and at < start)
        or (end is not None and at >= end)
    else "CURRENT"
)
```

`current_ids` is the set of latest non-deleted version identifiers per document
at the cutoff. So a claim is `CURRENT` only when all three hold: it is in the
latest active version of its document, it is not superseded, and the validity
window contains `at`. Out-of-window and unreplaced-but-not-current both land in
`UNCERTAIN`, and the historical and uncertain lists overlap by design.

An explicit `supersedes_id` from any claim in the same resolution marks a claim
`SUPERSEDED` regardless of the window, so an explicit replacement is visible
even when the authored window has since closed.

## Question interpretation

`memory_query.interpret` is a keyword heuristic over the question, not temporal
reasoning. Explicit `as_of` or `snapshot_id` wins. Failing that, exactly one
`YYYY-MM-DD` in the question becomes a cutoff at midnight UTC, inclusive. Two
dates are `422 invalid_timestamp`. A `stage <name>` phrase is
`422 invalid_timestamp` unless the caller supplies its own `as_of` or
`snapshot_id`, because Mind Palace has no stage calendar and will not invent one.

Relative phrases such as "before Kafka" select history but never resolve a
before-entity event boundary. The documented position is that a caller who needs
a precise historical state supplies a captured timestamp or snapshot.

`temporal` intent requires a cutoff or snapshot; without one it is rejected
rather than answered from the live state.

Relevance never overrides the clocks. `memory_query.select` projects the
complete time-scoped archive first, then ranks, and a relevance score can only
include or exclude an authored key. It cannot promote an old claim to `CURRENT`.

## Research-only temporal primitives

`api/services/memory_reasoning.py` defines `TemporalState`, `temporal_state_at`,
`snapshot_diff`, and `conflict_lifecycle`. `temporal_state_at` classifies using
explicit fields and returns `HISTORICAL` when the query time is at or past
`valid_until`, before `valid_from`, or before `observed_at`. These types back
the M007 and M008 research programs and are excluded from the API image. They
are not part of the released query path and are not a public contract.

## Feed ordering

The M009 feed orders rows by:

```text
(observed_at ASC, version_id ASC)
```

`version_id` is a deterministic tie-break, so equal timestamps still have one
total order. The query is a parameterized keyset predicate:

```sql
(v.observed_at, v.id) > (:observed_at, :version_id)
```

It requests `page_size + 1` rows, trims the extra one after computing
`has_more`, and never uses `OFFSET`, Python-side history loading, or a
process-local cursor store. Valid page sizes are 1 through 500, default 50.
Captured 10,000-row `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans in
[`docs/m009/query-plans.md`](../m009/query-plans.md) show no `OFFSET`, the tuple
keyset predicate on middle and tail pages, and `idx_memory_versions_observed` on
the `memory_versions` access path.

## Feed consistency, measured

Each HTTP request is one PostgreSQL statement under MVCC. Successive requests
can see successive committed states; no snapshot is held across requests.

The recorded concurrency artifact
([`docs/m009/concurrency-validation.json`](../m009/concurrency-validation.json))
started with 30 versions, read page 1 at size 5, then inserted four
target-corpus rows including two tied at the boundary timestamp, one later row,
and one late row whose observation key preceded the cursor, plus four rows in
another corpus. The observed result:

- continuation returned exactly the 28 rows strictly after the boundary;
- the two equal-timestamp rows were ordered by `version_id`;
- the later-timestamp row was included;
- the late-before-cursor row was excluded from continuation but present in a
  fresh traversal;
- a fresh traversal returned all 34 target-corpus versions;
- no duplicate identifiers, no cross-corpus rows, and strict ordering held.

This is live keyset continuation under successive MVCC reads. It is not a global
snapshot and not a lossless late-arrival guarantee. A row committed after a
cursor was issued whose observation key is at or before that boundary is not
returned by that continuation, so clients needing completeness run periodic
full reconciliation.

The M009 feed does not provide exactly-once delivery, broker, CDC, or
transactional-delivery semantics, and its final page has no implicit
continuation cursor, so a polling client restarts and deduplicates or keeps its
own boundary policy.

## Two limits worth restating

Newer is not valid. A version observed today can carry a `valid_until` that
already passed, in which case it reads as `UNCERTAIN` rather than `CURRENT`.

A snapshot fixes a reference set, not the world. Replay answers from the
versions it captured. Anything observed after the cutoff is absent by
construction, which is the point, and is also why a replay cannot answer a
question about later state.
