# Memory model

Scope: the append-only archive behind every ordinary memory operation, as
implemented at `bb8ce0b` (M009 released as `v0.6.0` at
`033c1484dca53fbb40dbf82904aacf5f2834142a`). The M009 operational feed is a
separate read contract over the same rows and is described in
[`docs/operations.md`](../operations.md).

## What is stored

```text
corpus → document identity → observed version → authored claim → exact evidence
                                  ↓                    ↓
                        lifecycle / snapshots   supersession / conflicts
```

The corpus is the namespace boundary. Document identity is
`sha256(corpus_id + ":" + path)` (`api/services/memory.py`,
`memory_document_id`), so the same relative path can exist independently in two
corpora, and the identity survives deletion and restoration. A rename creates a
new identity; no MOVED state is inferred.

A version records one observed source state: raw content, parsed metadata,
chunks, a predecessor link, a version number, a lifecycle event, and the
database `observed_at` time. Versions are immutable. The
`migrations/versions/004_memory.py` statement-level trigger raises
`memory archive is append-only` on `UPDATE`, `DELETE`, or `TRUNCATE`, so the
guarantee holds for direct SQL writers and not only for the service.

A claim is authored by the source document, never extracted by a model. Each
version may author at most one claim per key. A claim carries a key, a JSON
value, prose, optional `[valid_from, valid_until)` bounds, and one or more exact
quotes.

Evidence is a quote plus its character offsets inside a specific archived chunk,
together with the corpus, version, claim, and chunk identifiers it points at.

## Lifecycle

| Event | Live index | Archive |
|---|---|---|
| `NEW` | document and chunks inserted | version 1, chunks, claims, evidence |
| `UNCHANGED` | untouched | untouched, because the fingerprint matched |
| `MODIFIED` | stale chunks replaced | new version, predecessor linked, same-key claims supersede |
| `DELETED` | live rows and manifest removed | tombstone version appended |
| `RESTORED` | document and chunks reinserted | new version linked to the tombstone |

Change detection hashes raw content plus all normalized metadata, including
claims (`memory._hash(content, metadata)`), not live identifiers or rechunking
(`record_version`). A prose-only edit therefore creates a version with
`memory_changed=false`: the source changed, the assertion did not.

Deletion is append-only. `record_deletion` writes a `DELETED` version and never
touches live rows itself; ingestion removes the live entries in the same
transaction (`api/services/ingestion.py`, `_delete_stale_paths`). The tombstone
keeps the claim history and the evidence quote readable.

## Status resolution

`memory._query_result` assigns one status per claim:

- `SUPERSEDED` when another claim names it in `supersedes_id`.
- `CONFLICTING` when two active claims share a key, come from different
  documents, and carry different canonical JSON values. Conflicting claims are
  dropped from `current_memories` and kept in `conflicts` with their evidence.
- `CURRENT` when the claim belongs to the latest non-deleted version of its
  document at the requested cutoff and sits inside its authored validity window.
- `UNCERTAIN` otherwise, which includes any claim whose version is not the
  current one, and any claim outside its window.

`CURRENT` means the latest in-window source assertion that no other active
source contradicts. It is not verified present-day truth. The repository keeps
retrieval, relevance, applicability, and authority separate: a newer source is
not automatically valid, and a similar source is not automatically
authoritative.

Supersession is only ever recorded inside one document lineage.
`004_memory.py` adds a trigger requiring a supersession edge to point at a claim
in the immediate predecessor version of the same document, and
`(supersedes_id IS NULL) = (supersession_basis IS NULL)` with basis
`same_document_key` or `single_claim_replacement`. Because rows are append-only
and edges point backwards one version, cycles are impossible.

## Conflict closure

A path or query selector never hides the other side. `memory_public.project`
and `memory_public.bounded_pack` both close over connected conflict groups until
the set stops growing, and `bounded_pack` closes over conflicts introduced by
before/after change sets. Alternatives therefore appear together or not at all.

## Snapshots and replay

`snapshot()` runs under the corpus writer lock, rejects a cutoff later than the
database clock, and records every version with `observed_at <= as_of`, including
tombstones, so replay preserves historical and superseded claims as well as the
current state. The snapshot identifier is
`sha256("snapshot", corpus_id, cutoff.isoformat())`, so an identical cutoff
returns the existing snapshot instead of creating a second one. Replay fixes
both the observation clock and the validity clock to the saved `as_of`
(`memory_public.execute_in_session`).

A deferred constraint trigger in `004_memory.py` requires every claim to have at
least one evidence row in the same transaction. `005_multiple_evidence` widens
that to several distinct references per claim and adds a trigger that refuses
evidence inserts into a version already captured by a snapshot, taking the same
advisory lock as `snapshot()` so the check cannot race.

The post-release follow-up in this audit adds migration
`migrations/versions/006_snapshot_membership_seal.py`. It records a seal per
snapshot, rejects later membership inserts for sealed snapshots, and makes the
seal a deferred requirement for committing a snapshot. The migration backfills
snapshots created under 004 and 005. The service writes the seal only when
`memory_snapshot_seals` exists, so the 004/005 contract still holds in an
isolated schema; `tests/test_memory_snapshot_seal.py` covers both shapes.

The migration is not part of the immutable `v0.6.0` release. The focused
snapshot-seal suite passed against the local PostgreSQL 15.4 service after the
follow-up was added.

## Bounded output

`memory_public.bounded_pack` selects greedily and stably. The budget counts
Unicode characters of the complete canonical JSON envelope, including state,
claims, changes, evidence, sources, conflicts, and the truncation flag, not
tokens or bytes. The truncation flag is reserved before selection, so flipping it
cannot overflow the budget. A budget that is in range but too small for the
empty envelope is `422 invalid_budget`, and a budget outside 512 to 128000 is
rejected at request validation. `truncated=false` is set only when nothing was
dropped.

Given the same archive state, clocks, selectors, and budget, a canonical lexical
pack is identical. A query pack also needs the same question, intent, embedding
model, and runtime. `truncated=true` warns that an omission is not evidence of
absence.

## Interfaces

Ordinary memory operations share one typed boundary
(`api/services/memory_public.py`) reached by REST (`api/routers/memory.py`), the
Python SDK (`mindpalace_sdk.py`), MCP (`mcp_server.py`), and the remote CLI
(`cli/memory.py`). Adapters translate transport and errors; they do not
implement archive policy. Operations are `query`, `current`, `history`,
`changes`, `evidence`, `as-of`, `snapshot`, `replay`, and `pack`.

The M009 feed is deliberately not one of them. It is a `GET` over
`memory_versions` exposed through REST, SDK, and CLI only. The reason is
recorded in [`docs/operations.md`](../operations.md): the feed has its own
cursor, page-size, and operational error contract, and adding it to MCP would
create a second unversioned schema on an unauthenticated tool surface.

## Transaction boundary

Writers take a transaction-scoped `pg_advisory_xact_lock` keyed by corpus
(`memory.lock_corpus`) before any live or archive write, and hold it through
`record_version` or `record_deletion`. Ingestion parses, validates authored
claims, embeds, upserts the document, replaces chunks, appends the archive
version, and updates the manifest in one transaction, so a failure rolls back
live rows and archive rows together. Reads load one coherent MVCC view.

Memory archiving turns on per corpus and never silently switches off: a corpus
with any archived version keeps archiving on subsequent writes, and a document
that authors `claims:` is archived even if the flag is off
(`api/services/ingestion.py`, `_ingest_content`).

## Known boundaries

- Ordinary public reads load the whole time-scoped archive in one statement
  before applying selectors, and are not paginated. The response budget does not
  bound read cost.
- Conflicts require the same exact key with different canonical values across
  active documents. There is no general contradiction detection and no semantic
  entailment check.
- Corpus namespaces are not authorization. There is no built-in authentication
  or per-corpus access control.
- Archive retention and erasure are unresolved. Ordinary DML cannot erase
  archive rows, and corpus deletion fails while archive rows exist.
