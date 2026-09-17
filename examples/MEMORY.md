# Persistent memory: evolving project demo (M005)

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

## Schema (migration `004_memory`)

| Table | Purpose |
|---|---|
| `memory_documents` | Stable `(corpus_id, path)` identity; survives deletion/restore. ID is `sha256(corpus_id + ':' + path)`, enforced by CHECK. |
| `memory_versions` | Immutable observed source states: raw content, normalized metadata, fingerprint, `observed_at`, predecessor, `NEW`/`MODIFIED`/`RESTORED`/`DELETED` event, version number. |
| `memory_chunks` | Immutable version-specific chunks (text, heading path, order). No embeddings duplicated. |
| `memory_claims` | Explicitly authored claims: key, JSONB value, claim text, optional `valid_from`/`valid_until`, `supersedes_id`, `supersession_basis`. |
| `memory_evidence` | Exact quote plus character offsets into one archived chunk; one evidence row per claim. |
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

## Claim authoring

Two equivalent deterministic forms:

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

- `evidence` must be an exact substring of one archived chunk.
- `claim` text must also appear in that chunk. Provenance is exact-match, not
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

- Claim support is exact text containment; no semantic entailment, no source
  truthfulness verification, no LLM inference anywhere.
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
