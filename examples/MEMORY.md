# Persistent memory: demos and evaluation

Mind Palace provides persistent, portable **corpus memory for AI applications**,
not human or conversational memory. Start with [why memory beyond RAG](WHY_MEMORY.md)
and the [public API contract](MEMORY_API.md).

## Rolled-back A–G evaluation demo (M006)

From the repository root, after installing requirements and `pip install -e .`:

```bash
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace
HF_HUB_OFFLINE=1 python examples/memory_evaluation_demo.py
```

Use a disposable PostgreSQL database with permission to create a schema and
provision the migrations' extensions. No API server, LLM, or model cache is
needed. The demo uses `memory_benchmark_workload()` with **artificial deterministic
fixture embeddings**, not a learned retrieval model. It opens a random schema,
applies migrations there, and rolls back the outer transaction on exit; no real
corpus or report artifact persists. It issues no direct production SQL or commit.
The shared context owns savepoints and rollback. Run it standalone, sequentially.

The script resolves the real `eval/memory_benchmarks.yaml` relative to the checkout,
loads its A–G source directories, and executes all **39 exact authored query cases**
at their declared stages. It prints current/history counts, document changes,
conflict alternatives, exact evidence, saved snapshot stages, and pack budgets.
It does not invent source data, timestamps, or expected results from API output.
Checks remain active under `python -O`. Default workload timeout is 120 seconds;
`--timeout-seconds` accepts 1–600. Timeout requests cancellation and rollback
cleanup; cleanup time is additional, and no PASS is printed on failure.
There is deliberately no `--save`: use the evaluation CLI for report artifacts.

| Stage | New events | Meaning | Live docs / conflict groups |
|---|---|---|---|
| A | 10 NEW | Redis Streams, JWT, Kafka migration target with 6 partitions | 10 / 0 |
| B | 1 MODIFIED | Ownership prose only; `memory_changed=false`, `DOCUMENT_MODIFIED` | 10 / 0 |
| C | 2 MODIFIED | Same-document streaming/auth supersession to Kafka/OIDC | 10 / 0 |
| D | 1 MODIFIED | Kafka partitions 6 → 12 | 10 / 0 |
| E | 1 NEW | Stale JWT incident runbook conflicts with OIDC | 11 / 1 |
| F | 1 DELETED | Runbook tombstoned; exact evidence retained | 10 / 0 |
| G | 1 RESTORED | Identical runbook bytes restore the same semantic conflict pair | 11 / 1 |

Stage directories are overlays with logical paths relative to each stage root;
unchanged copies add no versions. **Deletion runs after ingestion**: F deliberately
contains the runbook file as well as listing it for deletion. The current loader
supports this; the older compatibility warning in the corpus README is stale.
Stage letters resolve to actual captured observation cutoffs, not simulated dates.
The fixture assigns no validity dates. E/G have nine unopposed current claims
plus two conflicting active claims—not eleven unopposed current claims.

Assertions include prose-only modification, supersession, deletion/restoration,
public provenance and exact archived chunk slices, two distinct references for
one storage claim, and the hostile fixture's verbatim text remaining data. Each
A–G replay is captured before later mutations and compared **byte for byte** after
G, without normalizing away clocks for that check. `normalize()` is also exercised
for stage aliases. Packs at 1000/2000/4000/8000/16000 Unicode characters check the
complete envelope, evidence closure, all-or-none conflict alternatives, honest
truncation, and repeatability for the same frozen G state. A whole conflict may
be omitted when it does not fit; that is not a resolved disagreement.

### Recorded demo validation (2026-09-17)

The following command completed successfully in this workspace:

```bash
DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace \
  HF_HUB_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
  venvmp/bin/python examples/memory_evaluation_demo.py
```

**PASS:** 39 authored cases, provenance, frozen replay, and pack safety; the
transaction rolled back. Final stage counts were **17 versions, 16 claims,
17 evidence references, 7 snapshots, 11 live documents, 1 live conflict group**.
Lifecycle totals: 11 NEW, 4 MODIFIED, 1 DELETED, 1 RESTORED. These are demo
observations, not latency percentiles or retrieval/generation quality results.

## Memory evaluation CLI and results

This local CLI runs directly against PostgreSQL, unlike `memory current` and
other remote-only memory CLI commands. From the repository root:

```bash
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace
mkdir -p eval/results
HF_HUB_OFFLINE=1 python -m cli.main eval memory --repetitions 1 \
  --save eval/results/memory-fixture-smoke.json
```

The save path must be new: the CLI refuses overwrite and does **not** create its
parent directory. Omit `--save` for text output only. A one-repetition run is a
functional check, not a percentile measurement. Default repetitions are 20;
valid range is 1–1000. Default scaling sizes are empty (no synthetic sweep).

Manual cached-model measurement, only when the real model is already cached:

```bash
HF_HUB_OFFLINE=1 python -m cli.main eval memory --embeddings cached \
  --repetitions 20 --sizes 100,500,1000 \
  --save eval/results/memory-cached-manual.json
```

Cached mode constructs an independent local-only sentence-transformer using
`EMBEDDING_MODEL` (default `all-MiniLM-L6-v2`), requires 384 dimensions, and fails
if weights are absent. It neither downloads nor silently substitutes a fixture.
The default fixture mode constructs no learned model and makes no provider call;
its artificial deterministic vectors cannot establish retrieval quality.

**Recorded cached evaluation:** tailored lexical cases passed **39/39**; the
natural-language diagnostic was **0/24 correct nonempty current-claim sets**.
The live baseline's **79.17% literal text coverage is not semantic accuracy**.
These reported cached results are separate from the fixture demo run above.
See [WHY_MEMORY.md](WHY_MEMORY.md) for interpretation and the maintainer-authored
[measured M006 report](../docs/evaluation/m006.md) for results and environment.
No unknown performance numbers or 5,000-document measurements are asserted here.

`--generation` explicitly opts in to A: a memory-pack prompt through `LLMService`,
and B: the actual default `/api/query/ask` route captured at the relevant live
stage (B requires cached embeddings). Both need explicit `LLM_PROVIDER`; configured
providers may require secrets, receive corpus text, and incur cost. Run only as a
standalone evaluation: the B adapter temporarily binds route globals and must not
share a process with serving traffic. Judging is literal checks, not an LLM judge
or proof of correct temporal framing. **Generation A/B: NOT RUN in the recorded
evaluation because no LLM service was available. M006 is not declared complete
or deployment-ready.**

Detailed scoring, timing, normalization, and gate semantics are in
[MEMORY_API.md](MEMORY_API.md#reusable-evaluation-workload-and-cli-semantics).

## M006.5 question retrieval: implemented and evaluated

M006.5 adds `POST /api/memory/query`, SDK `memory.query`, MCP `memory_query`, and
remote CLI `memory query`. Existing lexical `current`, `history`, `changes`, and
`pack` behavior is unchanged. Default `/api/query/ask` remains live RAG; explicit
`mode="memory"` now sends its `question` through query with automatic intent before
optional LLM generation. [REST, SDK, MCP, and CLI examples](MEMORY_API.md#question-retrieval-m0065).

```bash
python -m cli.main memory query --corpus my-corpus \
  --query "What carries Dispatch events now?" --intent current --budget 8000
```

Use an existing authored corpus. The question belongs in **`query`** and is
mandatory/nonempty. `intent` accepts `auto` (default), `current`, `historical`,
`temporal`, `change`, `conflict`, `provenance`. A date-only question cutoff means
midnight UTC observation time. Stage names require a real `as_of` or `snapshot_id`
from the caller; “before Kafka” does not infer a precise event boundary. Structured
time selectors take precedence, and temporal intent requires a cutoff/snapshot.

The query service loads the existing configured MiniLM model on demand and can
reuse its in-process singleton. Question and archived claim/text-key-path vectors
are **transient, recomputed per query, with no persistent cache/index**. The
0.30 cosine floor and 0.90 top-score band are relevance heuristics; same-authored-key
score propagation is not authority. Status/time projection, exact provenance,
and complete conflict groups still come from the memory core. Determinism needs
the same state, clocks, request, model, and runtime, not merely repeated words.
**No M006.5 schema migration** is needed; migration 005 below is the earlier
multiple-evidence extension, not a query migration.

### Recorded retrieval measurements

The saved `eval/results/m0065-final.json` contains **17/24** exact original
current-claim sets versus the earlier lexical **0/24**, and **30/40** exact added
question cases. Added category results are current **13/19**, historical **4/4**,
temporal **3/6**, change **3/4**, conflict **4/4**, provenance **3/3**. All **17**
failures and unchanged labels are documented in the
[full M006.5 report](../docs/evaluation/m0065.md), including the potential
ownership-clarification empty-history label issue.

There were **0 safety failures over 264 output cases** (64 main + 200 budget
outputs) and no execution failures. Budget correctness and retrieval completeness
are distinct: exact sets at 1000/2000/4000/8000/16000 characters were
**3/40, 3/40, 26/40, 30/40, 30/40**. A passing safety/execution gate does not certify
relevance; `truncated=false` does not prove no relevant claim was missed.

One warmed fixed query, 20 repetitions: **124.2/131.3 ms p50/p95**, versus
**4.0/5.1 ms** preembedded live search. Query includes per-call embedding and
archive projection/packing; live search excludes query embedding, so this is
**not a fair end-to-end latency comparison**. These are small-fixture local
measurements, not production latency promises.

### Reproduce M006.5 separately

With dependencies installed and `all-MiniLM-L6-v2` already cached, use the existing
local PostgreSQL instance on port 5433 (or your configured disposable database):

```bash
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace
mkdir -p eval/results
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
  venvmp/bin/python -m api.services.memory_query_benchmark --end-to-end \
  --save eval/results/m0065-local-rerun.json
```

This is a standalone, rollback-only database workload, not a REST-server benchmark
or a generation run. It uses the cached real model with no fixture fallback.
Unlike `cli.main eval memory --save`, this module **overwrites** its target and
does not create the parent; use a fresh filename to preserve recorded reports.
Omit `--end-to-end` (with a separate save path) for the ranking experiment only.

**Status:** M006.5 implemented/evaluated with remaining relevance limits;
**generation NOT RUN**. The maintainer reports the current full suite at **932
passed**; no-DB tests and lint are pending maintainer validation. These are not
new validation runs performed for this documentation update. This does not
establish a universally reliable question service or production readiness.
The [M006 report](../docs/evaluation/m006.md) and its incomplete generation/scaling
findings remain unchanged.

## Persistent evolving-project demo (M005)

The older SDK example below is still useful when you deliberately want a
committed corpus and response artifacts. Its persistence and embedding behavior
are different from the rolled-back A–G demo above.

Mind Palace retains evidence-backed source assertions as a project changes. It
records what a document asserted and when it was observed; it does not establish
perfect truth. Public SDK, REST, CLI, and MCP memory operations now consume the
M004 versioned foundation. See [MEMORY_API.md](MEMORY_API.md) for the implemented
contract and adapter limitations. `/search` and default RAG `/ask` still use the
live index; explicit memory operations read the archive.

## Run the evolving demo

From the repository root, with requirements and the editable package installed:

```bash
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head
python examples/memory_demo.py
```

Port 5433 is the optional local demo database; use your actual database port
(the default compose quick start uses 5432). The script requires `DATABASE_URL`
explicitly and generates a new `m005-demo-<uuid>` corpus by default. To choose a
name yourself, use a **new, dedicated** name:

```bash
python examples/memory_demo.py --corpus m005-dispatch-my-first-run
```

All existing names are refused, including nonempty corpora and empty live indexes
with retained history. There is no overwrite/delete flag. Do not share the demo
corpus with concurrent writers. The corpus reservation is create-only via corpus
management because the SDK has no create-only method; every sync and memory
operation uses the named local SDK. Sources are copied to a temporary staging
folder; checked-in Markdown is never edited during a run.

The demo calls no external paid LLM or `/ask`. Named local SDK initialization and
sync do use sentence-transformer embeddings (`all-MiniLM-L6-v2` by default).
Hugging Face weights may already be cached or may need a download. For a cached,
network-free run, set `HF_HUB_OFFLINE=1`; this fails if required weights are absent.

### What is exercised

The [Dispatch fixtures](evolving-project/README.md) describe an order-event service:

| Step | Expected streaming state | Other assertions |
|---|---|---|
| A | Redis Streams | Two active auth documents conflict; neither wins |
| A unchanged | Redis Streams | No extra archive version |
| B | Kafka | Same-key/same-document supersession of A |
| C | Kafka managed | Same-key/same-document supersession of B |
| deletion | No current streaming claim | Tombstone, retained historical evidence |
| restoration | Kafka managed | Stable document identity; new version after tombstone |

These are **expected assertions, not pre-recorded measurements**. The script uses
`query="streaming"` because matching is lexical AND and all architecture claims
share `architecture.streaming`. It does not promise natural-language recall.
Auth documents use `security.auth` with `OIDC` and `API key`; different-document
claims never supersede one another merely because one was observed later.

A snapshot captured at A is replayed through a separate SDK transaction immediately
and again after restoration. Both replay and as-of must still show A's assertion;
full canonical snapshot replay must remain identical, with validity fixed at the
saved cutoff. Exact authored body quotes, public provenance links, lifecycle and
supersession chains, and complete-envelope pack bounds are checked. The public
API exposes quote offsets, not full chunks; the database enforces the archived
chunk slice, while the demo checks exact authored body text and public links.

### Outputs and measurements

By default `examples/evolving-project/runs/<corpus>/` receives canonical SDK JSON
for current/history/changes/evidence/as-of/snapshot/replay/pack plus `report.json`.
Use `--output /path/to/a/new/directory` to select another new output location;
existing output directories are refused. `runs/` is ignored by Git. Reports include
actual wall-clock call durations, SDK sync counts/timing, response character
counts, selected values, evidence/conflict/change counts, observation timestamps,
and IDs. These are single-run observations, **not a benchmark or readiness gate**.
The script prints `PASS` only after every assertion succeeds. Failures leave any
already committed corpus data and partial reports intact; use a new name to retry.
The temporary working tree is cleaned up, but the corpus intentionally persists.

Pack budgets count Unicode characters of the complete canonical envelope, not
tokens or UTF-8 bytes. The demo checks 1024, 8000, and 128000; the contract permits
512–128000, but even an in-range budget can fail if the envelope itself is too big.
Claims and conflict sides retain their evidence atomically; quotes are not sliced.

No authentication is implemented; corpus namespaces are not access control.
M004 append-only guards currently block deletion of archived corpora, so there is
no corpus cleanup/erasure promise. Use a disposable database if full teardown is
required. Do not expose this deployment as an authenticated multi-tenant service.

### Validation in this workspace

Offline validation: four throwaway checks passed (all five fixtures through the
actual parser/chunker/claim validator, explicit assertion behavior, rejection of
dangling evidence, and refusal of an existing output directory before database
access). Black, Flake8, demo/CLI help, and `git diff --check` passed. No repository
test, service, or interface files were changed for this demo.

An end-to-end attempt using a unique `m005-pytest-<uuid>` corpus and
`DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace` with
`HF_HUB_OFFLINE=1` failed before corpus creation: connection refused on both
localhost IPv4 and IPv6. **No successful persistent demo run or stage measurements
are recorded here.** With that database running and migrated, the exact direct
command for a fresh UUID-named run in this workspace is:

```bash
DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace \
  HF_HUB_OFFLINE=1 venvmp/bin/python examples/memory_demo.py
```

Omit `HF_HUB_OFFLINE=1` only if model downloads are acceptable and needed.

## M004 foundation reference

## Schema (foundation `004_memory`, extended by `005_multiple_evidence`)

| Table | Purpose |
|---|---|
| `memory_documents` | Stable `(corpus_id, path)` identity; survives deletion/restore. ID is `sha256(corpus_id + ':' + path)`, enforced by CHECK. |
| `memory_versions` | Immutable observed source states: raw content, normalized metadata, fingerprint, `observed_at`, predecessor, `NEW`/`MODIFIED`/`RESTORED`/`DELETED` event, version number. |
| `memory_chunks` | Immutable version-specific chunks (text, heading path, order). No embeddings duplicated. |
| `memory_claims` | Explicitly authored claims: key, JSONB value, claim text, optional `valid_from`/`valid_until`, `supersedes_id`, `supersession_basis`. |
| `memory_evidence` | Exact quotes plus character offsets into archived chunks; migration 004 allowed one row per claim, migration 005 permits multiple distinct references. |
| `memory_snapshots`, `memory_snapshot_versions` | Reproducible point-in-time references to immutable versions. |

Relationships (all composite, corpus-scoped):

```text
corpora → memory_documents → memory_versions → memory_chunks
                                  ↓
                            memory_claims → memory_evidence → memory_chunks
                                  ↓ supersedes_id (same corpus, same document,
                                    immediate predecessor version only)
```

Database-enforced invariants (triggers/constraints, not just application checks):

- Archive tables reject `UPDATE`/`DELETE`/`TRUNCATE` (append-only).
- Evidence quote must exactly match the archived chunk at the stored offsets.
- A claim without evidence fails at transaction commit (deferred constraint trigger).
- Supersession must point to a claim in the immediate predecessor version of the
  same document; self-supersession and forward/cyclic edges are rejected.
- Version predecessors must be the actual previous version with a legal lifecycle
  transition (`NEW` root; `MODIFIED`/`DELETED` after active; `RESTORED` after `DELETED`)
  and strictly increasing observation time.
- `valid_until >= valid_from`; tombstone versions carry no content.
- Existing documents are backfilled with memory identities only — no invented
  versions, claims, or timestamps.

## Migration `005_multiple_evidence`

The released `004_memory` schema enforced `UNIQUE(corpus_id, claim_id)`, so it
could not represent two references for one claim. Two agreeing claims from two
documents were not a substitute. Migration `005_multiple_evidence` replaces that
constraint with unique `(corpus_id, claim_id, chunk_id, start_offset, end_offset)`
references and a claim lookup index. Existing corpus/version foreign keys,
exact-quote validation, deferred evidence requirements, and append-only guards
remain. The API response is still schema version 1 with plural `evidence_ids`.

A nonempty evidence list now authors all references atomically with a version.
The original evidence string remains supported with its prior identity behavior;
existing rows are not rewritten. Every supporting chunk must contain the claim
text as well as its exact quote. Duplicate or empty quotes are rejected. List
order remains in normalized metadata/fingerprints; reference resolution sorts
quotes, choosing the first occurrence in the lowest-order matching chunk.

Snapshot capture freezes evidence insertion for its referenced versions. A
BEFORE INSERT trigger uses the same corpus advisory lock and rejects new evidence
for any snapshotted version, so append-only inserts cannot mutate saved replay.
There is no public late-evidence append operation. Downgrade locks the evidence
table and **refuses if any claim has more than one reference**, before DDL; it
never discards provenance to restore the old uniqueness constraint.

The actual `data/storage.md` fixture authors one `data.primary` claim supported
by two quotes in two chunks. That file is unchanged across A–G: two references,
not two claims or extra lifecycle versions.

## Claim authoring

Two deterministic forms (string shorthand and structured claims):

```yaml
---
title: Architecture
claims:
  - "The application uses Kafka."          # string form: key derived from content
  - key: architecture.database             # structured form
    value: PostgreSQL
    claim: The primary database is PostgreSQL.
    evidence: The primary database is PostgreSQL.
    valid_from: "2026-08-11"               # optional; NULL = unknown
---
```

Validation (no LLM anywhere):

- `evidence` is a nonempty exact quote string or nonempty list of distinct quote
  strings. Each quote must be an exact substring of an archived chunk.
- `claim` text must also appear in every supporting chunk. Provenance is exact-match, not
  semantic entailment: the source author is responsible for truthfulness.
- One claim per key per version; duplicate keys are rejected.
- Rejection raises `ClaimValidationError` (path, content fingerprint, reason)
  before any write; ingestion fails with no partial state.

Supersession is established only by deterministic source-version replacement:
same key in the next version of the same document (`same_document_key`), or a
single string claim replaced by another (`single_claim_replacement`). A newer
claim in a *different* document never supersedes; differing values for the same
key across active documents are reported as conflicts. Dropping a claim retires
it without inventing supersession.

## Temporal semantics

- `observed_at` (DB `clock_timestamp()` under the corpus lock): when Mind Palace
  observed the version. `as_of` filters on this, inclusively.
- `valid_from`/`valid_until`: authored valid time. NULL means unknown — never
  fabricated from ingestion time.
- Authored validity is evaluated as `[valid_from, valid_until)`, with missing
  bounds unknown. Out-of-window claims are excluded from current. Public reads
  fix validity once per response (`valid_at`, otherwise `as_of`, otherwise now).
  Snapshot replay always uses the saved `as_of` for validity as well as observation.
- Query status: `CURRENT` (latest active version, inside validity window),
  `SUPERSEDED` (explicitly replaced), `CONFLICTING` (retained with evidence, never
  silently resolved, excluded from current), `UNCERTAIN` (outside authored validity
  or non-current without an explicit replacement). "Current" means latest observed
  source assertion, not independently verified present-day truth.

## Lifecycle

| Event | Live index | Archive |
|---|---|---|
| NEW | document + chunks inserted | version 1 + chunks + claims + evidence |
| UNCHANGED | untouched | untouched (same fingerprint → no new version) |
| MODIFIED | chunks replaced | new version, predecessor linked, same-key claims supersede |
| DELETED | document/chunks/manifest removed | tombstone version appended; history and evidence retained |
| RESTORED | document + chunks reinserted | new version linked to the tombstone; identity preserved |

Change detection covers raw content plus all normalized metadata (including
claims), not live IDs or rechunking. Renames are new identities; no MOVED state
is inferred. Re-ingesting identical content is idempotent.

## Transaction boundary

Every document ingestion runs in one caller-owned transaction under a
transaction-scoped `pg_advisory_xact_lock` keyed by corpus: parse → validate
claims → embed → upsert document → replace chunks → archive version/claims/
evidence → update manifest. Any failure (including an invalid claim or an
injected failure after archive insertion) rolls back live rows, manifest, and
archive together. Deletion sync records tombstones in the same locked
transaction. Memory auto-enables per document when `memory_enabled=True`, the
source authors `claims:`, or the corpus already has archived history — so
ordinary ingestion cannot silently bypass recorded history once a corpus has any.

## Usage

```python
import asyncio
from api.services.ingestion import IngestionService
from api.services.corpora import get_or_create_corpus
from api.services.db import session_scope
from api.services import memory

async def main():
    async with session_scope() as db:
        corpus = await get_or_create_corpus(db, "memory-demo")
    await IngestionService(memory_enabled=True).sync_repo("./docs", corpus["id"])
    async with session_scope() as db:
        async with db.begin():
            print(await memory.query(db, corpus["id"], query="database"))
            print(await memory.history(db, corpus["id"]))      # versions + claims + evidence
            print(await memory.changes(db, corpus["id"]))      # before/after per event
            print(await memory.evidence(db, corpus["id"]))
            snap = await memory.snapshot(db, corpus["id"])
        # db.begin() commits the caller-owned snapshot transaction on success.
    async with session_scope() as db:
        print(await memory.replay_snapshot(db, corpus["id"], snap["id"]))

asyncio.run(main())
```

Snapshots capture immutable version references under the writer lock; identical
cutoffs return the same snapshot. `replay_snapshot` resolves only captured
references and evaluates validity at the saved `as_of`, so later changes or wall
clock time cannot alter a replay. Future cutoffs are rejected. The low-level
service does not commit; use an explicit transaction as above. The public local
SDK instead calls `memory_public.execute`, which owns and commits the transaction.

## Known limitations

- Claim support is exact text containment; no semantic entailment or source
  truthfulness verification. No LLM extracts/adjudicates claims. Opt-in memory
  `/ask` generation is separate and remains unmeasured.
- M006.5 question relevance is heuristic, with related-concept confusion,
  multi-part omissions, and false positives on unrelated questions. Existing
  lexical operations are unchanged; no universal question reliability is claimed.
- Conflicts are same-key differing values across active documents — not general
  contradiction detection.
- No MOVED/SUPERSEDED lifecycle states; renames are new identities.
- Validity bounds do filter current; unknown validity is not proof of truth.
- Whole-directory sync is per-document atomic, not one atomic directory snapshot.
- M004 append-only guards currently block corpus deletion when archive rows
  exist. Archive erasure/retention is unresolved; deleting source files through
  sync appends tombstones, not archive erasure.
- The archive is append-only through normal DML, not tamper-proof against the
  database owner.

## Historical M004 validation record

The following is the pre-existing M004 test record, not an M005 demo run or a
fresh validation claim for the current working tree.

```bash
MEMORY_TEST_DATABASE_URL=postgresql://mpadmin:secret@localhost:5433/mindpalace \
  venvmp/bin/python -m pytest tests/test_memory_ingestion.py tests/test_memory_core.py -v
```

Tests run in isolated random schemas inside one rolled-back transaction; only
embeddings are a deterministic test double. Validation recorded for this
milestone: targeted memory suite **43 passed**; full suite on a fresh database
**201 passed, 0 skipped**; without a database **146 passed, 55 skipped**; fresh
migrations 001→004 plus repeated `upgrade head` passed; black/flake8 clean;
`git diff --check` clean. Warnings are pre-existing asyncio-marked sync tests
and connection-cleanup notices, unchanged by this work.
