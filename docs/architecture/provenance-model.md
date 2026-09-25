# Provenance model

Every claim Mind Palace returns is anchored to text that was archived, and the
anchor is enforced by the database rather than by application code. Evidence:
`migrations/versions/004_memory.py`,
`migrations/versions/005_multiple_evidence.py`, `api/services/memory.py`,
`api/services/memory_public.py`, and `api/models/memory.py`.

## What an evidence row is

`memory_evidence` stores, per reference:

- the claim it supports (`claim_id`), the version it was archived under
  (`version_id`), and the chunk containing the text (`chunk_id`);
- the quote itself, non-empty;
- `start_offset` and `end_offset`, character positions in the chunk;
- the corpus, and an identifier derived from the claim and the span it covers.

The `Evidence` contract in `api/models/memory.py` adds the projection fields a
caller sees: `document_id`, `path`, `source_hash` (the version fingerprint),
`heading`, `text`, and `observed_at`.

## What the database enforces

Three mechanisms in `004_memory.py` do the work:

1. **Exact match on insert.** A `BEFORE INSERT` trigger compares
   `substring(text FROM start_offset + 1 FOR char_length(quote))` against the
   quote for the named chunk and raises `23514` when they differ. A writer
   cannot claim a quote the archived chunk does not contain.
2. **Offset arithmetic.** A table check requires
   `end_offset = start_offset + char_length(quote)`, so offsets cannot describe a
   different span than the quote they accompany.
3. **Evidence is mandatory.** A `DEFERRABLE INITIALLY DEFERRED` constraint
   trigger on `memory_claims` checks at commit time that the claim has at least
   one evidence row. Deferring it lets the claim and its evidence land in one
   transaction, which is how ingestion writes them. A claim that ends up without
   evidence fails to commit.

Because the first check is a trigger rather than application validation, a
direct SQL insert is held to the same standard as the service.

## What the service validates before writing

`memory._prepare` rejects a source before any write when:

- a claim has no key, no prose, or an empty key;
- evidence is missing, empty, or a list containing duplicates;
- a quote is not an exact substring of some archived chunk;
- the claim text does not appear in the chunk that supports it, with the message
  that semantic inference is not supported;
- one version authors more than one claim for the same key;
- `valid_until` precedes `valid_from`.

`ClaimValidationError` carries the path and the version fingerprint, not the
source text, so a rejection message does not echo document content into logs.

References resolve in sorted quote order, taking the first occurrence in the
lowest-order matching chunk. Every supporting chunk must also contain the claim
text. The list order of authored quotes is preserved in metadata and therefore
in the version fingerprint, so reordering quotes is a real change and produces a
new version.

## Multiple references

`004_memory.py` allowed exactly one evidence row per claim. The A-G evaluation
fixture's `data/storage.md` authors one claim with two supporting quotes in two
different chunks, which that constraint could not represent.

`005_multiple_evidence` replaces the unique constraint with
`uq_memory_evidence_reference (corpus_id, claim_id, chunk_id, start_offset,
end_offset)`, so a claim may carry several distinct references while duplicate
references remain impossible. It adds
`idx_memory_evidence_claim (corpus_id, claim_id)` so a claim-scoped evidence
lookup has a direct index path.

Its downgrade refuses rather than discards: it takes
`LOCK TABLE memory_evidence IN ACCESS EXCLUSIVE MODE` and raises if any claim has
more than one reference, so a downgrade cannot silently drop provenance.

## Evidence and snapshots

`005_multiple_evidence` also freezes evidence against captured versions. A
`BEFORE INSERT` trigger on `memory_evidence` takes the same
`pg_advisory_xact_lock` that `memory.snapshot()` uses, then raises `23514` if
the target version already appears in `memory_snapshot_versions`. Because both
paths take that lock first, a concurrent insert cannot slip between the check
and the snapshot. Append-only evidence cannot therefore change what a later
replay returns.

The post-release follow-up in this audit adds
`migrations/versions/006_snapshot_membership_seal.py`, which extends the same
idea from evidence to snapshot membership: a seal row per snapshot, an
`AFTER INSERT` statement trigger with a transition table that rejects new
membership for a sealed snapshot, and a deferred constraint trigger requiring
the seal before a snapshot can commit. It backfills seals for snapshots created
under 004 and 005 and refuses to change their references. It is not part of the
immutable `v0.6.0` release. Its five database-backed seal tests passed against
the local PostgreSQL 15.4 service. `memory.snapshot()` checks `to_regclass` before
writing the seal, and `tests/test_memory_snapshot_seal.py` covers the sealed,
pre-seal, unsealed, and downgrade/re-upgrade cases.

## How provenance reaches a response

`memory_public.project` builds an `Evidence` record for every evidence row in
the resolved archive, indexes them by claim, and refuses to emit a claim that
has no evidence, raising `503 memory_unavailable` with "Archived claim has
incomplete provenance" rather than returning an unattributed assertion.

`_attach` then derives `sources` from retained evidence only, one `Source` per
retained `version_id`, sorted. A source cannot be listed for a claim that was
dropped, and no source is listed for a version with no retained evidence.

In a bounded pack, claims keep all of their evidence. Conflict alternatives
stay together or are omitted together. Quotes are never sliced to fit.

## What this does not establish

Exact containment is a statement about the archive, not about the world.

- A quote proves the archived chunk contained that text at that offset. It does
  not prove the claim is true.
- The claim text is required to appear in its supporting chunk, which blocks
  inference, not error. An author can write something false and support it with
  a genuine quote.
- Conflicts mean the same authored key carries different canonical values in
  active documents. That is a recorded disagreement, not a determination of
  which side is correct.
- `CURRENT` means latest active in-window source assertion. It is not verified
  present-day truth.
- Corpus content is data, not instructions. Provenance does not neutralize
  prompt injection, and attribution does not make a downstream model immune to
  text it has been handed. `tests/test_security.py` covers storage and retrieval
  of injection-shaped content as inert text.
- Archive tables are append-only under ordinary DML, not tamper-proof against
  the database owner. `README.md` states this and the same caution is repeated
  in `examples/MEMORY_API.md`.
