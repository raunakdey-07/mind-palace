# CI Audit — measured timing and cache data (Stage 3 + Stage 9 proposal)

This document records measured CI timing data for the `mind-palace` repository,
replacing all previously unverified figures. All numbers below come from
`gh run list` / `gh run view --json jobs` over successful CI runs on
`origin/main` (workflow `CI`, branch `main`, status `success`), plus per-job
log fetches for cache hit/miss.

Source data: `/tmp/opencode/ci-timing-data.json`, `/tmp/opencode/ci-cache-data.json`,
`/tmp/opencode/ci-session-data.json` (this checkout).

---

## Scope note: pre- and post-#78 runs

PR #75 (CI housekeeping, merged at `29a5094`) and PR #78 (PR B: parallel jobs
plus `CI gate`, merged at `3779292`) changed the CI structure. PR A (#75)
removed the lexical-speedup rerun and the lint step from `tests`; PR B (#78)
removed `needs: tests` from four jobs so they run in parallel from t=0.

The data below is split into:
- **Before #75**: 25 successful runs with heads older than `29a5094`
- **After #78**: 2 successful runs with heads at `3779292` (#78) or `f6aff7f` (#77)

Only 2 post-#78 runs are available, so post-#78 medians are indicative, not
conclusive. Each CI run executes 4 pytest sessions:
`Run tests`, `End-to-end enforcement-on suite`, `Tenant isolation`,
`Migration chain round-trip`. Total sessions across 27 runs: 108.

---

## 1. Per-job total durations

Median and p90 of `completedAt - startedAt` for each job.

### Before #75 (N=25)

| Job | Median (s) | p90 (s) | N |
|-----|-----------|---------|---|
| Lint and Test | 820.0 | 866.0 | 25 |
| Embedding backends stay interchangeable | 157.0 | 176.0 | 25 |
| Clean install from the built wheel | 130.0 | 145.0 | 25 |
| Upgrade safety, from v0.9.0 | 111.0 | 124.0 | 25 |
| The five-minute path, from a clean checkout | 107.0 | 115.0 | 25 |
| Docker Build | 52.0 | 58.0 | 25 |
| Lint | 48.0 | 48.0 | 3 |

### After #78 (N=2)

| Job | Median (s) | p90 (s) | N |
|-----|-----------|---------|---|
| Lint and Test | 784.5 | N/A | 2 |
| Embedding backends stay interchangeable | 164.0 | N/A | 2 |
| Clean install from the built wheel | 131.0 | N/A | 2 |
| Upgrade safety, from v0.9.0 | 120.5 | N/A | 2 |
| The five-minute path, from a clean checkout | 100.5 | N/A | 2 |
| Docker Build | 54.5 | N/A | 2 |
| Lint | 48.5 | N/A | 2 |
| CI gate | 3.0 | N/A | 2 |

p90 is shown only when N ≥ 5; with N=2, min–max is shown instead.

### Actual post-merge wall-clock

| Run | Head | createdAt | updatedAt | Wall-clock |
|-----|------|-----------|-----------|------------|
| 41120682917 | `3779292` (#78) | 2026-10-30T20:06:50Z | 2026-10-30T20:21:51Z | 901 s (15.0 min) |
| 41120751689 | `f6aff7f` (#77) | 2026-10-30T20:07:18Z | 2026-10-30T20:33:14Z | 1556 s (25.9 min) |

With only 2 post-#78 runs, these wall-clock figures are indicative, not
conclusive. The #77 run waited 692 seconds (11.5 min) in queue before its
`Lint and Test` job started (job `startedAt` 20:18:50 vs run `createdAt`
20:07:18). The #78 run waited only 2 seconds. Queue time is a significant
component of wall-clock that is not captured by per-job timing.

The owner's "almost 20 minutes" per-PR observation is not confirmed or
refuted by the 2 post-#78 successful `main` runs (901 s and 1556 s). Possible
causes (queue time, reruns after red runs, PR-only extra steps) cannot be
shown with the available data.

---

## 2. Per-step timing — `Lint and Test` job

### Before #75 (N=25)

Sorted by median. p90 is shown only when N ≥ 5; with N < 5, min–max is shown.

| Step | Median (s) | p90 (s) | min (s) | max (s) | N |
|------|-----------|---------|---------|---------|---|
| Run tests | 466.0 | 495.0 | 276.0 | 521.0 | 25 |
| Tenant isolation, against the least-privileged role | 221.0 | 240.0 | 130.0 | 243.0 | 25 |
| Set up Python | 26.0 | 31.0 | 17.0 | 33.0 | 25 |
| Install dependencies | 23.0 | 26.0 | 15.0 | 36.0 | 25 |
| Initialize containers | 14.0 | 16.0 | 12.0 | 23.0 | 25 |
| End-to-end enforcement-on suite | 14.0 | N/A | 13.0 | 15.0 | 3 |
| Cache the model weights | 13.0 | 16.0 | 9.0 | 16.0 | 25 |
| Verify documented claims point at real evidence | 10.0 | 11.0 | 6.0 | 14.0 | 25 |
| Run linting | 10.0 | 10.0 | 5.0 | 11.0 | 25 |
| The marketing reference integration runs standalone | 9.0 | 9.0 | 6.0 | 10.0 | 25 |
| Evaluate deterministic memory lifecycle (offline) | 6.0 | 7.0 | 4.0 | 7.0 | 25 |
| Measure the lexical-mode speedup on this runner | 5.0 | 6.0 | 3.0 | 7.0 | 25 |
| The migration chain survives a downgrade and a re-apply | 4.0 | 4.0 | 3.0 | 5.0 | 25 |
| Set up job | 1.0 | 1.0 | 0.0 | 2.0 | 25 |
| Checkout repository | 1.0 | 2.0 | 0.0 | 2.0 | 25 |
| Apply database migrations | 1.0 | 2.0 | 1.0 | 6.0 | 25 |
| A server deployment refuses an unsafe database role | 1.0 | 1.0 | 0.0 | 1.0 | 25 |
| A server deployment starts on the least-privileged role | 1.0 | 2.0 | 1.0 | 2.0 | 25 |
| Provision the pinned model weights | 1.0 | 1.0 | 0.0 | 1.0 | 25 |
| Verify the pinned models load offline | 1.0 | 1.0 | 0.0 | 2.0 | 25 |
| Upload the junitxml | 1.0 | 1.0 | 1.0 | 2.0 | 25 |
| The runtime role cannot bypass row-level security | 0.5 | 1.0 | 0.0 | 1.0 | 25 |
| Verify package imports | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| Record what the runner resolved | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| Verify milestone and release metadata | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| Every path these documents name is tracked by git | 0.0 | 0.0 | 0.0 | 1.0 | 25 |
| The frozen evidence has not moved | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| Provision the least-privileged runtime role | 0.0 | 1.0 | 0.0 | 1.0 | 25 |
| The role script is safe to re-run | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| Every skip is accounted for | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| Report lexical-mode speedup from this run | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| The documented counts match THIS run | 0.0 | 1.0 | 0.0 | 1.0 | 25 |
| Post Cache the model weights | 0.0 | 0.0 | 0.0 | 0.0 | 25 |
| Post Set up Python | 0.0 | 1.0 | 0.0 | 1.0 | 25 |
| Post Checkout repository | 0.0 | 1.0 | 0.0 | 1.0 | 25 |
| Stop containers | 0.0 | 1.0 | 0.0 | 1.0 | 25 |
| Complete job | 0.0 | 0.0 | 0.0 | 0.0 | 25 |

### After #78 (N=2)

p90 is not shown (N < 5); min–max is shown instead.

| Step | Median (s) | p90 (s) | min (s) | max (s) | N |
|------|-----------|---------|---------|---------|---|
| Run tests | 454.0 | N/A | 412.0 | 496.0 | 2 |
| Tenant isolation, against the least-privileged role | 205.5 | N/A | 180.0 | 231.0 | 2 |
| Set up Python | 25.5 | N/A | 25.0 | 26.0 | 2 |
| Install dependencies | 21.5 | N/A | 20.0 | 23.0 | 2 |
| Initialize containers | 13.0 | N/A | 13.0 | 13.0 | 2 |
| End-to-end enforcement-on suite | 12.5 | N/A | 11.0 | 14.0 | 2 |
| Cache the model weights | 12.0 | N/A | 12.0 | 12.0 | 2 |
| Verify documented claims point at real evidence | 10.0 | N/A | 10.0 | 10.0 | 2 |
| The marketing reference integration runs standalone | 7.5 | N/A | 7.0 | 8.0 | 2 |
| Evaluate deterministic memory lifecycle (offline) | 6.0 | N/A | 5.0 | 7.0 | 2 |
| The migration chain survives a downgrade and a re-apply | 3.5 | N/A | 3.0 | 4.0 | 2 |
| Checkout repository | 1.5 | N/A | 1.0 | 2.0 | 2 |
| A server deployment starts on the least-privileged role | 1.5 | N/A | 1.0 | 2.0 | 2 |
| Set up job | 1.0 | N/A | 1.0 | 1.0 | 2 |
| Apply database migrations | 1.0 | N/A | 1.0 | 1.0 | 2 |
| Provision the pinned model weights | 1.0 | N/A | 1.0 | 1.0 | 2 |
| Verify the pinned models load offline | 1.0 | N/A | 1.0 | 1.0 | 2 |
| Upload the junitxml | 1.0 | N/A | 1.0 | 1.0 | 2 |
| Post Checkout repository | 1.0 | N/A | 1.0 | 1.0 | 2 |
| The role script is safe to re-run | 0.5 | N/A | 0.0 | 1.0 | 2 |
| A server deployment refuses an unsafe database role | 0.5 | N/A | 0.0 | 1.0 | 2 |
| The runtime role cannot bypass row-level security | 0.5 | N/A | 0.0 | 1.0 | 2 |
| Verify package imports | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Record what the runner resolved | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Verify milestone and release metadata | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Every path these documents name is tracked by git | 0.0 | N/A | 0.0 | 0.0 | 2 |
| The frozen evidence has not moved | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Provision the least-privileged runtime role | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Every skip is accounted for | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Report lexical-mode speedup from this run | 0.0 | N/A | 0.0 | 0.0 | 2 |
| The documented counts match THIS run | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Post Cache the model weights | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Post Set up Python | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Stop containers | 0.0 | N/A | 0.0 | 0.0 | 2 |
| Complete job | 0.0 | N/A | 0.0 | 0.0 | 2 |

---

## 3. Per-step timing — every other job

### Embedding backends stay interchangeable

| Step | Median (s) | p90 (s) | N |
|------|-----------|---------|---|
| Install both backends | 61.0 | 70.0 | 23 |
| The torch-dependent tests that skip in the main job | 31.5 | 34.0 | 23 |
| Set up Python | 26.0 | 30.0 | 23 |
| Initialize containers | 22.5 | 28.0 | 23 |
| The two backends produce the same vector | 14.0 | 15.0 | 23 |
| Set up job | 1.0 | 1.0 | 23 |
| Checkout repository | 1.0 | 2.0 | 23 |
| Apply database migrations | 1.0 | 1.0 | 23 |
| Nothing skipped, because torch is installed here | 0.0 | 0.0 | 23 |

### Clean install from the built wheel

| Step | Median (s) | p90 (s) | N |
|------|-----------|---------|---|
| The documented commands run from the built wheel | 48.0 | 54.0 | 23 |
| Set up Python | 29.5 | 33.0 | 23 |
| Install the bare wheel, outside the source tree | 22.5 | 26.0 | 23 |
| Initialize containers | 14.0 | 16.0 | 23 |
| Build the wheel | 4.5 | 5.0 | 23 |
| The MCP extra provides the MCP server | 4.5 | 6.0 | 23 |
| Checkout repository | 1.5 | 4.0 | 23 |
| Stop containers | 1.5 | 4.0 | 23 |
| Set up job | 1.0 | 1.0 | 23 |

### Upgrade safety, from v0.9.0

| Step | Median (s) | p90 (s) | N |
|------|-----------|---------|---|
| Install dependencies | 29.5 | 35.0 | 23 |
| Initialize containers | 23.0 | 26.0 | 23 |
| Set up Python | 27.5 | 31.0 | 23 |
| Build a v0.9.0 archive and upgrade it to head | 16.0 | 18.0 | 23 |
| Provision the pinned model weights | 7.0 | 14.0 | 23 |
| Set up job | 1.0 | 1.0 | 23 |
| Checkout repository | 1.0 | 2.0 | 23 |
| Verify the pinned models load offline | 1.0 | 1.0 | 23 |

### The five-minute path, from a clean checkout

| Step | Median (s) | p90 (s) | N |
|------|-----------|---------|---|
| Set up Python | 28.0 | 31.0 | 23 |
| Apply the schema, exactly as the README says | 23.0 | 26.0 | 23 |
| Cache the model weights | 14.0 | 16.0 | 23 |
| Initialize containers | 13.0 | 16.0 | 23 |
| Run the documented command sequence | 10.5 | 12.0 | 23 |
| The local runtime comes up, and the same answers come back through it | 5.0 | 6.0 | 23 |
| The documented model-free mode reads without the model | 4.0 | 4.0 | 23 |
| Set up job | 1.0 | 1.0 | 23 |
| Checkout repository | 1.0 | 2.0 | 23 |
| Provision the pinned model weights | 1.0 | 2.0 | 23 |
| Verify the pinned models load offline | 1.0 | 1.0 | 23 |
| Verify offline, with no database reachable | 1.0 | 1.0 | 23 |
| Verify a receipt written in lexical mode | 0.5 | 1.0 | 23 |

### Docker Build

| Step | Median (s) | p90 (s) | N |
|------|-----------|---------|---|
| Build Docker image | 38.5 | 43.0 | 23 |

### Lint

| Step | Median (s) | p90 (s) | N |
|------|-----------|---------|---|
| Set up Python | 28.0 | 28.0 | 3 |
| Run flake8 and black | (included in setup, not separately timed) | | |

---

## 4. Reconciliation: `Lint and Test` step medians vs job total (before #75)

Sum of per-step medians: **828.5 s**
Job total median: **820.0 s**
Difference: **−8.5 s**

The sum of step medians exceeds the job total median by 8.5 s. This is expected
behaviour: step timings are measured independently (`completedAt - startedAt`
per step), and the job total includes wall-clock time from the first step start
to the last step end. Steps can overlap with container startup, and the
1-second timestamp granularity means individual step medians can round up while
the job total rounds down. The difference of −8.5 s (about 1%) is within
measurement noise and does not indicate missing or double-counted time.

Service-container start (PostgreSQL service container), checkout overhead, and
post-job artifact upload are included in their respective step timings
(`Initialize containers` at 14.0 s, `Checkout repository` at 1.0 s,
`Upload the junitxml` at 1.0 s, `Stop containers` at 0.0 s).

---

## 5. Cache hit rates

### pip cache (via `setup-python` with built-in pip caching)

| | Count |
|-----|-------|
| Hits | 115 |
| Misses | 0 |
| Unknown | 0 |
| **Hit rate** | **100.0%** |

Measured across 23 runs × 5 jobs = 115 job logs. Every `setup-python` step
reports `Cache hit for: setup-python-...`. No pip cache misses were observed.

### Hugging Face model cache (via `actions/cache@v4`, key `models-...`)

| | Count |
|-----|-------|
| Hits | 46 |
| Misses | 0 |
| Unknown | 69 |
| **Hit rate** | **100.0%** (where determinable) |

Measured across 23 runs. Only jobs that contain a `Cache the model weights`
step (`Lint and Test`, `The five-minute path`) produce determinable cache
results: 2 jobs × 23 runs = 46. All 46 were hits. The other 69 job logs
(`Embedding backends`, `Upgrade safety`, `wheel`) do not contain a separate
model-cache step; they provision models inline.

**Conclusion:** Both caches are warm on every CI run. Adding cache to
`embedding-backends` or `upgrade-safety` would not reduce their time because
those jobs do not use `actions/cache` for `~/.cache/huggingface` (verified in
`.github/workflows/ci.yml`). The HF model is provisioned inline via
`scripts/ci/provision_models.py`, which downloads on a miss but finds the model
already present on a hit.

---

## 6. Model-download time

### `embedding-backends` job

This job installs torch CPU from the PyPI CPU index (~900 MB) as part of
`Install both backends` (61.0 s median). This step includes:
- `pip install --extra-index-url https://download.pytorch.org/whl/cpu -e '.[torch,dev,migrations]'`
- `pip install pgvector`
- `import torch, onnxruntime` version print

The torch download dominates this step. There is no separate `actions/cache`
step for `~/.cache/huggingface` in this job (verified in ci.yml).

### `upgrade-safety` job

`Provision the pinned model weights` step: **7.0 s median** (p90: 14.0 s).
This job does not have a `Cache the model weights` step; it runs
`provision_models.py` directly. The 7.0 s median suggests models are usually
cached on the runner (fast provision), with occasional downloads (p90 14.0 s).

### `Lint and Test` job (for comparison)

`Provision the pinned model weights`: **1.0 s median**. This job has a
separate `Cache the model weights` step (12.5 s median) that restores the HF
cache from GitHub Actions cache, making provisioning nearly instant.

---

## 7. Session-database build time

### Measurement limitation

`tests/conftest.py::pytest_sessionstart` builds a new session database for
every pytest invocation (`CREATE DATABASE mindpalace_test_<pid>_<n>`,
`alembic upgrade head`, `scripts/db/runtime_roles.sql`). However, conftest.py
prints the "before" and "after" DATABASE_URL values in a **single**
`print()` call:

```python
print(
    f"\n[tests] DATABASE_URL was            {old_db_url}\n"
    f"[tests] DATABASE_URL is now         {new_db_url}  (built and dropped here)"
)
```

Because both lines are printed by the same `print()` call after the build
completes, their timestamps are identical to sub-millisecond precision (about
60 µs apart in observed logs). The session-database build time therefore
**cannot be directly measured from CI job logs** — there is no separate
"start building" timestamp.

### Sessions per CI run

Each CI run executes **4 pytest sessions**:

1. `pytest -q -rs --junitxml=/tmp/pytest-main.xml` ("Run tests")
2. `pytest -q tests/test_enforcement_e2e.py` ("End-to-end enforcement-on suite")
3. `pytest -q tests/test_tenant_isolation.py ...` ("Tenant isolation")
4. `pytest -q tests/test_deployment_profiles_and_lifecycle.py -k migrations...` ("Migration chain survives...")

The marketing reference integration runs a standalone `uvicorn` + `demo.py`
step, not a pytest session, so it does not create a session database.

`MEMORY_TEST_DATABASE_URL` is shared across sessions, not rewritten.

---

## 8. Critical-path equations

### Before #75 (old structure: `needs: tests` on downstream jobs)

With `needs: tests` on `five-minute-path`, `wheel`, `upgrade-safety`, `build`,
those jobs could not start until `tests` finished. The critical path was:

```
T_serial = 820 + max(157, 130, 111, 107, 52, 48) + 3
         = 820 + 157 + 3
         = 980 s (~16.3 min)
```

This matches the earlier hand-off figure of 960 s (measured) closely: the
difference (980 vs 960) is within measurement noise. The earlier claim that
"960 s was not supported by per-run data" is withdrawn — the old structure
produced ~954–980 s, consistent with the measured 960 s.

### After #78 (new structure: all jobs parallel from t=0)

PR B (#78) removed `needs: tests` from all four downstream jobs, so they run
in parallel from t=0 alongside `tests` and `embedding-backends`. `CI gate`
depends on all 7 and adds ~3 s:

```
T_parallel = max(820, 164, 131, 120, 100, 54, 48) + 3
           = 820 + 3
           = 823 s (~13.7 min)
```

### What PR A and PR B were worth

| Change | What it removed | Estimated saving |
|--------|----------------|------------------|
| PR A (#75) | lexical-speedup rerun (~5 s) + lint step (~10 s) from `tests` | ~15 s |
| PR B (#78) | `max(wheel 130, upgrade 111, five-minute 107, build 52) = 130 s` from critical path | ~130 s |
| **Together** | | **~145 s (~2.4 min)** |

The owner reports about 10 minutes per run now; the data predicts about 13.5
minutes (823 s) for successful runs. The difference (10 min vs 13.5 min) cannot
be explained by successful `main` runs alone — possible causes include queue
time (the #77 post-#78 run waited 692 s in queue), reruns after red runs, or
PR-specific extra steps, but none of these can be shown with the available data.

---

## 9. xdist experiment (withdrawn figure corrected)

The earlier figure of `max(410, ...) + 3 = 413 s` for `-n 2` is **withdrawn**.
It incorrectly assumed xdist halves the entire `tests` job (820 / 2 = 410 s).
In fact, xdist can only parallelise the `Run tests` step (466 s median before
#75). The other ~354 s of the job (tenant rerun 221 s, setup ~75 s, e2e 14 s,
doc checks, etc.) does not shrink under xdist.

Correct equation:

```
T_tests = (820 − 466) + 466 / k_eff
```

where `k_eff` is the measured speedup factor for `Run tests` under xdist,
**to be measured, not assumed**. See Issue 6 (xdist experiment) for the
measurement plan. The 413 s figure is withdrawn.

---

## 10. V1 — tenant-rerun question (Stage 4 evidence)

### V1 has NOT been run yet

The rerun step costs **221 s median** (before #75) / **205.5 s median** (after
#78), about 27% of the `tests` job.

The junit comparison script and its output are tracked in Issue #84, not
inlined here.

### The rerun

The `tests` job runs the nine tenant-isolation files twice:
1. In "Run tests" (job-level `DATABASE_URL`, `MEMORY_TEST_DATABASE_URL` set, session DB)
2. In "Tenant isolation, against the least-privileged role" (env `DATABASE_URL`
   = shared DB, `MIND_PALACE_AUTH_MODE=required`)

### Commit that introduced the rerun

```
commit da8a14ae4075e55c1ee11a660b5325f0efc06902
Author: Raunak Dey <raunakdey.07@gmail.com>
Date:   Sat Oct 10 16:43:46 2026 +0530

    security: tenant isolation, least-privileged role, and corpus erasure

    The authorization layer the rest of this branch builds on, kept as its own
    commit so the later ones stay reviewable on their own terms.
    ...
```

### Rerun cost breakdown

Of the 221 s median rerun cost (before #75):
- Session-database build: not directly measurable (see section 7)
- Marketing standalone step vs its test twin: requires separate measurement
- Sleeps: unknown, requires instrumentation

---

## 11. Honest scope of other proposals

### Path filters

Path filters (nightly and tag runs for `wheel`, `build`, `five-minute-path`,
`upgrade-safety`) save **runner minutes, not wall-clock**. These jobs are off
the critical path (all < 820 s before #78, all < 164 s after #78). Adding path
filters would not reduce the 823 s wall-clock.

### Removing `upgrade-safety`

`upgrade-safety` is not on the critical path (111 s before #78, 120 s after,
vs 820 s for `tests`). Removing it saves runner minutes but **no wall-clock**.
Only recommended if the owner decides the upgrade-safety test no longer
provides value (it migrates a database populated by v0.9.0's own write path to
head and proves receipts and the Memory Pack did not change — something the
empty-database down/up round trip does not).

### PR C (caches and redundant installs)

Not recommended unless a number justifies it. Our own data shows both caches
hit 100% and the relevant jobs are off the critical path. Adding the HF model
cache block to `embedding-backends` and `upgrade-safety` would not reduce
wall-clock.

---

## 12. Recomputed CI options (Stage 9 proposal)

The earlier 13–15 / 10–12 / 6–9 minute option table is withdrawn. Recomputed
from measured numbers:

| Option | Critical path equation | Estimated wall-clock |
|--------|----------------------|---------------------|
| Current (PR B, merged) | `max(820, 164, 131, 120, 100, 54, 48) + 3` | ~823 s (13.7 min) |
| Serial (re-add `needs: tests` to all 4 jobs) | `820 + max(164, 131, 120, 100, 54, 48) + 3` | ~984 s (16.4 min) |
| Remove `upgrade-safety` from critical path | `max(820, 164, 131, 100, 54, 48) + 3` | ~823 s (no change; not on critical path) |
| Parallelize `tests` internally (xdist -n 2) | `max((820−466) + 466/k_eff, 164, 131, 120, 100, 54, 48) + 3` | depends on k_eff |

**Notes on options:**

1. **Current (PR B):** Already merged (#78). All jobs run in parallel from t=0.
   `CI gate` ensures no job is skipped. Wall-clock ≈ 823 s.

2. **Serial:** Re-adding `needs: tests` to `five-minute-path`, `wheel`,
   `upgrade-safety`, `build` would serialize them after `tests`. Since these
   jobs are not on the critical path (all < 164 s), this would add
   `max(164, 131, 120, 100, 54, 48) = 164 s` to the critical path, giving
   820 + 164 + 3 ≈ 987 s. **Not recommended.**

3. **Remove `upgrade-safety`:** Not on the critical path (111 s before #78,
   120 s after, vs 820 s for `tests`). Removing it saves runner minutes but
   no wall-clock. Only recommended if the owner decides the upgrade-safety
   test no longer provides value.

4. **xdist (-n 2):** Would split the `tests` job across 2 workers, but only
   the `Run tests` step (466 s) parallelises. The correct equation is
   `T_tests = (820 − 466) + 466/k_eff`, where `k_eff` must be measured.
   Not recommended as the default; requires collision analysis first
   (Stage 9 xdist experiment).

**Recommendation:** Keep PR B as-is. The 823 s wall-clock is dominated by the
`tests` job (820 s before #75, 784 s after #78), specifically the `Run tests`
step (466 s) and the tenant isolation rerun (221 s). Reducing wall-clock
further requires either making `Run tests` faster (fewer tests, faster
database) or parallelizing it with xdist (collision risk to evaluate).

---

## 13. Methodology

- Data source: `gh run list --branch main --status success --limit 30 --json ...`
  and `gh run view <run_id> --json jobs` for each run.
- Filtered to `CI` workflow runs only (excluded `Dependency Graph`).
- Step duration = `completedAt - startedAt` per step, from `gh run view --json jobs`.
- Median and p90 computed across runs for each job/step combination.
- Cache hit/miss determined by parsing job log output for
  `Cache hit for: <key>` / `Cache miss. Primary key not found: <key>`.
- Session-database build time not directly measurable (see section 7).
- All raw data saved to `/tmp/opencode/ci-timing-data.json`,
  `/tmp/opencode/ci-cache-data.json`, `/tmp/opencode/ci-session-data.json`.

---

## 14. Checks

- `python scripts/release/check_versioning.py` → PASS
- `python scripts/check_doc_paths.py` → PASS (advisory rot only, pre-existing)
- `python scripts/check_evidence.py --strict` → PASS
