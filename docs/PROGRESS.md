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
| 1 CI green | **DONE** | the posture step asserted `import api.main` refuses, but the product deliberately checks the database posture in the *lifespan*. Step rewritten to run the lifespan, assert nonzero exit **and** the refusal reason; positive step now runs the same check on the least-privileged role. Mutation-tested both directions. |
| 2 151 vs 194 | **DONE** | reconciled exactly: 43 questions differ and the lab is right on **zero** that the benchmark calls wrong. 26 are top-1-vs-top-k (23 at rank 2, 3 at rank 4), 17 are the lexical-fallback path. **194 is the system number; 151 is a ranking metric for one arm and must never be quoted beside it.** |
| 3 release train | see below | |
| 4 retrieval lab | see below | |
| 5 D3 note | see below | |
| 6 STATUS honesty | see below | |

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
