# Progress — branch `wip/platform-readiness`

One line per phase, newest last. **NOT DONE** means the budget ran out before the
phase started or finished, and nothing in it is claimed.

Every number here came from a command run in the session named in its entry.
This file is the index; the detail lives in the documents it links.

---

## Session 6 — 2026-10-12: green first, then the claims, then the lab

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
  with no lexical fallback and reproduces 151/202, not 194/202. Anything measured
  against it is a property of that arm, not of the product. Phase 4 exists to
  fix exactly this.
