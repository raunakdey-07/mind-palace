# Progress

One line per phase, newest last. **NOT DONE** means the budget ran out before the
phase started or finished, and nothing in it is claimed.

Every number here came from a command run in the session named in its entry.
This file is the index; the detail lives in the documents it links.

---

## Session 8 — 2026-10-13: correct the record, the skips, and the evidence (branch `wip/wedge`)

**Written first, before any work, with every phase below marked NOT DONE.**

PR #4 (the 7 commits of `wip/claims-and-lab`) **is merged**: `origin/main` is
`07d0f91` ("Merge pull request #4 from raunakdey-07/wip/claims-and-lab"). Local
`main` was fast-forwarded to it and `wip/wedge` was cut from it. Nothing in
Session 7 that was marked DONE is retracted below; what was wrong is corrected in
Phase 0 and the correction is the record.

**Baseline invariant at `07d0f91`, checked before any edit:**
`git diff v0.9.0..HEAD -- eval/ docs/performance/baseline-packs.json` is **empty**.

| phase | state | one line |
|---|---|---|
| 0 correct what is wrong in the repo | **PARTIAL** | **0a DONE** (two runs at `46add67`, both `success`, 5/5 jobs, nothing skipped). **0b DONE**: the harm matrix, "every arm is net negative" and the "43 questions differ" breakdown are **INVALID / NOT ESTABLISHED**, data kept, real gap stated as **77**. **0c DONE**: lab arms renamed; a bare `A/B/C` now means a benchmark strategy and nothing else. **0d DONE with one gap**: six corrections listed with primary sources re-fetched, but the **before-state is not recoverable from git** |
| 1 the six CI-only skips | **DONE** | **1a, 1b, 1c, 1d DONE**: cause **established and reproduced** (SQLAlchemy 2.1 resolves bare `postgresql://` to an uninstalled driver), fixed via `sync_url()`, **6 skipped -> 6 passed** under 2.1.4, and **the six now run on CI** (run `38446879617`: 1637 passed, 2 skipped). Skip gate **mutation-tested against the real regression** (exit 1, all six named). **1d DONE**: the 15-package table is measured on both sides; **14 of 15 match**, the two that differ are SQLAlchemy (the bug) and numpy (an untested drift). Constraints file **proposed, not applied** |
| 2 evidence from the runner | **DONE** | **2a DONE with runner evidence**: run `38446879617` @ `d1a4612` ran the 6 fixed tests for the first time (**1637 passed, 2 skipped, 1639 collected**) and the new gate **rejected four figures in STATUS.md** — `24`, `1613`, `1619`, `1625` — exit 1. The gate then found a bug **in itself** on that same run (labelled figures were being checked against the junit), fixed, 18 tests green. **2b DONE**: the slip was introduced at `c3c8252`, recorded at `68ff8e3`, and the gate could not catch it because `--require-head` ties a figure to a commit and the record was hand-written at the right commit |
| 3 FK violations and test-env causes | **PARTIAL** | **3a DONE**: all 15 violations named to a test and a line; 3 reproduced deliberately, **9 of 12** `memory_claim_embeddings` reproduced to `tests/test_context_product.py` as a **fixture defect**, **3 NOT ESTABLISHED**; product exposure answered with four reasons. **3b cause NOT ESTABLISHED -- the failure did not reproduce** (`5 passed`); `tests/README.md` written and the resolved database name printed once per session |
| 4 wedge feasibility | **PARTIAL** | **4a DONE**: 2 claims / 2 passages / answered; **0/0/abstained**; **0/0/abstained** -- and the transcript's answer was in the text verbatim. **4d DONE**: `docs/wedge-decision-note.md` recommends the question set before the extractor. **4b, 4c NOT DONE** |
| 5 the lab measures the product | **NOT DONE** | Nothing started. 5a arm on the product path reproducing 194/202 and the 77-question gap per question; 5b the harm matrix recomputed; 5c recall per arm per slice; 5d the threshold cross-validated |
| 6 release prep (docs only, no tag) | **DONE** | **6a DONE**: `mindpalace-os` is **404 on PyPI**; README's bare install changed to install from the repository, reason and the `mind-palace` collision warning beside it. **6b DONE**: CHANGELOG leads with upgrade notes (008-010, the grant step, what a downgrade destroys, no tenants, sealed scoring never happened); `docs/release/tag-checklist.md` written; `security.md` names the 0.9.x train, not a branch. **No tag cut** |

**Owner alone decides when 1.0.0 is ready and when to tag.** Nothing in this file
implies otherwise.

### 0a — CI at `46add67`, every job

Two runs exist at that commit. Both are pasted in full.

| run | created | conclusion | Lint and Test | five-minute path | Upgrade safety from v0.9.0 | Clean install from the built wheel | Docker Build |
|---|---|---|---|---|---|---|---|
| **38419632646** | 2026-10-13T15:09:39Z | `success` | success | success | success | success | success |
| **38422972841** | 2026-10-13T15:32:39Z | `success` | success | success | success | success | success |

**Nothing skipped in either run.** The four dependent jobs ran rather than
reporting `skipped`, which is the failure mode Sessions 3 and 6 recorded.

Run **38422972841**'s own figures, from job `115384866281`:

```
1619 passed, 8 skipped, 8 warnings in 388.67s (0:06:28)   <- Run tests
183 passed, 1 skipped in 171.16s (0:02:51)                <- Tenant isolation
1 passed, 34 deselected in 1.93s                          <- migration roundtrip
```

The 8 skips are named verbatim in §1a below.

### 0b — the correction, and the correction of the correction

**What the documents now say.** Arm `shipped-rank1` scores **117/202 correct**
(151 answered, 33 of them wrong, 1 of those on an absent question) at 85%
coverage, and **127 correct** at the shipped 0.30 threshold. The product's own
benchmark reports **194/202**. The gap is **77**, stated without explanation in
`docs/gate/trust-value-lean.md`, `docs/performance/retrieval-lab-report.md` and
this file.

**The wrong figures, file by file:**

| file | string before | string after |
|---|---|---|
| `docs/gate/trust-value-lean.md:9` | `**151/202**.` | `**117/202 correct** (151 answered, 33 of them wrong, 1 of them on an absent question).` |
| `docs/performance/retrieval-lab-report.md` banner | already corrected in Session 7 | unchanged |
| `docs/performance/retrieval-lab-report.md` addendum correction note | claimed the matrix *"was unaffected and was always right … its own row for arm B reads 112 + 47 + 4 + 39 = 202, which puts arm A at `112 + 4 = 116` correct — consistent with the artifact's 117"* | replaced: `112 + 4` is arm **B**'s count, arm A's is `112 + 39 = 151`, and 116 is not "consistent with" 117. **Session 7's correction was itself wrong**, and it used this project's own internal-consistency check on the wrong sum |
| `docs/PROGRESS.md` standing fact | already said 117 | unchanged, plus the INVALID marking |

**The harm matrix's baseline, measured.** `retrieval-lab-harm-dev.json` carries
`"baseline_correct": 151`, and `scripts/retrieval_lab/harm.py:60-65` defines
`correct()` so that **abstaining on an absent question counts as correct**.
Against `retrieval-lab-dev.json`'s
`arms.shipped-rank1.overall.at_threshold` (`answered 178`, `correct 127`,
`wrong 49`, `false_answer 2`, `abstained_correctly 24`), the 151 is **127
top-1-correct + 24 correctly abstained**. Recomputing each row's
`unchanged_correct + broken` from the artifact's own `detail` list gives 151 for
**all five** arms — the proof that the baseline was reused, not re-derived.

So the matrix partitions on **"not wrong"**, a blend of correctness and
abstention. "Every arm is net negative" is **NOT ESTABLISHED as a correctness
claim**. The numbers are kept unaltered and are not disputed; what they mean is
withdrawn.

### 0c — the arms are named now

`scripts/retrieval_lab/arms.py` exports `ARMS` and `ARM_MEANING`; the functions
are `arm_shipped_rank1`, `arm_bm25`, `arm_semantic`, `arm_rrf`,
`arm_fusion_rerank`, `arm_passage`, `arm_bge_small`. Every artifact key, table
header and prose reference follows. **A bare `A/B/C` in this repository now
means a benchmark strategy and nothing else** — the benchmark's keys
(`A_semantic`, `B_lexical`, `C_fallback`) were deliberately left alone, because
they are cited by `check_evidence` in `STATUS.md` and renaming them would edit a
recorded measurement to fix a naming problem.

**All lab artifacts were regenerated by re-running the lab, not by editing them.**

| artifact | regenerated result |
|---|---|
| `retrieval-lab-dev.json` | **identical** except one field: `bm25.risk_coverage` on the `current` slice, a BM25 tie-break (`accuracy 0.6296 -> 0.6071`). A small real non-determinism, now visible only because the artifact was rebuilt |
| `retrieval-lab-harm-dev.json` | **identical**, every cell: 4/39, 0/10, 2/127, 14/32, 16/48, same `unchanged_correct`/`unchanged_wrong`, `baseline_correct` still 151 |
| `retrieval-lab-calibration.json` | **identical** |
| `retrieval-lab-heldout-v1.json` | **NOT regenerated — see below.** Only the dict keys were renamed, verified to leave the leaf-value multiset unchanged |

**`retrieval-lab-heldout-v1.json` — a provenance problem, NOT ESTABLISHED.**
Regenerating it changed `counts.answerable` **140 -> 141** and
`overall.n_absent` **17 -> 16**, and with them several hundred downstream
numbers. Yet:

* `dataset_hash` is **identical**: `13030d897faabe4be95ab369c152da010bb4475cf916377436781308073fcba0`.
* `embedding_fingerprint` is identical: `5a4d22da8652139f`.
* `scripts/benchmark/m0130_heldout.py`, which builds the questions, has not
  changed since `dbdc35f` (2026-10-04); the artifact was written at `319ed10`
  (2026-10-11).
* `build_questions()` called directly today returns **157** questions, **16**
  of them `ABSENT`.

The committed artifact records an absent-question count the tree cannot produce.
**Cause not established.** Two consequences, and the second matters more:

1. The committed held-out lab numbers are **not reproducible**, and the
   regenerated ones are not committed either — committing either would be
   picking a side without evidence.
2. **`dataset_hash` does not cover `expect_state`.** `STATUS.md` uses it as a
   provenance anchor for these artifacts and it did not move when the
   answerable count did. Phase 5 rebuilds this artifact and should also fix what
   the hash covers.

### 0d — the corrections to `docs/competitive-landscape.md`, and what is NOT recoverable

Session 7 recorded "6 figures corrected against primary sources". **The
before-state is not recoverable.** `git log --all --follow
docs/competitive-landscape.md` returns exactly one commit, `48ac67b`, and the
blob it introduced (`50ba20d`) is the only version that has ever existed in this
repository. The pre-correction draft was never committed, never stashed
(`git stash list` is empty), and no other ref carries it. So the *pairs* (what it
said, what it says now) cannot be listed from evidence; only the corrections the
document declares about itself can.

**That is itself the finding**, and it is recorded rather than papered over: a
correction that was never committed cannot be audited. A future session cannot
tell which of this document's numbers were wrong, or how wrong.

What **is** established — the six corrections the document declares, each
re-verified against its primary source this session:

| # | correction | primary source (re-fetched 2026-10-13) | changes a conclusion? |
|---|---|---|---|
| 1 | TencentDB licence is **MIT**, not `NOASSERTION` | `.../LICENSE` on `feat/server_team`, HTTP 200, 1,315 bytes: *"TencentDB Agent Memory is licensed under the MIT."* | **No.** Licence was already MIT in the prose; only the GitHub API's auto-detection disagreed. |
| 2 | The tag and `ROADMAP.md` have **drifted**: release `v2.0.3-beta.1`, roadmap says *"Current release: **v2.0.1-beta.1**"* | `.../ROADMAP.md`, HTTP 200, 4,956 bytes; both strings present | **No**, but it is the one claim in the doc that is about the vendor's own accuracy, and it is now verified rather than asserted. |
| 3 | *"Editable memories: L1 - L3"* is in the next release, with the vendor's stated reason | `.../ROADMAP.md`, HTTP 200; the string is present verbatim | **No.** It was already the basis of §5's point 4. |
| 4 | MemPalace ships **45 MCP tools**, "not 19" | `github.com/MemPalace/mempalace` `README.md` on `main`, HTTP 200, 16,769 bytes; "45 MCP tools" present | **No.** 19 was never used in any conclusion. |
| 5 | Earlier drafts relied on **forks and mirrors**; the canonical repository was read | `github.com/MemPalace/mempalace` `README.md`, fetched directly this session | **Yes, for provenance.** Every MemPalace figure in §3 now traces to the canonical repo rather than to a mirror. The figures themselves (96.6%, 98.4%, 92.9%) are present in that README. |
| 6 | The AAAK **84.2% R@5** lossy-abbreviation figure is **not in the canonical README** and is carried as second-hand | `docs/competitive/landscape.md`, read 2026-09-26 (our own earlier review) | **Yes.** It is downgraded from a fact to a flagged second-hand figure. It appears in no conclusion. |

**Sources re-fetched and still marked unverified** (the doc's §7 marks these, and
nothing this session changes that): `vectorize.io`, `mem0.ai`,
`supermemory.ai`, `atlan.com`, `studiomeyer`, `hackernoon`, `korben`. Not fetched
this session. The Mem0 figure still has three unresolvable values
(94.4% / 93.4% / 49.0%) and none was chosen.

### 3a — every FK violation in the CI PostgreSQL log, named

Source: the `Lint and Test` job log of run **38422972841** at `46add67`
(`gh run view --job 115384866281 --log`), downloaded this session. 15 violations
in total: 12 `memory_claim_embeddings`, 2 `memory_evidence`, 1
`memory_snapshot_versions`. Each was then **reproduced locally** against a
disposable PostgreSQL 15.4 container (`mp_fkprobe`, port 15437) with
`log_min_error_statement = error`, running each suspect test alone between
`RAISE WARNING` markers so the log segments per test.

| constraint | count in CI log | test and line | deliberate or defect |
|---|---:|---|---|
| `memory_claim_embeddings_corpus_id_claim_id_fkey` | 12 | `tests/test_context_product.py`, **one violation per test**, from the 9 tests that call `build()` on a store | **fixture defect** |
| `memory_evidence_corpus_id_version_id_claim_id_fkey` | 2 | `tests/test_memory_multiple_evidence.py:200` and `:204`, both through the `insert_reference` helper at `:84` | **deliberate negative tests** |
| `memory_snapshot_versions_corpus_id_version_id_fkey` | 1 | `tests/test_memory_core.py:472`, inside `test_database_immutability_and_scoped_provenance` | **deliberate negative test** |

**The `memory_evidence` pair.** The helper at
`tests/test_memory_multiple_evidence.py:84` issues the insert; line 200 passes
`claim_id=other["claim_id"]` and line 204 passes `version_id=other["version_id"]`.
Both are inside `pytest.raises(IntegrityError)` and both exist to prove that a
claim from another corpus, and a version from another corpus, are refused.
Reproduced: exactly 2, in the same test, in that order. A **third** attempt in
the same test (line 213, `claim_id=newer["claims"][0]["id"]`) targets the same
constraint but never reaches it — the `evidence must exactly match its archived
chunk` BEFORE trigger fires first. That is why the log shows 2 and not 3, and it
is the kind of detail that is only visible by running it.

**The `memory_snapshot_versions` one.** `tests/test_memory_core.py:472` inserts
a snapshot membership naming `foreign["id"]`, a version from another corpus. It
is the last assertion in a block of append-only and cross-corpus refusal tests,
and it is wrapped in `pytest.raises(IntegrityError)`.

**The 12 `memory_claim_embeddings`, and why 3 of them are not established.**
Reproduced: **9**, not 12 — exactly one per test, for each of the 9 tests in
`tests/test_context_product.py` that call `build()` on a store. The other two
tests in the file produce none, and neither reaches the query path.
**The remaining 3 are NOT ESTABLISHED**: they did not reproduce, and nothing
this session attributes them.

The mechanism for the 9, and it is a fixture defect rather than a product path:

* `tests/test_memory_core.py::memory_db` creates a **fresh PostgreSQL schema**
  per test (`schema = "memory_test_" + uuid4().hex`), applies **only**
  `migrations/versions/004_memory.py`, and sets
  `SET LOCAL search_path TO "<schema>", public`.
* `migrations/versions/004_memory.py` contains the string
  `memory_claim_embeddings` **zero times** — it does not create that table.
* So `memory_claims` resolves to the per-test schema and
  `memory_claim_embeddings` falls through `search_path` to `public`, which the
  session database really does have. The product's
  `api/services/claim_embeddings.py:24` `INSERT` therefore writes to
  `public.memory_claim_embeddings`, whose `FOREIGN KEY ... REFERENCES
  memory_claims(corpus_id, id)` (`migrations/versions/007_claim_embedding_cache.py`)
  points at `public.memory_claims` — which has no such claim.
* The write is caught by `except Exception` in
  `api/services/memory_query.py::_warm_claim_cache`, logged at `debug`, and the
  test passes.

**Is the product's best-effort cache write exposed to the same violation in real
use? No, for four reasons, each checkable:**

1. Production has **one** schema, so both tables resolve to the same relations
   and the FK's referenced rows are the rows just written. The violation requires
   two schemas and a `search_path` that spans them; nothing in the product sets
   one.
2. `memory_claims` is **append-only**. The same CI log shows
   `ERROR: memory archive is append-only` for `DELETE FROM memory_claims WHERE
   corpus_id = $1`, so a claim cannot vanish between `pending()` and `store()`.
3. The FK is `ON DELETE CASCADE`, so even a delete would remove the cache row
   rather than orphan it.
4. `store()` is called with the same `corpus_id` that `pending()` was called
   with at all three call sites — `ingestion._cache_claim_vectors`,
   `memory_query._warm_claim_cache`, `rehydrate.backfill_claim_embeddings` — so
   a claim id from one corpus cannot be written under another's id.

All three call sites already degrade rather than raise, so even if the condition
were reachable it would cost latency, not correctness.

### 3b — the json-contract failures: **not reproduced this session**

Stated plainly because the instruction asked for one cause: **there is no
established cause, because the failure did not reproduce.**

Run this session, with the CI shape of the environment — `DATABASE_URL` set to a
maintenance database and rewritten by the session hook, `MIND_PALACE_TEST_DB=session`,
and `MEMORY_TEST_DATABASE_URL` set to a separate migrated database, which is
exactly the split `.github/workflows/ci.yml` creates for the `Run tests` step:

    $ pytest tests/test_m016_json_contract.py -q -rs
    5 passed in 22.49s

One candidate was found and is named so the next session does not re-derive it:
`api/services/memory_benchmark.py:456` reads

    url = database_url or os.getenv("MEMORY_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

That is **product code falling back to a test-only variable**, which on CI points
it at a different database than the one the CLI seeded into. It is the only place
outside `scripts/benchmark/` and `tests/` that reads the variable. It did **not**
cause a failure in this session's run, so it is recorded as a lead and **not as
a cause**. `PYTHONPATH` was ruled out for these five tests: none of them spawns a
subprocess, and the exported-`PYTHONPATH` problem recorded in Session 7 item 3 was
the wheel test's subprocess importing from the source tree, a different failure
in a different file.

**Done for 3b regardless of the cause:**

* `tests/README.md` **added**, documenting both variables, which one the session
  hook rewrites, which one it deliberately does not, and why. `MEMORY_TEST_DATABASE_URL`
  is **not** rewritten: what the variable means is unchanged, which was the
  constraint.
* `tests/conftest.py::_announce_databases` prints, **once per session**, the
  database each variable names, and warns when they differ. Before this, nothing
  in a passing run said which database half the suite was using.

---

### 1d — CI versus workstation, and a constraints file **proposed, not applied**

`requirements-runtime.txt` opens with `sqlalchemy[asyncio]>=2.0.0`. There is no
upper bound anywhere in the dependency set, so **the runner and a workstation
resolve the same requirement to different packages.** That single line is the
whole Phase 1 bug.

| package | workstation (Python 3.14.8) | CI (Python 3.11.17) | same? |
|---|---|---|---|
| **SQLAlchemy** | **2.0.46** | **2.1.4** | **NO — this is the bug** |
| asyncpg | 0.30.0 | 0.30.0 | yes (`==` in requirements-runtime.txt) |
| psycopg2-binary | 2.9.13 | 2.9.13 | yes |
| alembic | 1.20.0 | 1.20.0 | yes |
| pgvector | 0.5.1 | 0.5.1 | yes |
| fastapi | 0.143.0 | 0.143.0 | yes |
| starlette | 1.8.0 | 1.8.0 | yes |
| pydantic | 2.14.1 | 2.14.1 | yes |
| httpx | 0.28.1 | 0.28.1 | yes |
| typer | 0.27.3 | 0.27.3 | yes |
| sentence-transformers | 6.1.0 | 6.1.0 | yes |
| torch | 2.14.1 | 2.14.1+cpu | yes, plus the CPU index — a deliberate difference, see `ci.yml` |
| transformers | 5.19.0 | 5.19.0 | yes |
| **numpy** | **2.4.3** | **2.4.6** | **NO — a patch-level drift nobody chose** |
| sentencepiece | NOT INSTALLED | NOT INSTALLED | yes (neither) |

Workstation column: `importlib.metadata.version` on this machine, this session.
CI column: the **"Record what the runner resolved"** step in
`.github/workflows/ci.yml`, added this session, read from run **38446879617** at
`d1a4612`.

**Fourteen of fifteen match. The two that do not are the two that matter, and
neither is pinned.** SQLAlchemy 2.1.4 is the Phase 1 bug. numpy 2.4.6 against
2.4.3 is a drift this project did not choose and has not tested on both sides;
it is recorded here as a difference, not as a defect — **nothing in this session
observed numpy behaving differently**, and no claim is made about it.

The Python versions differ too: **3.14.8** on the workstation, **3.11.17** on
the runner. That is a larger gap than any package and it is deliberate — CI
pins 3.11 in `ci.yml`. It is also untested ground for the other 14.

**Constraints file — proposed, NOT applied.** A `requirements-constraints.txt`
containing:

```
# Behaviour-changing upper bounds. NOT a full lockfile.
#
# Every bound below is here because an unbounded upgrade changed an *observable*
# behaviour, not because a number looked nicer pinned.
#
# sqlalchemy: 2.1 moved the default driver for a bare `postgresql://` from
#   psycopg2 to psycopg (v3). This project installs psycopg2-binary. That change
#   alone made six tests skip on every runner for four sessions -- Phase 1. The
#   cause is FIXED in code (the tests now pin the driver through
#   tenant_harness.sync_url), and the six tests pass on the runner as of
#   d1a4612. This bound is defence in depth, so the next 2.x break is a
#   decision somebody makes rather than a surprise somebody diagnoses.
# asyncpg is already `==0.30.0` in requirements-runtime.txt; it is listed here so
#   this file is the one place to look for the version story.
sqlalchemy>=2.0,<2.1
```

consumed as `pip install -c requirements-constraints.txt -r requirements.txt`.

**numpy is deliberately not pinned.** It drifted 2.4.3 to 2.4.6 without anyone
choosing it and nothing observable followed. Pinning it would encode a fear
rather than an observation, and the next session would have to work out which.

**Deliberately not proposed:** a full lockfile with hashes. It would pin
`torch`, `transformers` and `sentence-transformers` to one wheel per platform,
and the CPU-index choice in `.github/workflows/ci.yml` already exists precisely
because a lockfile was judged not worth that cost. Proposing one would be
proposing a decision, not fixing a defect.

---

### 1a–1c — the six CI-only skips: cause, fix, and a gate that fails the build

**The CI-only skips, verbatim**, from run **38422972841** at `46add67`
(`-rs` output, `Run tests` step, line 1230-1237 of the job log):

```
SKIPPED [1] tests/test_deployment_profiles_and_lifecycle.py:1436: set MIND_PALACE_MIGRATION_ROUNDTRIP=1 to exercise CREATE DATABASE
SKIPPED [1] tests/test_ingestion_statistics.py:413: no reachable DATABASE_URL
SKIPPED [1] tests/test_ingestion_statistics.py:435: no reachable DATABASE_URL
SKIPPED [1] tests/test_ingestion_statistics.py:458: no reachable DATABASE_URL
SKIPPED [1] tests/test_ingestion_statistics.py:473: no reachable DATABASE_URL
SKIPPED [1] tests/test_ingestion_statistics.py:525: no reachable DATABASE_URL
SKIPPED [1] tests/test_ingestion_statistics.py:564: no reachable DATABASE_URL
SKIPPED [1] tests/test_wheel_contents.py:110: set MIND_PALACE_TEST_BUILD_WHEEL=1 to build and inspect a wheel
```

Eight skips: **two opt-in env-var skips, six that were a defect.** Session 5,
6 and 7 each recorded "cause not established" and each instrumented
`_db_available()` and got zero hits. The instrumentation was looking in the wrong
place — the function never raises, it *returns False*.

**1a — reproduced, no temporary CI step needed.** The hypothesis in the brief
(2.1.4 vs local 2.0.x, driver not installed) was tested directly rather than
assumed. A fresh venv with only one thing changed:

```
$ python3 -m venv --system-site-packages /tmp/opencode/venv-sa214
$ /tmp/opencode/venv-sa214/bin/pip install 'sqlalchemy==2.1.4'
$ /tmp/opencode/venv-sa214/bin/python -c "import sqlalchemy; print(sqlalchemy.__version__)"
2.1.4
```

Then the file, against the **same reachable** disposable database, once per
version:

| environment | result |
|---|---|
| workstation, SQLAlchemy 2.0.46 | `6 passed in 111.04s` |
| SQLAlchemy 2.1.4, **before the fix** | `6 skipped in 0.43s` |
| SQLAlchemy 2.1.4, **after the fix** | `6 passed in 110.34s` |

And the exception, directly:

```
$ DATABASE_URL=postgresql://mpadmin:secret@localhost:15434/mindpalace \
  /tmp/opencode/venv-sa214/bin/python -c "import sqlalchemy as sa; sa.create_engine('postgresql://…')"
EXCEPTION: ModuleNotFoundError No module named 'psycopg'
```

**Cause, established.** `tests/test_ingestion_statistics.py::_db_available()`
rewrote only `postgresql+psycopg://`. SQLAlchemy 2.0 resolves a **bare**
`postgresql://` to psycopg2; 2.1 resolves it to **psycopg (v3)**. CI installs
`psycopg2-binary` and not `psycopg`. `create_engine` raised, the bare
`except Exception: return False` swallowed it, and six statistics tests stopped
running. `DATABASE_URL` in `.github/workflows/ci.yml:41` is bare
`postgresql://`, which is why it only ever happened on a runner.

**1b — fixed in the cause, not in the symptom.** `_db_available()` now pins the
driver through `tenant_harness.sync_url()` — the helper
`tests/session_database.py` and `tests/tenant_harness.py` already use — instead
of carrying a fourth copy of the rewrite list. A list of three that one file has
to remember is how this bug comes back. The skip reason now carries the real
exception rather than a sentence that can be false.

**The tests do not fail on CI with the fix in place.** That is a prediction
until a runner says so, and it is recorded as one; the measurement is in the
table above and the runner's is the run id at the end of this session.

**1c — a skip gate, mutation-tested.** `scripts/check_skips.py` reads the
junitxml and fails the job on any skip outside an allowlist. The allowlist holds
exactly the two opt-in skips, keyed on the test name **and** on the opt-in
variable appearing in the reason, so a test that keeps its name and starts
skipping for something else does not pass on the old entry.

Mutation test, run against the **real regression** — the pre-fix file restored,
under SQLAlchemy 2.1.4:

| input | exit | what it printed |
|---|---:|---|
| control: the fixed file + `test_wheel_contents.py` | **0** | `every skip is allowlisted (1 of 1)` |
| mutant: the pre-fix file under 2.1.4 (7 skips) | **1** | `UNALLOWED SKIPS (6)` and all six `tests.test_ingestion_statistics::*` names |
| an allowlisted name skipping for a different reason | **1** | rejected |
| a junitxml that does not exist | **1** | rejected rather than passing quietly |

12 tests over both gates in
`tests/test_skip_gate_and_runner_evidence.py`, including the two rejections
Phase 2a needs.

---

### 4a — ingestion inventory, three input shapes

`scripts/wedge/ingestion_inventory.py`, one disposable corpus on a throwaway
PostgreSQL 15.4 container, one document per shape. "Passages" is the number of
`memory_evidence` rows that shape added, read through
`api/services/memory.py:evidence`. "Recall" is the product's own `query`
operation against that corpus. Full JSON at `/tmp/…/inventory.json` is not
committed; the script is, and it re-runs.

| input shape | claims written | passages | chunks | recall |
|---|---:|---:|---:|---|
| document with `claims:` frontmatter | **2** | **2** | 1 | **answered, and correct** |
| plain markdown, no frontmatter | **0** | **0** | 1 | **abstained** |
| five-session conversation transcript | **0** | **0** | 6 | **abstained** |

Three things fall out, and the second is the expensive one.

1. **A document with no authored claims is stored and cannot answer anything.**
   Not a threshold problem: `passages_stored` is 0, so there is no evidence row,
   no claim to attach one to, and `current` returns nothing.

2. **The transcript's answer was in the text, verbatim, and still abstained.**
   Session 5 contains the sentence *"Checkout retries a failed payment twice
   before giving up."* The question was *"How many times does checkout retry a
   failed payment?"* The document was chunked into 6 chunks, the chunk holding
   that sentence was stored, and the product returned nothing. A system that
   ranks **chunks** would have answered; this one ranks **claims**, and there
   were none.

3. **The authoring rule is stricter than the docs suggest, and it is right.**
   `api/services/memory.py:validate_source` requires the claim text to appear
   **verbatim** in an archived chunk ("semantic inference is not supported") and
   the `evidence` quote to be an **exact substring** of one. Two drafts of the
   frontmatter document were **rejected** before the third was accepted — one for
   a paraphrased quote, one because a claim that reads better than the document's
   own wording is not in the document. That is the trust property working. The
   cost is authoring, and pricing it needs a model this project does not have a
   credential for.

### 2a — evidence from the runner, and the runner rejecting four figures

**Run 38446879617 at `d1a4612`, the first run in this repository's history where
the six `tests/test_ingestion_statistics.py` tests RAN on a runner.** Its own
`Run tests` line, from job `115467468438`:

```
1637 passed, 2 skipped, 1639 collected in 388.79s
```

against `1619 passed, 8 skipped` at `46add67`. The whole difference is those
six tests plus twelve new ones.

**The junitxml is uploaded as an artifact** (`actions/upload-artifact@v4`, name
`pytest-junitxml`, 90-day retention) and `check_evidence --require-head
--junitxml /tmp/pytest-main.xml` runs **in the same job, two steps later**,
reading that file. Its output:

```
  --  runner mode: /tmp/pytest-main.xml says 1637 passed, 2 skipped, 1639 collected, at d1a4612
PROBLEMS (4):
  !!  docs/STATUS.md: claims 24 passed;  no run produced that
  !!  docs/STATUS.md: claims 1613 passed; no run produced that
  !!  docs/STATUS.md: claims 1619 passed; no run produced that
  !!  docs/STATUS.md: claims 1625 passed; no run produced that
[error]Process completed with exit code 1.
```

**Rejects a figure from an older commit: YES.** `1613` and `1619` are real —
runners genuinely produced them — but they were unlabelled, so they were claims
about *this* tree, and this tree produced neither.

**Rejects a hand-edited figure: YES.** `1625` is the number Session 7 laundered
out of a log archive, by reading the `check_evidence` step's echo of the
workstation record instead of the `Run tests` step. It was rejected here for the
same reason as the others, and the reason is structural this time: the number
cannot be produced by a junitxml, because **a person cannot put a `<testcase>`
in a file.**

**`GITHUB_SHA` is checked.** `--expect-sha` defaults to `$GITHUB_SHA` and is
compared with `git rev-parse HEAD`; a mismatch fails loudly rather than
quietly validating one commit's figure against another's junit.

### 2a, part two — **the gate found a bug in itself, on that same run**

The first version of runner mode replaced `RUN_RECORD` wholesale. So
`24 passed (as of fd53840)` failed, because today's run did not produce 24 —
even though `fd53840` really did, and the figure is a **subset** run that the
document says so about.

That is a wrong rejection, and it was caught by the gate being run against real
documents rather than only against unit tests. The rule is now what the
docstring always said:

| figure form | checked against |
|---|---|
| `1637 passed` — no label | **the junitxml of this run, and nothing else** |
| `24 passed (as of fd53840)` | **`docs/performance/test-runs.json`**, because a labelled figure is a claim about a named commit |

Both pools are named in the gate's output, so the split is visible rather than
implicit:

```
  --  runner mode: unlabelled figures are checked against this run; figures
      labelled 'as of <commit>' are checked against docs/performance/test-runs.json
  ok  docs/STATUS.md: 24 passed labelled 'as of fd53840', which is a recorded run
  ok  docs/STATUS.md: 1613 passed labelled 'as of 312958d', which is a recorded run
  ok  docs/STATUS.md: 1619 passed labelled 'as of c3c8252', which is a recorded run
  ok  docs/STATUS.md: 1637 passed labelled 'as of d1a4612', which is a recorded run
```

`STATUS.md` now leads with **1637 passed (as of `d1a4612`)** and keeps 1613 and
1619 with the commits that produced them. `docs/performance/test-runs.json` has
the `d1a4612` entry with its junitxml, and `pytest-head.xml` is that junit.

**18 tests** cover both gates: the 6 pre-existing ones in
`tests/test_evidence_gate_tied_to_head.py` plus the 12 in
`tests/test_skip_gate_and_runner_evidence.py`, all green.

### 2b — the 1625 vs 1619 slip: **reconciled**

| | |
|---|---|
| which commit recorded it | **`68ff8e3`**, "fix: the runner figure I recorded was wrong, and the gate could not catch it" |
| which commit introduced the error | **`c3c8252`**. Run `38410829513` @ `c3c8252` was recorded as "1625 passed". Its own `Run tests` step says **`1619 passed, 8 skipped, 8 warnings in 426.79s`**. 1625 is the *workstation* count at `312958d`. |
| why | 1625 was read out of the log archive by grepping the whole file and taking the first match, which was the `check_evidence` step echoing the workstation record back. |
| what the gate could not catch | `--require-head` ties a figure to a **commit**, and the record had been hand-written **at the right commit**. A gate that reads what a person wrote cannot disagree with them. That limit was written down at the time and nothing was built against it; `--junitxml` is the thing built against it. |
| now | `1625` is rejected by the runner gate (above). The `38410829513` entry in `test-runs.json` carries the correction and says so. |

---

**Written first, before any work, with every phase below marked NOT DONE.**
Branch `wip/claims-and-lab`, cut from `main` at `f08d8f8` (the merge of
`wip/platform-readiness`).

**Owner decisions D1 (model access) and Hub access were not filled in before this
session started.** Both are still the bracketed placeholders, so Phase 4c's MiniLM
LongMemEval baseline and all of Phase 5 are blocked on the owner's answer, not on the
work. They are recorded as blocked rather than guessed.

| phase | state | one line |
|---|---|---|
| 0 push | **DONE** | Branch pushed. Four runs, all `success`, 5/5 jobs each: **`38400659433`** @ `312958d`, **`38410829513`** @ `c3c8252`, **`38414137784`** @ `bc8af70`, **`38417158234`** @ `68ff8e3`. `log_error_verbosity = terse` in the compose file, with the `STATEMENT` caveat in the file itself |
| 1 land the competitive doc (docs only) | **DONE** | `docs/competitive-landscape.md` added; **6 figures corrected against primary sources**; 5 Mind Palace figures verified against evidence artifacts and `STATUS.md`; 8 sources fetched and read directly, 5 marked unverified this session; linked from the docs index only; `check_evidence --strict` passing |
| 2 finish the claims-true work | **PARTIAL** | 2a, 2b, 2d, 2e, 2f **DONE**. All 8 skips named. Evidence gate tied to HEAD, rejecting a stale figure, with 6 tests over it. The `MEMORY_TEST_DATABASE_URL` cause found and proven. Warnings 39 -> 8. `corpus` vs `corpora` documented, nothing added. **2c NOT DONE**: the 9 `memory_claim_embeddings` violations are isolated to `tests/test_context_product.py` and are a fixture defect, but the 2 `memory_evidence` and 1 `memory_snapshot_versions` are **not established** |
| 3 the lab measures the product | **PARTIAL** | **3a premise DONE:** the product path re-run this session reproduces **194/202 exactly** (`scripts/benchmark/m0115_experiments.py`, strategy `C_fallback`, dataset `9cdecda73cbdc8ae`, A 178 and B 164 also identical to the recorded artifact). The lab's own arm A is still the old arm A at **117/202 correct**, not 151 as the docs claimed -- that error is corrected. **3a arm rewrite, 3b, 3c, 3d, 3e NOT DONE**, so every harm-matrix and recall figure remains a property of the lab arm, not of the product |
| 4 wedge feasibility (measurement and design notes only) | **NOT DONE** | Nothing started. 4a the three input shapes, 4b the three ADR repositories with "superseded by" status, 4c LongMemEval reachability and per-category counts plus the MiniLM baseline **blocked: Hub access not answered**, 4d `docs/wedge-decision-note.md` |
| 5 downstream pilot | **NOT DONE** | **blocked, D1 not answered.** Four arms, one small and one stronger model with exact IDs, per slice, harm first, Wilson intervals |

**Owner decides when 1.0.0 is ready.** Nothing in this file implies otherwise.

---

## Session 6 — 2026-10-12: green first, then the claims, then the lab (branch `wip/platform-readiness`)

**Written first, before any work, with every phase below marked NOT DONE.**

Branch is RED on CI. Run `38242335025`, `failure`, 9m42s: **Lint and Test** failed on
2 tests, and the other four jobs (Clean install, Five-minute path, Upgrade
safety, Docker Build) were **skipped** because they depend on it.

| phase | state | one line |
|---|---|---|
| 1 CI green | **DONE** | Run **38375728987**, `success`, 5/5 jobs, nothing skipped, 13m49s wall-clock: Lint and Test; Clean install from the built wheel; Five-minute path; **Upgrade safety, from v0.9.0**; Docker Build. Runner suite: **1613 passed, 8 skipped** (main) + 183 passed, 1 skipped (tenant isolation). **Hermetic on the runner: the `NOT hermetic` line is gone** (0 occurrences). The two `RUNTIME_ROLE` literal failures are fixed. CPU torch: default PyPI wheel **900 MB** -> CPU index **184 MB**. |
| 2 make our own claims true | **PARTIAL** | **2b and 2f DONE.** `scripts/db/runtime_roles.sql` printed the runtime password on every run (`SELECT set_config` returns its argument and psql prints every result set); now ends in `\\g /dev/null` -- canary password: **0 in stdout, 0 in stderr, exit 0**, role still `NOSUPERUSER NOBYPASSRLS`. security.md corrected. **The leaking `mp_tenant_test_<hex>` role is fixed**: a cluster carried 24; baseline 0 -> run 1 -> 0 -> run 2 -> 0. **2a, 2c, 2d, 2e NOT DONE.** |
| 3 the lab measures the product | **NOT DONE** | arm A on the real product path reproducing 194/202; the harm matrix against it; recall@1/3/5; the arm-E threshold cross-validated; arm G if Hub allowed |
| 4 downstream pilot | **NOT DONE** | needs D1. Arms A/B/C/D, at least one small and one stronger model, per slice, harm first |
| 5 positioning and authoring cost | **NOT DONE** | `docs/what-mind-palace-claims.md`; the raw-notes-to-claims cost for a 20-page document |

**Owner decides when 1.0.0 is ready.** Nothing in this file or in
`docs/release/v1.0.0-readiness.md` implies otherwise; the readiness document is an
informational checklist and stays labelled as one.

## Session 5 — 2026-10-12: stop misleading, then the gate, then the documents

**Written first, before any work, with every phase below marked NOT DONE.** An
early stop must lose nothing.

| phase | state | one line |
|---|---|---|
| 0 stop misleading | **DONE** | banners on both lab documents: arm A is not the product path, 151/202 not 194/202, so the harm matrix, the 36.4% ceiling and every confident-wrong rate are NOT ESTABLISHED for the product. `v0.10.0` replaced with the 0.9.x train; one historical occurrence in `docs/release-map.md` kept, because it records that M015.2/M016 were consolidated. Tag counts reconciled: **13 tags exist, 8 were matrix rows**, five named with reasons. |
| 1 upgrade gate that can see policy bugs | **PARTIAL** | **1a/1b/1c DONE.** A real uvicorn server as `mindpalace_runtime` in `required` mode, read through the public SDK with a real credential: 11 assertions, all green on a v0.9.0 archive upgraded to 010. The operator step is asserted, not assumed -- a credential is a tenant when issued, and each grant widens it. **The wrong-policy mutation is CAUGHT, exit 1**, and the control exits 0. **1d/1e/1f NOT DONE** (v0.8.0 path, ~1,000-version upgrade, Docker bind assertions). |
| 2 harness and CI honesty | **NOT DONE** | the leaking per-session role; clean-room on min and max Python; the negative-posture mutation |
| 3 STATUS honesty and release plan | **PARTIAL** | **3a and 3e DONE.** STATUS.md now carries one three-score table with 194/202 labelled a DEVELOPMENT SCORE and held-out v1 (burned) and frozen 45/60 beside it; the four-way breakdown stops banking the 1 confident-wrong that its own runner documents as a grader disagreement (product 0, grader 1). The 'no migration' sentence is now explicitly past tense, with the downgrade cost stated. `docs/release/v1.0.0-readiness.md`: 8 rows, **0 MET**, 2 PARTIAL, 5 NOT MET, 1 NOT ESTABLISHED. **3b (commit classification, cherry-pick split decision), 3c (CHANGELOG drafts), 3d (tag checklists) NOT DONE.** |
| 4 lab measures the product | **NOT DONE** | arm A on the real product path reproducing 194/202; the harm matrix against it; recall@1/3/5 per slice; the arm-E threshold cross-validated |
| 5 D3 design note | **NOT DONE** | three options with receipt/pack/SDK/MCP effects and 1.0.0-contract compatibility |

## Session 4 — 2026-10-12: release safety

**CI green on a real runner, with the new gate job.** Run `38237730312`,
`success`, five jobs: Lint and Test; Clean install from the built wheel; The
five-minute path; **Upgrade safety, from v0.9.0**; **Docker Build** (used to be
main-only).

Phase 1 proved the v0.9.0 -> head 010 upgrade preserves everything: the v0.9.0
receipt still verifies offline, claim identities do not move, no claim is lost,
the pack is byte-identical once the clock is pinned. Supersession survives.

**A downgrade destroys `memory_principals` and disables RLS on all 9 protected
tables. It preserves all 6 claims.**

**The fixture cannot prove tenant preservation.** A `DELETE FROM
memory_principals` mutation in migration 008 SURVIVED, because a v0.9.0 archive
has no principals -- the table is created by 008 itself. Phase 1c this session
replaces that with a mutation the job must actually catch.

Phase 2a: `tests/tenant_harness.py` provisions a per-session role and stops
rotating the cluster's `mindpalace_runtime`. Two concurrent pytest sessions on
one cluster: 61 passed each, shipped role password UNCHANGED. **One test role
still leaks per session** -- Phase 2a this session.

## Session 3 — 2026-10-12: CI green

Run `38232167537`, `success`, 14m57s. Three runner-only defects, each one commit
and each hiding behind the next because the following step reported `skipped`:
the posture step asserted `import api.main` refuses when the product checks the
lifespan; the posture steps ran after a step that rotated the runtime role's
password; the marketing example ran half of its own two-shell instructions.

## Session 2 — 2026-10-11

Phases 0-3 done. Secret scan clean; branch pushed with tags byte-identical.
Silent-failure fixes with a fail-before/pass-after regression test. Stale-runtime
handshake. Protocols frozen. Retrieval lab built, arms A-F, arm G unreachable.
**v4 does not exist.** Phase 6 downstream NOT DONE.

## Session 1 — 2026-10-11

Hermetic suite, evidence hygiene, docs split, two release blockers fixed (wheel
missing `mindpalace_errors`; default-corpus `char(64)` read-back).

---

## Standing facts that are easy to get wrong

* **`origin/main` is `v0.9.0`.** No `v0.9.1` tag locally or remotely.
* **`main` is contained in this branch.** All 5 commits `main` has ahead of
  `origin/main` are ancestors of `wip/platform-readiness`.
* **Migrations 008, 009 and 010 have never shipped.** They all arrived in
  `da8a14a`, which is after v0.9.0. v0.9.0's schema head is 007.
* **Held-out v1 is burned** (157 questions). Diagnostic only.
* **v3 is not independent of the dogfood. v4 does not exist.**
* **Sealed scoring has never happened.** No number in this repository is
  evidence of generalisation.
* **Arm A in the retrieval lab is not the product path.** It uses rank-1 grading
  with no lexical fallback and reproduces **117/202 correct** (151 answered, 33
  wrong), not 194/202. Anything measured against it is a property of that arm,
  not of the product. Phase 3 exists to fix exactly this. *(Corrected
  2026-10-13: this line previously said "reproduces 151/202", which conflated
  the number answered with the number correct.)*

---

## What this session got wrong, and how it was found

Kept here because the corrections are the evidence.

1. **A runner figure was laundered through the gate's own output.** Run
   `38410829513` was recorded as "1625 passed". 1625 is the *workstation*
   count. The runner said **1619**. The number came from grepping the whole log
   archive and taking the first match, which was the `check_evidence` step
   echoing back the workstation record rather than the `Run tests` step. Fixed
   in `68ff8e3`. The lesson is a real limitation, not a slip of the pen:
   `--require-head` ties a figure to a **commit**, and it cannot tie a figure to
   a **log**. The run record is hand-written either way, so the gate believes a
   wrong number exactly as much as a right one.

2. **An existing doc's headline number was wrong.** `docs/performance/
   retrieval-lab-report.md` said arm A was "correct on 151 of 202". 151 is the
   number *answered*; `correct` is 117. The harm matrix underneath was always
   right -- its own row reads 112 + 47 + 4 + 39 = 202, putting arm A at 116 --
   so only the prose label was wrong, and it had been copied into a banner and
   into this file.

3. **My own environment caused five test failures that were not defects**, and
   I nearly reported them as such: an exported `PYTHONPATH` made the wheel
   test's subprocess import from the source tree. The lesson is that "these
   tests fail" was my conclusion before I had checked whether the tests were
   wrong or the box was.

4. **A stale session database from a killed run caused a 6-failure run**, and I
   recorded a number from that run before noticing. Disposable databases are
   only disposable if the previous one was actually dropped.

5. **Six CI-only skips remain unexplained.** Instrumenting
   `tests/test_ingestion_statistics.py::_db_available()` to log its real
   exception recorded **zero** failures across a full local run, and all 6 tests
   pass locally every time. Their CI-only cause is **not established** and is
   not guessed at anywhere in this repository.

## The mechanism that actually found things

Every substantive error above was caught by a check comparing two things that
ought to agree, and failing when they did not: the gate refusing a figure whose
commit did not match, the lab artifact contradicting a prose label, a test
passing alone but not in a suite, an instrumented probe logging zero hits.
None of them was caught by re-reading the code.
