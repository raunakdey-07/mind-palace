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
| 0 correct what is wrong in the repo | **NOT DONE** | 0a CI at `46add67`/`07d0f91`, every job pasted; 0b arm A is **117 correct**, not 151 answered, and the harm matrix, "every arm is net negative" and the "43 questions differ" breakdown are INVALID/NOT ESTABLISHED with the data kept; 0c rename lab arms so "A/B/C" cannot mean two things; 0d the 6 corrections to `docs/competitive-landscape.md` with primary sources |
| 1 the six CI-only skips | **NOT DONE** | 1a reproduce in a fresh venv from `requirements.txt` as CI resolves it (SQLAlchemy 2.1.4 vs local 2.0.51 is a **hypothesis, not a finding**); 1b fix the cause and report any resulting CI failure; 1c a skip gate with an allowlist for the two opt-in env-var skips, mutation-tested; 1d 15-package version table, constraints file **proposed, not applied** |
| 2 evidence from the runner | **NOT DONE** | 2a upload junitxml, run `check_evidence --require-head` in the same job, reject a hand-edited figure and a stale-commit figure; 2b reconcile the 1625 vs 1619 slip |
| 3 FK violations and test-env causes | **NOT DONE** | 3a name the test and line behind every `memory_claim_embeddings`, `memory_evidence`, `memory_snapshot_versions` violation; 3b one stated cause for the json-contract failures, document the session-DB rewrite of `MEMORY_TEST_DATABASE_URL`, print the resolved database name once per session |
| 4 wedge feasibility (measurement and design notes only) | **NOT DONE** | 4a ingestion inventory over three input shapes; 4b three ADR repositories with "superseded by", **design only**; 4c LongMemEval via `workflow_dispatch` only, never on push, with a MiniLM top-k baseline labelled a baseline; 4d `docs/wedge-decision-note.md` |
| 5 the lab measures the product | **NOT DONE** | 5a lab arm A on the real product path graded by the benchmark's own grader, reproducing 194/202, then the 77-question gap per question; 5b the harm matrix of every other arm recomputed against it; 5c recall@1/3/5 per arm per slice; 5d the arm-E threshold selected inside each training fold, grouped by claim key |
| 6 release prep (docs only, no tag) | **NOT DONE** | 6a PyPI state of `mindpalace-os` and the minimal README change; 6b CHANGELOG draft, tag checklist, `security.md` version wording. **No tag.** |

**Owner alone decides when 1.0.0 is ready and when to tag.** Nothing in this file
implies otherwise.

---

## Session 7 — 2026-10-13: push, land the competitive doc, then the lab measures the product

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
