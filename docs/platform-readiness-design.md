# Platform readiness: design note

Written before implementation, per the phase gate. It states what exists, what is
missing, and what this phase will and will not change. Nothing here is a claim
about work not yet done.

## What the repository actually is today

Verified by reading the code and the live schema, not by reading the previous
report.

* Authorization is `principal → role` plus `principal → set of corpus ids`. That is
  the whole model. `memory_principals(id, name, key_hash, role, …)` and
  `memory_principal_corpora(principal_id, corpus_id)`.
* Every tenant table is scoped by `corpus_id` alone. There is **no** resource- or
  document-level ownership column anywhere.
* Thirteen tables carry row-level security; every policy is one of two shapes:
  `mindpalace_allowed_corpus(corpus_id)`, or — for `chunks` — an `EXISTS` against
  the parent `documents` row.
* `api/errors.py` and `cli/errors.py` exist but are *message* helpers, not a type
  hierarchy. The SDK has exactly two exception classes.
* There are no request or correlation identifiers anywhere in the codebase.
* `Embedder` and `Reranker` are singletons that import `sentence_transformers`
  directly. There is no provider protocol and no check that vectors from two
  different models never share an index.
* Generation already lives behind `LLM_PROVIDER` with an OpenAI-compatible
  transport, and no vendor SDK is a core dependency. This is closer to
  provider-neutral than it first appears.

## The gaps, ranked by consequence

### 1. No record-level isolation inside a corpus

A principal holding a grant on a corpus sees **every** row in it. Two users
sharing a workspace share every fact, permanently, with no way to keep something
private. The previous phase documented corpus-level isolation as the honest
ceiling, on the reasoning that derived rows reference across a corpus so a
mis-assigned owner would hide authored facts.

That reasoning was half right, and the half that was wrong is now visible in the
schema. The archive is a **tree**, and the tree is closed:

```
memory_documents                       <- the resource; gains ownership
  └── memory_versions                  (memory_document_id)
        ├── memory_snapshot_versions   (version_id)
        │     └── memory_snapshot_seals (snapshot_id)
        ├── memory_chunks              (version_id)
        ├── memory_evidence            (version_id)
        └── memory_claims              (version_id, memory_document_id)
              └── memory_claim_embeddings (claim_id)
```

Every descendant reaches a root document. Nothing in the archive is an orphan.
So ownership can sit at one place and be inherited, which is what the earlier
note assumed could not be done. `memory_document_id(corpus_id, path)` makes the
document's identity `(corpus, path)` — a path is already the unit a user
ingests, so the resource boundary coincides with something users already think
in.

**Decision: implement it**, at the document, with inheritance. Not record-level in
the sense of per-claim; per-document, which is the granularity the archive's own
identity function already uses.

### 2. No error taxonomy and no request identity

A failure from PostgreSQL, from the embedding provider, and from a caller's own
bug all arrive as either a `HTTPException` or a `ValueError`. A consuming
application cannot tell them apart without reading this repository. There is
also no way to correlate a failure with a request across the SDK, the CLI, the
runtime socket and MCP.

**Decision: implement.** It is additive, touches no memory semantics, and is the
single highest-leverage change for "somebody else's subsystem".

### 3. No provider protocol, and model identity is not enforced at read time

`Embedder` hardcodes one library. Vectors carry `embedding_model`,
`embedding_dimension`, `embedding_version` columns — so the identity *travels* —
but nothing checks at query time that the model in the index is the model the
process loaded. A deployment that changes `EMBEDDING_MODEL` silently searches an
index built with another model.

**Decision: implement a narrow protocol plus an identity guard.** The guard
refuses rather than degrades, which is the only safe failure mode.

### 4. Cryptographic erasure

Not implemented, and not implementable here with a claim of completeness. It needs
a key provider outside the database, a migration of every plaintext column
including vectors, and a documented answer for backups that Mind Palace does not
control. Building half of it and calling it erasure is the failure the task names
explicitly.

**Decision: do not implement.** Produce the lifecycle inventory and the design
note, keep the honest physical-deletion contract, and report this as unresolved
with the design attached.

## What this phase will not do

* No Redis, queue, second database, or vector store.
* No agent framework, generation pipeline, or orchestration layer.
* No change to Memory Pack v1, Receipt v1, `MemoryResponse`, temporal semantics,
  supersession, conflicts, abstention, or retrieval thresholds.
* No claim that RLS contains a compromised application process. It contains a
  forgotten predicate. A process that can set its own session scope can still
  forge one; this is documented, not fixed, and no new control changes that.
* No re-generation of the disputed M006.75 result, and no benchmark improvement
  pursued as an objective.

## The trust boundary, restated honestly

Both the tenant scope and the acting principal reach the database as **session
settings the application writes**. A role can set its own. This is why migration
`010` binds the `{*}` sentinel to a role that already reads every row, and why the
named-scope forge remains open and documented.

Resource-level authorization inherits exactly that property. It is defence
against an omitted or wrong predicate in a query — which is the failure mode that
actually happens — and not against a process that has decided to lie. Making the
database know which principal is presenting means moving authentication and
authorization into PostgreSQL, which is a different security model and a separate
decision. It is not smuggled in here as an implementation detail.
---

## What changed after the note was written

Recorded here because the note is the audit trail, and a note that is quietly
outdated is worse than none.

**Resource-level authorization is implemented** (migration `011`). The note
predicted it was tractable; it was, with three findings that only surfaced by
running it:

1. **`SET search_path = public` breaks replay into another schema.** PostgreSQL
   validates a `LANGUAGE sql` body at `CREATE FUNCTION` time using the
   *function's* search path, so a function pinned to `public` validates its body
   against the wrong tables. `tests/test_memory_ingestion.py` replays the whole
   chain into a throwaway schema and failed exactly that way. The migration now
   interpolates the schema it is running in.
2. **Permissive policies are OR'd.** Migration `008`'s policies were still present,
   so the new ones were installed, correct, and inert. The first run of the peer
   test failed for that reason alone. `011` now drops `mindpalace_tenant` before
   creating its replacements.
3. **`ON DELETE SET NULL` cannot work on an append-only table.** The FK action
   issues an `UPDATE`, which `memory_reject_mutation` refuses — so deleting a
   principal who owned a document failed with "memory archive is append-only".
   `RESTRICT` is the coherent choice: a principal who owns a document cannot be
   deleted until it is erased or shared.

**The error taxonomy and request ids are implemented** (`mindpalace_errors.py`,
`identify_request` middleware). The existing `detail` shape is preserved; the new
fields were added beside it.

**Provider protocols are documented and conformance-tested**
(`tests/test_provider_conformance.py`), but the *identity guard* — refusing to
search an index built with a different model — is documented rather than wired
into every query path. That gap is named in the final report.

**Cryptographic erasure is not implemented.** See `docs/deletion-and-erasure.md`
for the design and the reason.

**Resource-level authorization costs about 10x on a full-corpus scan** — measured,
not estimated. See `docs/performance-resource-authorization.md`.
