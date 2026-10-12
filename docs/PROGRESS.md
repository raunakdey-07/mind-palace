# Progress — branch `wip/platform-readiness`

One line per phase, newest last. **NOT DONE** means the budget ran out before the
phase started or finished, and nothing in it is claimed.

Every number here came from a command run in the session named in its entry.
This file is the index; the detail lives in the documents it links.

---

## Session 5 — 2026-10-12: stop misleading, then the gate, then the documents

**Written first, before any work, with every phase below marked NOT DONE.** An
early stop must lose nothing.

| phase | state | one line |
|---|---|---|
| 0 stop misleading | **NOT DONE** | banners on the two lab documents; `v0.10.0` naming replaced with the 0.9.x train; tag-count reconciliation |
| 1 upgrade gate that can see policy bugs | **NOT DONE** | runtime-role assertions after the upgrade; the operator grant step; the wrong-policy mutation (`id` instead of `corpus_id`); v0.8.0 via its own ingestion path; a ~1,000-version upgrade; Docker bind assertions with a mutation |
| 2 harness and CI honesty | **NOT DONE** | the leaking per-session role; clean-room on min and max Python; the negative-posture mutation |
| 3 STATUS honesty and release plan | **NOT DONE** | re-derive every number; label 194/202 dev and put held-out v1 and frozen 45/60 beside it; classify every commit with a reason; cherry-pick to decide split vs single; CHANGELOG drafts; tag checklists; `docs/release/v1.0.0-readiness.md` |
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
