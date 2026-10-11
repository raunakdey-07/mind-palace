# Deletion, retention, and why cryptographic erasure is not implemented

This document separates two things that are constantly conflated, states exactly
what Mind Palace can delete today, and explains why "cryptographic erasure" is
reported as **unresolved** rather than shipped.

## The two claims

**Deletion** — remove active data and make it unavailable through every supported
interface. Mind Palace implements this.

**Cryptographic erasure** — make *retained* copies, including backups, permanently
unrecoverable, by destroying keys. Mind Palace does **not** implement this, and
deleting rows is not a weaker version of it. They differ in kind.

## What deletion does today

`mindpalace corpus delete <name>` and `DELETE /api/corpora/{name}` call
`mindpalace_erase_corpus`, one `SECURITY DEFINER` function that, in a single
transaction:

* takes the same advisory lock every writer takes, so erasure serialises with
  concurrent writes;
* deletes every archive table in dependency order — `memory_snapshot_seals`,
  `memory_snapshot_versions`, `memory_snapshots`, `memory_evidence`,
  `memory_claims`, `memory_chunks`, `memory_versions`, `memory_claim_embeddings`,
  `memory_documents`;
* deletes the live projection: `chunks` through its documents, `documents`,
  `ingestion_manifest`;
* deletes every credential's grant to the corpus;
* returns per-table row counts and table names — and nothing else.

The report carries counts because an erasure audit trail that reproduced the
content it erased would defeat the erasure. There is deliberately no field in
which to put one.

Authorization is `manage_corpus`, checked twice: once in the application, and
again inside the function against the *connecting* role's own tenant scope. The
runtime database role holds no `DELETE` on any archive table, so this function is
the only path to remove rows — and `PUBLIC` holds no `EXECUTE` on it, so a role
that is neither the runtime role nor an administrative one cannot call it at all.

It is idempotent: erasing an absent corpus reports `deleted: false` with reason
`corpus_not_found`.

### Verified

`tests/test_deployment_profiles_and_lifecycle.py` asserts completeness (every
table reaches zero), neighbour-intactness (a corpus sharing the tenant is
unchanged on every table, before and after), authorization (`403` for the wrong
role, `404` for another tenant so existence is not confirmed), idempotency,
serialisation under concurrent access, and that no customer content appears in the
report.

## The complete data lifecycle

Everywhere a corpus's data can persist, and who can actually remove it.

| Location | Kind | Erasable by Mind Palace? |
|---|---|---|
| `memory_*` archive tables | active data | **yes** — `mindpalace_erase_corpus` |
| `documents`, `chunks`, `ingestion_manifest` | active data (live projection) | **yes** — same function |
| `memory_resource_grants` | active data | **yes** — cascade with the corpus's documents |
| PostgreSQL WAL | operational | only by operator retention |
| PostgreSQL backups, snapshots, replicas | **operator-controlled** | **no** — outside Mind Palace |
| Filesystem: sources, temp files | customer-controlled | **no** — Mind Palace never deletes a source file |
| Exported Receipt v1, Memory Pack v1 | **customer-exported** | **no** — a file on the customer's machine |
| Logs and traces | operational | **no** — and deliberately contains no memory content, so there is nothing to erase |
| The database itself | operator-controlled | **no** |

Mind Palace never keys a corpus to a filesystem path, so there is no on-disk
per-corpus state to clean up. That is a design choice that removes a whole class
of erasure problem, and it does not remove the backup one.

## Why cryptographic erasure is not implemented

The requirement has four parts, and three of them are outside what this process
can honestly deliver alone.

1. **A key provider independent of the backup.** The only copy of a
   corpus's data-encryption key must not live in the same backup that is
   supposed to become unrecoverable. That means a KMS, a Vault, or an operator-held
   secret. Mind Palace has no such integration, and inventing a "default key
   provider" that stores keys in the database would produce a system that
   *appears* to offer cryptographic erasure while offering none.
2. **Complete coverage, including derived data.** Encrypting claim text is not
   enough. The 384-dimension embedding vectors leak semantic content and must be
   encrypted too, as must evidence quotes, document paths, claim keys, identifiers,
   and every index derived from them. A partial implementation that encrypts the
   obvious columns and leaves the vectors in the clear is worse than none: it
   converts an obvious gap into a subtle one.
3. **Migration of existing plaintext rows.** Every existing row would need
   rewriting, with the archive's append-only triggers disabled for the duration
   and re-enabled afterwards. That is a maintenance window on data someone may
   not be able to re-encrypt at all.
4. **A statement about backups that is true.** Erasing the key makes the
   *database's* copies unrecoverable. It says nothing about a `pg_dump` taken last
   Tuesday whose plaintext was written before any key existed, or about a
   replica, or about a customer who exported a Memory Pack.

Any one of those is a project. Together they are a different product with a
different operational story, and shipping half of it under this name is the
failure mode this section exists to prevent.

### The design, for whoever builds it

Not speculative — this is the shape that would satisfy the requirement, recorded
so the decision is auditable rather than merely deferred.

* **Envelope encryption.** Per-corpus data-encryption key (DEK), wrapped by a
  key-encryption key (KEK) held in an external provider. Row ciphertext holds
  `nonce || ciphertext || tag`; the wrapped DEK lives in a `memory_corpus_keys`
  table holding only the provider's key id, never key material.
* **What is covered.** Claim text and values, evidence quotes, document paths,
  claim keys, snapshot content, and embedding vectors. Deterministic search on
  encrypted columns is impossible, so retrieval would need either a blind index
  (leaks equality) or decryption-and-filter, which changes what a corpus can hold
  at scale. **This is the real cost, and it is a retrieval-architecture change,
  not a storage change.**
* **Key lifecycle.** Created with the corpus; destroyed on erasure; destroyed
  after a documented retention window if the operator chooses; rotated by
  re-wrapping the DEK, which does not require rewriting ciphertext.
* **What survives erasure.** The schema. Row counts. The fact that a corpus once
  existed and was erased. The KEK, which wraps nothing after the DEK is destroyed.
* **Proof.** Erasing the DEK must make a previously-captured ciphertext
  unrecoverable, demonstrated against a real key provider and a real backup, not
  asserted.
* **Self-hosting.** An operator without an external KMS has no compliant option.
  Saying so is part of the product.

### The honest interim statement

> Mind Palace deletes a corpus's rows from the live database, and can delete the
> key material that would be needed to read any *future* encrypted copy. It cannot
> make a backup taken before that point unrecoverable, and it does not currently
> encrypt anything.

Anyone whose retention obligation requires the second thing needs a different
system for now. That is a worse answer than a qualified "yes", and it is the
correct one.

## Related, and not the same thing

* **Deleting a source file is not deleting memory.** `ingest-repo` writes a `DELETED`
  tombstone version and removes the live row. The content remains in earlier
  versions, retrievable with `history` or `as-of`. This is deliberate: the archive
  records what was true and when, and discarding it would make a Receipt
  meaningless. Erasing a *corpus* is the operation that removes content.
* **A Receipt proves integrity, not truth.** It proves the archived chunk
  contained the quoted text at those offsets at that version. It does not prove
  the claim is true, and no part of this document changes that.