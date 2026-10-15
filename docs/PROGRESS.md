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

## Design note: `memory_remember` as an MCP tool

**Owner approval to IMPLEMENT: _blank in the brief._** Nothing below has been
built. This is the note the brief asks for when the answer is NO or absent, and
an absent answer is not an approval.

### The finding that motivates it

An agent cannot write a fact today. The 15 MCP tools are `context`,
`list_corpora`, `memory_as_of`, `memory_changes`, `memory_current`,
`memory_evidence`, `memory_explain`, `memory_history`, `memory_pack`,
`memory_query`, `memory_recall`, `memory_replay`, `memory_snapshot`, `search`,
`sync`. `remember` is reachable from the CLI, the SDK and REST, and from no MCP
client. The only MCP write that carries a claim is `sync(corpus, path)`, which
needs a file the agent has to author on disk with a `claims:` block.

### Tool and arguments

Mirror the SDK exactly, so there is no second argument shape to keep in step:

```
memory_remember(statement: str, corpus: str | None = None,
                key: str | None = None, file: str | None = None)
```

`statement`, `corpus`, `key`, `file` — the four parameters of
`MindPalace.remember` at `mindpalace_sdk.py:874`. Returns `MemoryResult`, the
same dataclass `sync` already returns, serialised as the tool's structured
output. `file` is read by the server, not by the agent, and is subject to
`require_permitted` like any other ingest path.

### Role and credential

Same as every other MCP tool: the credential is
`MIND_PALACE_MCP_CREDENTIAL`, resolved by the existing `_caller()` /
`_client(corpus)` machinery, and the caller needs the `read_write` role. No new
credential, no new scope table, no new env var. A credential with only read
rights must be refused **by the existing authorization path, not by a check in
the tool** — a tool-level check would be the first thing to drift.

### Idempotency

Already correct and worth keeping as the contract. Remembering an identical
statement returns `unchanged=True` and the same `version_id`; remembering the
same `key` with new text supersedes. So an agent retrying a failed call cannot
duplicate, and an agent looping cannot grow the corpus. A tool that did not
preserve this would be a new footgun, not a new capability.

### The exact-quote rejection

**It does not apply here, and pretending otherwise would be the real design
error.** `remember` takes the statement and generates its own exact-substring
evidence from it, so there is no separate `evidence` field to mismatch — the
failure mode that bites document authors does not exist on this path.

Two exceptions, and both must be returned to the agent as data, not as a tool
error:
- `file=` with a statement that is not a substring of that file. Same rejection
  the ingest path raises, same wording: *"evidence must be an exact substring of
  an archived chunk"*. An agent needs the document version in the message, or it
  cannot work out which file it mis-quoted.
- a `key` that fails `validate_corpus_name` / corpus resolution. Returned as the
  structured error the other tools already return.

A rejection must come back as a normal tool result carrying the reason, never as
a JSON-RPC protocol error. A protocol error looks like a broken server and
invites a retry; a structured refusal tells the model what to change.

### Untrusted source text

The sharp edge. `statement` is attacker-controllable in the threat model where a
model has ingested a document and is asked to remember something from it. Three
consequences:

1. **The statement is data, not instruction.** It must never be interpolated
   into anything that changes behaviour. This is already true of every write
   path; it is named here because an MCP tool is called *by a model reading
   text*, which is the one place where that assumption is load-bearing.
2. **No secret-free claim is a safe claim.** Storing a string from an untrusted
   document makes it retrievable by anyone who can read the corpus, and
   retrievable is durable. There is no sanitisation that makes this safe; the
   only mitigation is that the caller's credential decides.
3. **`file=` is the sharper one.** An agent can be talked into `file="/etc/..."`.
   `require_permitted` already refuses paths outside `MIND_PALACE_INGEST_ROOTS`
   and its message deliberately does not name the allowed roots. That refusal
   must be surfaced verbatim and must not be enriched for the MCP case.

The honest position: this tool makes it *easier* for a model to write to memory,
and the system's answer to bad memory is `history` and erasure, not prevention.
That trade should be an explicit owner decision, not a side effect.

### Tests needed

- the tool appears in `tools/list` and every tool has a description;
- `statement` alone writes a claim whose evidence is its own exact substring;
- repeating it returns `unchanged=True` and the same `version_id`;
- a second `key` supersedes and `memory_history` shows both;
- `file=` outside the ingest roots is refused, and the message names no root;
- `file=` with a non-substring statement returns the rejection as a *result*;
- a read-only credential is refused;
- an untrusted string containing instruction-shaped text is stored verbatim and
  changes no behaviour.

### What a test would already break

`.github/workflows/ci.yml:875` asserts `len(tools) == 15`. That is the **only**
place the tool list is pinned — `tests/test_mcp_server.py` has three tests and
none enumerates the tools. So a sixteenth tool breaks CI in one line, in the same
job this session wired the clean-room script into, and that assertion should move
into `tests/test_mcp_server.py` first so the count lives next to the server.

### Diff size estimate

| | lines |
|---|---:|
| `@mcp.tool` + docstring + `_execute_memory("remember", ...)` | ~30 |
| `MemoryRequest` reuse or a small write-shaped request model | ~10 |
| authorization on the write path, if not already shared | ~15 |
| tests | ~90 |
| `ci.yml` `15` → `16` | 1 |
| **total** | **~145** |

Above the ~120 line bar in the brief, before any argument-shape validation worth
writing. The honest answer to "under ~120?" is **no**, so even with approval this
wants its own round rather than being bolted on.
---

## Standing facts

- **`origin/main` is `07d0f91`, the merge of PR #4.** Neither `wip/wedge` nor
  `wip/independent-set` has ever been merged, and no PR exists for either.
- 13 tags. Latest is v0.9.x. Tagging and 1.0.0 are the owner's decision.
- `eval/` and `docs/performance/baseline-packs.json` are frozen and unchanged
  since `v0.9.0`.
- No test has ever been weakened, skipped or deleted to reach green.