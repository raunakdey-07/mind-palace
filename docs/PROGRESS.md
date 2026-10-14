# Progress

An index, one line per phase, newest last. **NOT DONE** means the phase was not
finished and nothing in it is claimed.

Every number came from a command run in the session named beside it.

**The full history of all twelve sessions, unedited, is
[docs/internal/progress-history.md](internal/progress-history.md).** This file
was 1397 lines; that is where the detail went, not the bin. Figures quoted below
are the ones still load-bearing. Anything not here is in the history file or in
the document the phase links.

---

## Session 12 — 2026-10-14: cut what nobody reads (branch `wip/independent-set`)

- **1a** DONE. Five benchmark scripts with zero callers → `scripts/archive/`
  (911 lines). Kept, not deleted: their numbers are cited.
- **1b** DONE. This file 1397 → one page. History moved unedited, byte-identical
  (verified by `diff -q` against `git show HEAD:docs/PROGRESS.md`).
- **1c** DONE. `docs/first-hour-audit.md` refreshed in place: install measured at
  79 s, not the 202 s it claimed. The 5.9 GB figure was right.
- **1d** DONE. First-hour read load 19 files / 7134 lines → 3.
- **1e** DONE. Nine ignored `eval/results/` files and the two adjudication
  documents tracked; manifest reconciled.
- **1f** DONE. `.env.example` holding every variable compose requires.
- **1g** REPORTED. CI minutes before and after; slow-jobs-on-PR **proposed, not
  applied**.
- **2** DONE. The test session fixture now fails loudly when it cannot reach a
  database. Before: 1645 skipped, exit 0.
- **3** DONE. `ingest-repo` works in-process. HTTP route unchanged.
- **4** DONE. `remember` reachability documented in `docs/authoring.md`.
- NOT DONE. No measurement was started. Nothing was scored.

## Session 11 — 2026-10-14: reduction round (branch `wip/independent-set`)

- **0a** DONE. Inventory of code, tests, scripts, docs, CI minutes, surfaces.
- **0c** DONE. Manifest reconciled to 32 = 24 `eval/` + 8 `docs/performance/`;
  the two M007.1 adjudication documents tracked, owner-approved. Zero existing
  frozen files modified.
- **1** DONE. The first hour as a stranger. Three stumbles, all unnamed in the
  README: three undocumented required variables failing `docker compose` one at a
  time; `ingest-repo` being an HTTP client to a hard-coded `localhost:8000`; and
  plain Markdown and transcripts ingesting fine while answering *"Nothing was
  invented"*, which reads as "the document was ignored". README text and the
  abstention message fixed; the abstention itself unchanged.
- **2** DONE. Labelled-passage option, dev set only. At the shipped threshold it
  rescues **0** answerable questions, because the arm abstains on zero of them;
  its whole value sits at the 85%-coverage point. 0 of 26 absent questions would
  receive a gold passage at either point.
- NOT FIXED. Running with only `MEMORY_TEST_DATABASE_URL` set skipped all 1645
  tests and exited 0. **Fixed in Session 12, Phase 2.**

## Session 10 — 2026-10-14: stop files vanishing, correct the record (branch `wip/independent-set`)

- **1d** DONE. Audit of 12 ignored non-cache files; 7 documents recovered to
  tracked. `scripts/check_doc_paths.py` and `scripts/check_frozen.py` wired into
  CI and mutation-tested.
- **2a** DONE. A wrong `dataset_hash` claim removed. Two definitions of
  "answerable" differ by exactly one question, `rel-cycle` / `ho-rel-cycle`.
- **2b** DONE. `docs/performance/m0115-gold-audit.json`: gold is present for
  175 of 202.
- **2c** DONE. BM25 tie-break made deterministic. It **costs one question**
  (93→92, 86→85, 67→66). Lab protocol §9.1 now requires Wilson and paired
  bootstrap intervals.
- **2d** DONE. The product-code `MEMORY_TEST_DATABASE_URL` fallback removed.
  Zero test changes.
- **3b** DONE. The harm matrix was **INVALID as a correctness claim**: it counted
  abstaining on an absent question as correct. The real gap is 194−117 = **77**,
  not 43.
- **3a** NOT DONE. The independent question set was never built. Nothing scored.
- **3c** NOT DONE. The passage-option measurement.

## Session 9 — 2026-10-14: reconcile the numbers (branch `wip/independent-set`)

- **0** DONE. 6 CI-only skips root-caused and fixed via `tenant_harness.sync_url()`;
  `scripts/check_skips.py` added and mutation-tested.
- **1** DONE. `check_evidence --require-head --junitxml`; all 12 FK violations
  attributed (12 a fixture defect, now 0; 3 deliberate negative tests).

## Session 8 — 2026-10-13: correct the record, the skips, the evidence (branch `wip/wedge`)

- **0b** DONE. One confusion table, summing to 202, asserted by script.
- **0c** DONE. Arms renamed; BM25 tie-break made deterministic (see Session 10).
- **0e** DONE. The `memory_db` fixture defect fixed.
- **1a–1c** DONE. The six CI-only skips (see Session 9).
- **2b** DONE. The 1625-vs-1619 test-count slip reconciled.

## Sessions 1–6 — 2026-10-11 to 2026-10-12 (branches `wip/wedge`, `wip/platform-readiness`)

CI made green; claims and the retrieval lab built; release safety added. Detail in
the history file.

---

## Standing facts

- **`origin/main` is `07d0f91`, the merge of PR #4.** Neither `wip/wedge` nor
  `wip/independent-set` has ever been merged, and no PR exists for either.
- 13 tags. Latest is v0.9.x. Tagging and 1.0.0 are the owner's decision.
- `eval/` and `docs/performance/baseline-packs.json` are frozen and unchanged
  since `v0.9.0`.
- No test has ever been weakened, skipped or deleted to reach green.