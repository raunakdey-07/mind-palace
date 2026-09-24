# M009 release readiness

**Decision: M009: RELEASE READY**

Release target: `v0.6.0`. This document is included in the release commit;
Git history records the final commit, tag, and published release. Validation
used a disposable PostgreSQL database created by
`scripts/m009_release_validation.py`; the configured archive was not modified.

## Gate evidence

| Gate | Evidence | Status |
|---|---|---|
| keyset feed | `api/services/memory_feed.py`; `tests/test_m009_feed_integration.py` | PASS |
| signed cursor | `tests/test_m009_feed.py`; `tests/test_m009_adapters.py` | PASS |
| corpus binding | integration tests; live wrong-corpus check | PASS |
| REST | `docs/m009/live-interface-validation.json` | PASS |
| SDK | `docs/m009/live-interface-validation.json` | PASS |
| CLI | `docs/m009/live-interface-validation.json`; `docs/m009/cli-validation.txt` | PASS |
| REST/SDK/CLI equivalence | live artifact: identical sequence digest across all three interfaces | PASS |
| concurrency | `docs/m009/concurrency-validation.json`; `tests/test_m009_feed_integration.py` | PASS |
| health live | `tests/test_m009_health.py` | PASS |
| health ready | `tests/test_m009_health.py`; schema/timeout/failure cases | PASS |
| scale 100 | `docs/m009/scale-benchmark.json` | PASS |
| scale 1k | `docs/m009/scale-benchmark.json` | PASS |
| scale 10k | `docs/m009/scale-benchmark.json` | PASS |
| p50/p95 | 20 measured iterations after two warm-ups; artifact includes samples | PASS |
| query plans | `docs/m009/query-plans.md`; real 10k `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` | PASS |
| security | cursor/config/error tests; redacted artifacts; no cursor/secret logging | PASS |
| MCP decision | `docs/operations.md`; `README.md`; `examples/MEMORY_API.md` | PASS |
| documentation | operations/API/README/changelog/release-map audit | PASS |
| full regression | `docs/m009/full-regression.txt` | PASS |
| static validation | `docs/m009/static-validation.txt` | PASS |
| diagnostics classification | `docs/m009/diagnostics-inventory.md` | PASS |

Every PASS above points to a repository artifact or an executed command; no
PASS is based only on implementation presence.

## Live REST / SDK / CLI equivalence

The runner started the real `api.main:app` through Uvicorn on a disposable
PostgreSQL database, then used real HTTP requests, the public
`MindPalace(base_url=...)` SDK, and the installed `venvmp/bin/mindpalace memory
feed` command. The fixture contained 12 target versions at page size 2.

All three traversals returned 12 items in 6 pages, with the same logical
sequence digest:

```text
cb5be2e84861bbcc23eded7ad00f09765060b56cfa66a2532c71155679bac624
```

Invalid and wrong-corpus cursor responses were `422` with stable
`invalid_cursor` and `cursor_corpus_mismatch` codes. Cursor values and signing
material are redacted from the artifact.

## Concurrency semantics

The isolated test started with 30 versions, read page 1 at size 5, then inserted
four target-corpus rows before requesting the continuation: two rows tied at
the boundary timestamp, one later row, and one deliberately late row whose
observation key was before the cursor. Four rows in another corpus were also
inserted.

The observed guarantee is **live keyset continuation under successive MVCC
reads**:

- continuation returned 28 rows, exactly the rows strictly after the boundary;
- the two equal-timestamp rows were ordered by `version_id`;
- the later-timestamp row was included;
- the late-before-cursor row was excluded from continuation but present in a
  fresh traversal;
- no duplicate IDs, no cross-corpus rows, and strict ordering were observed;
- a fresh traversal returned all 34 target-corpus versions.

This is not a global snapshot or lossless late-arrival guarantee. See
`docs/operations.md` for the precise limitation.

## Scale and performance matrix

The benchmark used exact isolated relational fixtures of 100, 1,000, and
10,000 `memory_versions` rows, page size 50, two warm-up traversals, and 20
measured iterations. The service path, keyset query, cursor handling, and
PostgreSQL connection/session overhead were exercised; generated database
contents were not committed.

Wall-clock p50/p95 in milliseconds:

| Versions | First p50/p95 | Middle p50/p95 | Tail p50/p95 | Full p50/p95 | Cold first full traversal |
|---:|---:|---:|---:|---:|---:|
| 100 | 37.944 / 38.724 | 37.708 / 39.752 | 37.390 / 39.072 | 42.216 / 43.937 | 43.896 ms |
| 1,000 | 39.691 / 42.443 | 42.903 / 44.767 | 45.079 / 49.511 | 198.554 / 206.126 | 200.718 ms |
| 10,000 | 55.183 / 56.767 | 65.896 / 68.028 | 38.026 / 40.030 | 808.565 / 834.543 | 812.645 ms |

SQL execution p50/p95 for the full traversal was 6.938/7.834 ms (100),
133.588/138.689 ms (1,000), and 502.867/516.644 ms (10,000). Individual samples,
row counts, and all first/middle/tail SQL timings are in
`docs/m009/scale-benchmark.json`.

The cold sample is deliberately recorded separately and is sensitive to
PostgreSQL/OS cache and fixture setup; these numbers are evidence for this run,
not a production SLA or a statistical guarantee across hardware.

## Query-plan validation

For the 10,000-version fixture, real `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`
plans were captured for first, middle, and tail queries. The plans show:

- no `OFFSET` in the SQL or plan;
- the tuple keyset predicate for middle and tail queries;
- `idx_memory_versions_observed` used by the `memory_versions` access path;
- the expected primary-key lookup for `memory_documents`;
- no accidental full-history Python load (the feed executes one bounded query).

The observed execution times in the captured plans were approximately 0.519 ms
(first), 0.554 ms (middle), and 0.633 ms (tail) after fixture analysis. Full
JSON plans and buffer data are in `docs/m009/query-plans.md`.

## Performance-improvement decision

No speculative optimization was retained. The benchmark and plans show the
canonical query already selects feed-required columns, uses the composite
observed-at keyset index, and avoids full-history loading. The cold-start
sample is cache/setup-sensitive, while warm full-traversal p50/p95 is stable in
the recorded run. Adding a cache, new index, broker, or alternate database
would not be justified by this evidence. Any future optimization must record a
baseline, change, and post-change benchmark.

## Health, security, and MCP

- Liveness is process-only and remains available when PostgreSQL startup fails.
- Readiness returns `200` only with PostgreSQL connectivity and the required
  archive relations; refusal, timeout, and missing schema return sanitized
  `503` responses.
- Cursor signing requires a configured secret of at least 32 bytes; there is no
  predictable release fallback. Cursors are not logged, and artifacts contain
  only redacted cursor metadata.
- Feed errors expose stable codes/messages without SQL, credentials, tracebacks,
  filesystem paths, or connection strings.
- MCP exclusion is deliberate: the feed's cursor/page/operational contract is
  exposed through REST, SDK, and CLI, while existing MCP memory tools retain
  their `MemoryRequest`/`MemoryResponse` contract.

## Full validation record

`docs/m009/full-regression.txt` records:

```text
965 passed, 147 skipped, 10 warnings
```

The skips are environment-gated tests; they are not counted as passes. The
M009 integration tests were run with PostgreSQL enabled and passed. The host's
unqualified `pytest` launcher is outside the project environment and fails at
collection with missing dependencies; the repository runtime command
`venvmp/bin/python -m pytest -q` is the authoritative run and is the one
recorded above. The exact CI lint scope (`api cli tests`) passes Black, Flake8, and
`git diff --check`; the separate repository-wide command that included the
pre-existing `scripts/benchmark/m00675.py` was not allowed to modify that
research-boundary file. CLI help and public SDK import are recorded in
`docs/m009/cli-validation.txt`.

## Known limitations

- No exactly-once, broker, CDC, Kafka, or transactional-delivery guarantee.
- No globally consistent snapshot across independent page requests.
- Late commits at or before an issued keyset boundary require reconciliation.
- The final page has no implicit continuation cursor; polling clients must
  restart and deduplicate or maintain a separate boundary policy.
- No built-in authentication or per-corpus authorization.
- Archive retention/erasure and cursor expiry beyond signing-key/archive
  availability remain outside M009.
- MCP feed support is intentionally deferred as a separate product decision.

**No commit/push/tag performed.**
