# Progress — branch `wip/platform-readiness`

One line per phase, newest last. **NOT DONE** means the budget ran out before the
phase started or finished, and nothing in it was claimed.

Every number here came from a command run in the session named in its entry.
The session that produced the retrieval-lab figures is recorded in
`docs/performance/retrieval-lab-report.md`; this file is the index.

---

## Session 3 — 2026-10-11 (this round)

| phase | state | one line |
|---|---|---|
| 0 integrity after rewrite | **DONE** | gitleaks over the 44 pushed commits: 0 findings. `.retrieval_lab_cache/` is gitignored at `.gitignore:169`, 0 tracked `.npy`. `git diff v0.9.0..HEAD` over `eval/`, `baseline-packs.json`, `dev-tokenizer.json`: empty. All 5 commits that local `main` has ahead of `origin/main` are already ancestors of this branch, so `main` is contained in the branch and there is no divergence to reconcile. |
| 1 CI green | **DONE — GREEN** | run `38232167537`, 2026-10-12 09:47:37Z → 10:02:34Z, **14m57s**. `Lint and Test` success 11m09s (27 steps), `Clean install from the built wheel` success 2m26s, `The five-minute path, from a clean checkout` success 3m43s. `Docker Build` **skipped, and I am not counting it as green**: it carries `if: github.ref == 'refs/heads/main'` and genuinely did not run. Three runner-only defects fixed, each one commit: the posture step's wrong entry point; the posture steps ordered after a step that rotates the runtime role's password; and the marketing example running half of its own two-shell instructions. |
| 2 151 vs 194 | **DONE** | reconciled exactly: 43 questions differ and the lab is right on **zero** that the benchmark calls wrong. 26 are top-1-vs-top-k (23 at rank 2, 3 at rank 4), 17 are the lexical-fallback path. **194 is the system number; 151 is a ranking metric for one arm and must never be quoted beside it.** |
| 5 release plan | **PARTIAL** | 46 commits after v0.9.0 classified (heuristic, some rows need a human). Migrations 008/009/010 were ALL introduced by `da8a14a`, which is after v0.9.0 — **no release has ever shipped them**; v0.9.0's head is 007, same as v0.8.0. The suspected STATUS.md contradiction does not exist: line 98's "No migration" is scoped to v0.9.0 and line 47's head-010 is about this branch. Version-train proposal NOT DONE. |

| 4 retrieval lab | **PARTIAL** | 3a harm DONE (every arm net negative). 4f/4g NOT DONE. See below. |
| 5 D3 note | **NOT DONE** | |
| 6 STATUS honesty | **NOT DONE** | |

### Phase 1 detail, for whoever picks this up

The posture step asserted `import api.main` refuses under a server deployment.
`api/main.py` documents that importing must not require a connection and that
the posture check lives in the **lifespan**. The step was asking the wrong
question and had been red on every runner. It now runs the lifespan, asserts a
nonzero exit **and** that the output contains `refusing to start`, and the
positive step runs the same check on the least-privileged role. Mutation-tested:
`ALTER ROLE mindpalace_runtime SUPERUSER BYPASSRLS` makes it refuse.

### Phase 1, second failure: the posture step was pointed at a rotated role

The positive posture step failed after the previous fix. The database was
correct -- all 13 protected tables had RLS enabled AND forced, the role was
NOSUPERUSER NOBYPASSRLS, and `psql` authenticated fine. The cause:

    tests/tenant_harness.py:66   RUNTIME_PASSWORD = "tenant-isolation-test-password"

The harness runs a modified copy of `scripts/db/runtime_roles.sql` against
`$DATABASE_URL`, and that script ends in `ALTER ROLE mindpalace_runtime
PASSWORD ...`. On CI `$DATABASE_URL` is the **shared** `mindpalace` database, so
`Tenant isolation` rotated the runtime role's password. Every later step
authenticating as `mindpalace_runtime` with `ci-runtime-role-password` could not
connect, and `posture` -- which degrades to "unknown means not proven" by design
-- reported all 13 protected tables as unprotected.

The two posture steps now run immediately after provisioning, before the suite
and before tenant isolation. Posture is a property of the deployment, so it
belongs where the deployment is built.

**Not fixed, and it should be:** `tests/tenant_harness.py` rotating a password on
`$DATABASE_URL` is its own defect. It should provision against a database it
owns. Flagged rather than done, because it is a test change with a wider blast
radius than a workflow reorder.

### Phase 1, third failure: the example ran half of its own instructions

`httpx.ConnectError: All connection attempts failed`.
`examples/marketing-campaign-memory/demo.py` is a *client* -- it imports none of
Mind Palace, not even the ASGI app object, because the point is that an
independent application can be built on the documented HTTP interface. Its README
says so in two shells; CI ran only the second. The step now starts the service,
waits for `/openapi.json` to answer 200, runs the demo, and stops the service.
Verified locally: HTTP 200, demo exit 0.

Three runner-only failures had now been sitting behind each other, each hiding
the next because the step after it was reported `skipped`.

### Phase 4 items still open, in priority order

### Phase 1, mutation testing of the posture steps

| mutation | result |
|---|---|
| baseline: clean provisioning | PASS |
| B: `DISABLE ROW LEVEL SECURITY` on `memory_claims` | FAILS, names `memory_claims` |
| C: `NO FORCE ROW LEVEL SECURITY` on `memory_claims` | FAILS, names `memory_claims` |
| D: rotate the runtime role's password (what the tenant harness does) | FAILS, names all 13 — **the exact CI failure** |
| D, password restored | PASS |

Mutation A ("never provision the role at all") was not a real mutation: the role
is cluster-wide and already existed, so the connection still succeeded. Recorded
rather than quietly dropped.

### Phase 1, third failure: the example ran half of its own instructions

`httpx.ConnectError: All connection attempts failed`.
`examples/marketing-campaign-memory/demo.py` is a *client* -- it imports none of
Mind Palace, not even the ASGI app object, because the point is that an
independent application can be built on the documented HTTP interface. Its README
says so in two shells; CI ran only the second. The step now starts the service,
waits for `/openapi.json` to answer 200, runs the demo, and stops the service.
Verified locally: HTTP 200, demo exit 0.

Three runner-only failures had now been sitting behind each other, each hiding
the next because the step after it was reported `skipped`.

### Phase 4 items still open, in priority order

* **4a** recall@1/3/5 per arm per slice, claim and passage — NOT DONE, and it is
  the number the "hand the model ranked candidates" design stands or falls on.
* **4b** arm F canary (query with each passage's own text; recall@1 must be
  near 1.0) — NOT DONE. **Until that canary runs, the git-derived figure for
  arm F is not trustworthy.** The 0.0093 survives a gold-set correction but a
  canary has never been run against it, so the "retrieval weakness" verdict is
  provisional.
* **4c** cross-validate arm E's raw-score threshold by selecting the threshold
  inside each training fold — NOT DONE. The 36.4% ceiling is an upper bound and
  only the fitted gate's 14.2% has been cross-validated.
* **4d** L2 rebuild digest — NOT DONE.
* **4e** dogfood Q4 per arm — NOT DONE. Q4 remains untested by any arm.
* **4f** arm G — blocked on Hub. 4g independent set — the D2 decision was left
  unedited, so **sealed scoring has not happened**.

## Session 2 — 2026-10-11

| phase | state | one line |
|---|---|---|
| 0 checkpoint, tag matrix | DONE | README quickstart on every tag; v0.9.0 passes end to end, v0.2.x fail to migrate (no `script_location`), neither bug in v0.9.0 |
| 1 clean-room CI | DONE | 17/17 on a built wheel from outside the repo; mutations fail as required |
| 1 secret/audit, first push | DONE | gitleaks 21 findings, all pre-existing hex digests; branch pushed, tags byte-identical |
| 2 silent-failure fixes | DONE | `parse_markdown` reports malformed frontmatter; `sync` reports documents AND claims; regression test fails before / passes after |
| 2 stale runtime | DONE | `build_id()` handshake; a changed code under a live runtime is refused and discarded |
| 3 sealed eval | PARTIAL | protocol frozen before any run; **v4 does not exist** — no independent author, model credit $0.00 |
| 4 retrieval lab | DONE | lab built, arms A–F, protocol frozen; arm G unreachable |
| 5 lean | PARTIAL | install paths measured; CPU-only torch measured but **not applied** to the README |
| 6 downstream | NOT DONE | no model reachable |

## Session 1 — 2026-10-11

Platform-readiness round: hermetic suite, evidence hygiene, docs split, two
release blockers fixed (wheel missing `mindpalace_errors`; default-corpus
`char(64)` read-back). Suite 1607 passed / 2 skipped at the time.

---

## Standing facts that are easy to get wrong

* **`origin/main` is `v0.9.0`.** There is no `v0.9.1` tag locally or remotely.
* **`main` is contained in this branch.** All 5 commits `main` has ahead of
  `origin/main` are ancestors of `wip/platform-readiness`.
* **Held-out v1 is burned** (157 questions, scored many times, failures known
  to this repository's author). Diagnostic only.
* **v3 is not independent of the dogfood.** v4 does not exist.
* **Sealed scoring has never happened.** No number in this repository is
  evidence of generalisation.


---

## Session 4 — 2026-10-12: release safety

**CI is green on a real runner, with the new gate job.** Run `38237730312`,
`success`:

| job | result |
|---|---|
| Lint and Test | success |
| Clean install from the built wheel | success |
| The five-minute path, from a clean checkout | success |
| **Upgrade safety, from v0.9.0** | **success** (new this round) |
| **Docker Build** | **success** (used to be main-only) |

That is the two conditions a tag needs from CI: green on a built wheel, Docker
included, and the v0.9.0 upgrade test passing on a runner rather than on a
remembered local run.

### What Phase 1 found

The v0.9.0 -> head 010 upgrade **preserves everything**: the v0.9.0 receipt still
verifies offline, claim identities do not move, no claim is lost, and the pack is
byte-identical once the clock is pinned. Supersession survives the round trip —
`history` shows PostgreSQL, `recall` returns CockroachDB.

**A downgrade destroys `memory_principals` and disables RLS on all 9 protected
tables. It preserves all 6 claims.** That belongs in the release notes.

**The fixture cannot prove tenant preservation, and that is a real limit.** A
`DELETE FROM memory_principals` mutation in migration 008 SURVIVED the job,
because a v0.9.0 archive has no principals — the table is created by 008 itself.
The test that does bite is the after-export asking a different question, which
fails with `differs in ['query']`.

### Phase 4 naming, applied to docs only

v0.9.1 is skipped. The branch is **unreleased, planned v0.10.0**. `pyproject.toml`
is untouched at 0.9.1 and nothing is tagged; applying the NAME to the version
field would be a release, not a plan.
