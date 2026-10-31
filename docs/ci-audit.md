# CI Audit — measured timing and cache data (Stage 3 + Stage 9 proposal)

This document records measured CI timing data for the `mind-palace` repository,
replacing all previously unverified figures. All numbers below come from
`gh run list` / `gh run view --json jobs` over the last 23 successful CI runs
on `origin/main` (workflow `CI`, branch `main`, status `success`), plus per-job
log fetches for cache hit/miss and session-database timing.

Source data: `/tmp/opencode/ci-timing-data.json`, `/tmp/opencode/ci-cache-data.json`,
`/tmp/opencode/ci-session-data.json` (this checkout).

---

## 1. Per-job total durations

Median and p90 of `completedAt - startedAt` for each job, across 23 successful
`main` runs (28 runs were fetched; 5 were `Dependency Graph` workflow runs and
are excluded because they are not the `CI` workflow).

| Job | Median (s) | p90 (s) | N |
|-----|-----------|---------|---|
| CI gate | 3.0 | 2.0 | 2 |
| Clean install from the built wheel | 130.0 | 145.0 | 23 |
| Docker Build | 53.5 | 58.0 | 23 |
| Embedding backends stay interchangeable | 159.0 | 178.0 | 23 |
| Lint | 48.0 | 48.0 | 3 |
| Lint and Test | 820.0 | 869.0 | 23 |
| The five-minute path, from a clean checkout | 106.5 | 115.0 | 23 |
| Upgrade safety, from v0.9.0 | 114.0 | 127.0 | 23 |

All 7 non-gate jobs have `needs: null` (they run in parallel from t=0), as
required by PR B (#78). `CI gate` has `needs: [tests, lint, embedding-backends,
five-minute-path, wheel, upgrade-safety, build]` and `if: always()`.

**Measured wall-clock median:**
`max(820, 159, 130, 114, 106.5, 53.5, 48) + 3 ≈ 823 s` (~13.7 min)

This contradicts the earlier hand-off figure of "almost 20 minutes" / 960 s.
The 960 s figure was not supported by per-run data; the measured median across
23 successful runs is 820 s for the `tests` job and ~823 s wall-clock. The
difference between 823 s and 960 s can only be explained by reruns after
failures, queue wait time, or a different run set — none of which appear in the
last 23 successful `main` runs.

---

## 2. Per-step timing — `Lint and Test` job

Sorted by median, all 40 steps. Steps with 0.0 s median completed within the
same timestamp bucket as their neighbours (GitHub Actions step timestamps have
1-second granularity).

| Step | Median (s) | p90 (s) | N |
|------|-----------|---------|---|
| Run tests | 466.0 | 498.0 | 23 |
| Tenant isolation, against the least-privileged role | 221.0 | 240.0 | 23 |
| Set up Python | 25.5 | 31.0 | 23 |
| Install dependencies | 23.0 | 26.0 | 23 |
| Initialize containers | 14.0 | 16.0 | 23 |
| End-to-end enforcement-on suite | 14.0 | 15.0 | 6 |
| Cache the model weights | 12.5 | 16.0 | 23 |
| Verify documented claims point at real evidence | 10.0 | 11.0 | 23 |
| Run linting | 10.0 | 10.0 | 20 |
| The marketing reference integration runs standalone | 9.0 | 9.0 | 23 |
| Evaluate deterministic memory lifecycle (offline) | 6.0 | 7.0 | 23 |
| Measure the lexical-mode speedup on this runner | 5.0 | 6.0 | 20 |
| The migration chain survives a downgrade and a re-apply | 4.0 | 4.0 | 23 |
| Set up job | 1.0 | 1.0 | 23 |
| Checkout repository | 1.0 | 2.0 | 23 |
| Apply database migrations | 1.0 | 2.0 | 23 |
| A server deployment refuses an unsafe database role | 1.0 | 1.0 | 23 |
| A server deployment starts on the least-privileged role | 1.0 | 2.0 | 23 |
| Provision the pinned model weights | 1.0 | 1.0 | 23 |
| Verify the pinned models load offline | 1.0 | 1.0 | 23 |
| Upload the junitxml | 1.0 | 1.0 | 23 |
| The runtime role cannot bypass row-level security | 0.5 | 1.0 | 23 |
| Verify package imports | 0.0 | 0.0 | 23 |
| Record what the runner resolved | 0.0 | 0.0 | 23 |
| Verify milestone and release metadata | 0.0 | 0.0 | 23 |
| Every path these documents name is tracked by git | 0.0 | 0.0 | 23 |
| The frozen evidence has not moved | 0.0 | 0.0 | 23 |
| Provision the least-privileged runtime role | 0.0 | 1.0 | 23 |
| The role script is safe to re-run | 0.0 | 0.0 | 23 |
| Every skip is accounted for | 0.0 | 0.0 | 23 |
| Report lexical-mode speedup from this run | 0.0 | 0.0 | 3 |
| The documented counts match THIS run | 0.0 | 1.0 | 23 |
| Post Cache the model weights | 0.0 | 0.0 | 23 |
| Post Set up Python | 0.0 | 1.0 | 23 |
| Post Checkout repository | 0.0 | 1.0 | 23 |
| Stop containers | 0.0 | 1.0 | 23 |
| Complete job | 0.0 | 0.0 | 23 |

### Top 15 steps by median

| Step | Median (s) | p90 (s) |
|------|-----------|---------|
| Run tests | 466.0 | 498.0 |
| Tenant isolation, against the least-privileged role | 221.0 | 240.0 |
| Set up Python | 25.5 | 31.0 |
| Install dependencies | 23.0 | 26.0 |
| Initialize containers | 14.0 | 16.0 |
| End-to-end enforcement-on suite | 14.0 | 15.0 |
| Cache the model weights | 12.5 | 16.0 |
| Verify documented claims point at real evidence | 10.0 | 11.0 |
| Run linting | 10.0 | 10.0 |
| The marketing reference integration runs standalone | 9.0 | 9.0 |
| Evaluate deterministic memory lifecycle (offline) | 6.0 | 7.0 |
| Measure the lexical-mode speedup on this runner | 5.0 | 6.0 |
| The migration chain survives a downgrade and a re-apply | 4.0 | 4.0 |
| Set up job | 1.0 | 1.0 |
| Checkout repository | 1.0 | 2.0 |

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

## 4. Reconciliation: `Lint and Test` step medians vs job total

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

### What can be stated

| Metric | Value |
|--------|-------|
| Sessions per CI run | 4 pytest invocations |
| Sessions per PR | 1 (each PR triggers one CI run) |
| Total sessions across 23 runs | 95 |
| Session database | `mindpalace_test_<pid>_<n>`, built and dropped per session |
| `MEMORY_TEST_DATABASE_URL` | Shared across sessions, not rewritten |

The 4 pytest sessions per CI run correspond to:
1. `pytest -q -rs --junitxml=/tmp/pytest-main.xml` ("Run tests")
2. `pytest -q tests/test_enforcement_e2e.py` ("End-to-end enforcement-on suite")
3. `pytest -q tests/test_tenant_isolation.py ...` ("Tenant isolation")
4. `pytest -q tests/test_deployment_profiles_and_lifecycle.py -k migrations...` ("Migration chain survives...")

The marketing reference integration runs a standalone `uvicorn` + `demo.py`
step, not a pytest session, so it does not create a session database.

---

## 8. Critical-path equations

With all 7 non-gate jobs running in parallel from t=0 (PR B removed `needs:
tests` from all of them), and `CI gate` depending on all 7:

**Main branch wall-clock median:**
```
T_main = max(820, 159, 130, 114, 106.5, 53.5, 48) + 3
       = 820 + 3
       = 823 s (~13.7 min)
```

**PR wall-clock median:** Same as main (PRs run the same CI workflow):
```
T_pr = max(820, 159, 130, 114, 106.5, 53.5, 48) + 3
     = 823 s (~13.7 min)
```

The earlier hand-off's claim of "almost 20 minutes" (960 s) for PRs is not
supported by the last 23 successful `main` runs (median 823 s). The difference
can only be explained by reruns after failures, queue wait time, or a different
run set — none of which appear in the measured data.

---

## 9. Recomputed CI options (Stage 9 proposal)

The earlier 13–15 / 10–12 / 6–9 minute option table is withdrawn. Recomputed
from measured numbers:

| Option | Critical path equation | Estimated wall-clock |
|--------|----------------------|---------------------|
| Current (PR B, merged) | `max(820, 159, 130, 114, 106.5, 53.5, 48) + 3` | ~823 s (13.7 min) |
| Serial (re-add `needs: tests` to all 4 jobs) | `820 + max(159, 130, 114, 106.5, 53.5, 48) + 3` | ~983 s (16.4 min) |
| Remove `upgrade-safety` from Pr critical path | `max(820, 159, 130, 106.5, 53.5, 48) + 3` | ~823 s (no change; not on critical path) |
| Parallelize `tests` internally (xdist -n 2) | `max(410, 159, 130, 114, 106.5, 53.5, 48) + 3` | ~413 s (6.9 min) |

**Notes on options:**

1. **Current (PR B):** Already merged (#78). All jobs run in parallel from t=0.
   `CI gate` ensures no job is skipped. Wall-clock ≈ 823 s.

2. **Serial:** Re-adding `needs: tests` to `five-minute-path`, `wheel`,
   `upgrade-safety`, `build` would serialize them after `tests`. Since these
   jobs are not on the critical path (all < 820 s), this would add
   `max(159, 130, 114, 106.5, 53.5, 48) = 159 s` to the critical path, giving
   820 + 159 + 3 ≈ 982 s. **Not recommended.**

3. **Remove `upgrade-safety`:** Not on the critical path (114 s < 820 s).
   Removing it saves runner minutes but no wall-clock. Only recommended if the
   owner decides the upgrade-safety test no longer provides value.

4. **xdist (-n 2):** Would split the `tests` job across 2 workers. However,
   each worker builds its own session database (`MEMORY_TEST_DATABASE_URL` is
   shared and unrewritten), so tests that create schemas there may collide.
   The controller also builds its own session database. Not recommended as the
   default; requires collision analysis first (Stage 9 xdist experiment).

**Recommendation:** Keep PR B as-is. The 823 s wall-clock is dominated by the
`tests` job (820 s), specifically the `Run tests` step (466 s) and the tenant
isolation rerun (221 s). Reducing wall-clock further requires either making
`Run tests` faster (fewer tests, faster database) or parallelizing it with
xdist (collision risk to evaluate).

---

## Methodology

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

## Checks

- `python scripts/release/check_versioning.py` → PASS
- `python scripts/check_doc_paths.py` → PASS (advisory rot only, pre-existing)
- `python scripts/check_evidence.py --strict` → PASS
