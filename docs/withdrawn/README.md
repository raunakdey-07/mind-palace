# Withdrawn: resource-level authorization

`011_resource_level_authorization.py` is **not** in the migration chain. It was
written, applied, and worked — the isolation tests passed and the negative
controls fired correctly — and then it was withdrawn because it broke a write
path it should not have touched.

## What it did

Migration `010` enforces corpus-level isolation: a principal holding a corpus
sees every row in it. Two people sharing a workspace therefore share every fact,
with no way to keep something private. This migration added ownership to the
archive root (`memory_documents.owner_principal_id`) and made it inherit to
versions, claims, evidence, chunks, embeddings, snapshot membership and seals.
`NULL` meant shared, which is the previous behaviour, so it was additive.

Three findings came out of building it, and they are the valuable part — they
are documented because they will bite whoever reinstates this:

1. **`SET search_path = public` breaks replay into another schema.** PostgreSQL
   validates a `LANGUAGE sql` body at `CREATE FUNCTION` time using the
   *function's* search path, so a function pinned to `public` validates against
   the wrong tables. `tests/test_memory_ingestion.py` replays the whole chain into
   a throwaway schema and failed exactly that way. The fix is to interpolate the
   schema being migrated.

2. **Permissive policies are OR'd.** PostgreSQL combines multiple permissive
   policies with OR, so leaving migration `008`'s `mindpalace_tenant` in place
   alongside the new one means a caller passes whenever *either* is satisfied.
   The new policies were installed, correct, and completely inert. `011` must drop
   the old ones first — which is why it looks destructive in the diff.

3. **`ON DELETE SET NULL` cannot work on an append-only table.** The foreign-key
   action issues an `UPDATE`, and `memory_reject_mutation` refuses `UPDATE` on the
   archive. Deleting a principal who owned a document failed with "memory archive
   is append-only" — a bewildering message for what is an ownership conflict.
   `RESTRICT` is the coherent choice.

## Root cause of the write failure — ESTABLISHED

Reproduced in pure SQL against a disposable database with only migration 011
applied, as the least-privileged role, with a corpus in scope.

| Statement | Result |
|---|---|
| `INSERT INTO memory_documents (corpus_id, id, path) VALUES (...)` | **succeeds** |
| the same `... ON CONFLICT (corpus_id, id) DO NOTHING` | **ERROR 42501** — `new row violates row-level security policy for table "memory_documents"` |
| the same `... RETURNING id` | **ERROR 42501** |

Both predicates, evaluated for the row being inserted (which does not exist yet):

```
WITH CHECK side   mindpalace_allowed_corpus(corpus_id)          = true
USING side        mindpalace_allowed_document(corpus_id, id)    = false
```

**Mechanism.** Migration 011 installed:

```sql
USING      (mindpalace_allowed_corpus(corpus_id)
            AND mindpalace_allowed_document(corpus_id, id))
WITH CHECK (mindpalace_allowed_corpus(corpus_id))
```

and `mindpalace_allowed_document` is `COALESCE((SELECT ... FROM memory_documents
WHERE corpus_id = ... AND id = ...), FALSE)` — a lookup of the row's own
existence. For a brand-new document the subquery returns NULL, `COALESCE` yields
`FALSE`, so the `USING` side is FALSE while the `WITH CHECK` side is TRUE.

PostgreSQL evaluates **only `WITH CHECK`** for a plain `INSERT`. It evaluates
**`USING` as well** for `ON CONFLICT` and for `RETURNING`, because both must
*select* the row — `ON CONFLICT` to decide whether a conflict occurred,
`RETURNING` to hand it back. A row that has been inserted but is not yet visible
to the existence-dependent predicate fails a check the plain statement never
applies.

This is not a PostgreSQL bug and cannot be fixed by reordering the policy: a
predicate that hides a row before it exists cannot also be the predicate that
admits it. `WITH CHECK` on the insert path was never the problem.

**Minimal reproduction:** `docs/withdrawn/test_insert_on_conflict_rls.py`. Three
tests: the plain insert succeeds, the `ON CONFLICT` insert raises `42501`, and
the two policy sides are shown to disagree for the same row. It passes on a
database with 011 applied and fails on one without it, in both directions.

## The two shapes that work

1. **Corpus-only `USING`** — what migration 008 has, and what ships today.
   No existence dependency, so the insert path is unaffected. Ownership is then
   enforceable on `SELECT` only, which is to say not enforced at all.

2. **Ownership on write via `WITH CHECK`, ownership on read via a restrictive
   policy** — the shape reinstating 011 should use:

   ```sql
   CREATE POLICY read_owner ON memory_documents
       FOR SELECT USING (mindpalace_allowed_corpus(corpus_id)
                         AND mindpalace_allowed_document(corpus_id, id));
   CREATE POLICY insert_allowed ON memory_documents
       FOR INSERT WITH CHECK (mindpalace_allowed_corpus(corpus_id));
   ```

   Restrictive policies are AND-ed with permissive ones, so an ungranted row is
   invisible to `SELECT` and the insert path never evaluates the
   existence-dependent predicate. The same split applies to every archive table.

   **Not implemented.** That is the Phase 4 decision.

## Why it was withdrawn

`POST /api/memory/remember` began returning `503` for a write that has always
succeeded, once the resource policy was installed. The mechanism is established
above and reproduced in `docs/withdrawn/test_insert_on_conflict_rls.py`.

The migration was removed from the chain, the write path was reverted, and the
capability is reported as designed-and-prototyped rather than shipped. Shipping a
migration that breaks the primary write path is not acceptable at any confidence
level, and the correct policy shape is known but not yet applied.

The earlier note in this file said the cause was "not diagnosed to a conclusion".
That is superseded: it is diagnosed, and the diagnosis is the `ON CONFLICT`
interaction, not anything in the corpus predicate.

## What survives

* `api/services/resources.py` — the ownership model, document identity, and the
  share/unshare functions. Inert until a migration reinstates the columns.
* `owner_principal_id` parameters on `record_version` and `ingest_file`. Accepted
  and ignored, with a docstring saying so, so reinstating is a migration rather
  than an edit at twenty call sites.
* `tests/test_resource_isolation.py` — the adversarial suite. It fails against the
  current schema, which is correct: the capability it tests is not shipped.
* `PRINCIPAL_SETTING` and `_principal_value()` in `api/services/db.py`. Setting an
  unused transaction-local GUC is inert and keeps the wiring honest.

## To reinstate

1. Re-add the migration with findings 1-3 above already applied, **and** the
   policy shape above — `FOR SELECT` for the existence-dependent ownership
   predicate, `FOR INSERT`/`FOR UPDATE`/`FOR DELETE` for the corpus predicate.
   Copying the `ALL` policy from the withdrawn version reproduces the 42501.
2. **Re-measure.** The prototype cost roughly 10x on a full-corpus scan
   (`docs/performance-resource-authorization.md`). A design that has to be
   re-architected for correctness should not also be assumed to keep that number.

---

## Phase 1 finding: the five isolation failures were the environment

The five runtime-role failures reported as regressions **do not reproduce** on a
database built from `migrations/` plus `scripts/db/runtime_roles.sql`. On that
database the whole suite passes: **1600 passed, 1 skipped, 0 failed.**

The schema is byte-identical between the two databases on every dimension checked:
110 column definitions, 3 constraints, 24 triggers, 13 policies (names *and*
qual/with_check expressions), `relrowsecurity`/`relforcerowsecurity` on all 16
tables, 3 table grants and 303 column grants for `mindpalace_runtime`, and 4
default ACLs. No `011` residue on either side: zero `owner_principal_id` columns,
only `mindpalace_allowed_corpus` present, no `memory_resource_grants` table.

What differs is **data**: the working database carries 108 corpora, 306
principals, 231 documents and 316 claims accumulated across many sessions, plus
two `memory_principal_corpora` rows inserted by hand. The clean database carries
7 / 9 / 11 / 15.

The specific contaminating row was **not identified**. What is established is that
the failures are not reproducible from a clean build, and that the schema is not
the difference. Treat the earlier "five isolation failures" as an artefact of a
polluted working database and rebuild that database before drawing conclusions
from it.
