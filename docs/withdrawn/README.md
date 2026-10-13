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

## Splitting the policy: TESTED, and it does not work

An earlier version of this file proposed that the fix was to move the
existence-dependent predicate out of the write path:

```sql
CREATE POLICY read_owner ON memory_documents
    FOR SELECT USING (mindpalace_allowed_corpus(corpus_id)
                      AND mindpalace_allowed_document(corpus_id, id));
CREATE POLICY insert_allowed ON memory_documents
    FOR INSERT WITH CHECK (mindpalace_allowed_corpus(corpus_id));
```

**Tested on a disposable database with only migration 011 applied. It does not
work.** `ON CONFLICT DO NOTHING` and `RETURNING` still fail, identically:

| Statement | With the split policy |
|---|---|
| `INSERT ... VALUES (corpus_id, id, path)` | succeeds |
| `INSERT ... ON CONFLICT (corpus_id, id) DO NOTHING` | **ERROR 42501** |
| `INSERT ... RETURNING id` | **ERROR 42501** |

The split relocates the failure rather than removing it. Both `ON CONFLICT` and
`RETURNING` apply the **`SELECT`** policy to the row they are producing, because
both must *select* it: `ON CONFLICT` to decide whether a conflict occurred,
`RETURNING` to hand it back. The existence-dependent predicate was moved into
`FOR SELECT`, which is exactly the policy those two statements evaluate. Moving
the predicate into the policy that is consulted on the write path was never the
problem; the problem is that the predicate is consulted there.

One thing the split does get right, and it is worth keeping: it does not leak.
A caller scoped to one corpus still sees only that corpus's rows.

### A PostgreSQL detail worth knowing

A policy declared `FOR INSERT` with only a `WITH CHECK` clause is stored with
`pg_policy.polcmd = 'a'` (`ALL`) and `polqual = NULL`. It looks like it applies
to reads; it does not, because a NULL `USING` contributes nothing and permissive
policies are OR-ed. Worth knowing before writing a negative test that asserts a
policy list, because the policy you wrote and the policy in the catalog are not
the same shape.

## The shape that works — TESTED, and not shipped

Do not look the row up. Read the row.

```sql
CREATE POLICY owner_read ON memory_documents FOR SELECT
    USING (mindpalace_allowed_corpus(corpus_id)
           AND (owner_principal_id IS NULL
                OR owner_principal_id::text = mindpalace_acting_principal()::text));
CREATE POLICY corpus_write ON memory_documents FOR INSERT
    WITH CHECK (mindpalace_allowed_corpus(corpus_id));
```

The predicate evaluates columns **of the row being tested**, so a row that does
not exist yet evaluates to whatever its own columns say rather than to `FALSE`.
Measured, same disposable database:

| Statement | Result |
|---|---|
| `INSERT ... VALUES (corpus_id, id, path)` | succeeds |
| `INSERT ... ON CONFLICT (corpus_id, id) DO NOTHING` | **succeeds** |
| `INSERT ... RETURNING id` | **succeeds** |

And it isolates. A row inserted with `owner_principal_id` set, read as the
least-privileged runtime role scoped to that corpus:

| Acting principal | Rows visible |
|---|---|
| the owner | 1 |
| a stranger in the same corpus | 0 |
| none set | 0 (fails closed) |

**None of this is shipped.** `owner_principal_id` does not exist in the current
schema. This section records that the shape was tested in isolation and what the
result was, so that reinstating is a decision rather than a re-derivation. What
it does *not* establish: the cost (the ~10x scan figure was measured against the
withdrawn design, not this one), the behaviour on the eleven other archive
tables, and the performance of a per-row ownership predicate at scale. Treat
those as open.

## The two shapes, and why one was dropped

An earlier draft of this file offered two shapes: the corpus-only `USING` that
migration 008 ships, and a `FOR SELECT` / `FOR INSERT` split for the ownership
predicate. The second was proposed but never tested, and it is wrong — see
**Splitting the policy** above, where it was tested and still returns 42501.

What survives from that draft is the first shape and one observation: restrictive
policies are AND-ed with permissive ones, so a `FOR SELECT ... AS RESTRICTIVE`
policy is the only way to *narrow* what the corpus policies grant. The
`owner_principal_id` design above does not need it, because the ownership test
sits inside the permissive policy's own `USING`.

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
   `owner_principal_id` design from **The shape that works** — the ownership
   predicate must read the row's own column, not look the row up. Copying the
   `ALL` policy from the withdrawn version reproduces the 42501, and so does the
   `FOR SELECT`/`FOR INSERT` split: both were tested.
2. **Re-measure.** The prototype cost roughly 10x on a full-corpus scan
   (`docs/performance-resource-authorization.md`). That number was measured
   against the withdrawn design, so it is not a prediction for the design above.
3. **Re-decide the eleven other archive tables.** Only `memory_documents` was
   tested. Every derived table inherits ownership through a parent, and each one
   needs its own version of the same argument.

---

## The five isolation failures: root cause, ESTABLISHED

Not data. Not role state. **One policy expression on one table.**

```sql
-- development database (hand-repaired after 011 came out of the chain)
mindpalace_tenant ON memory_documents
    USING (mindpalace_allowed_corpus(id))
    WITH CHECK (mindpalace_allowed_corpus(id))

-- migration 008, which is what ships
mindpalace_tenant ON memory_documents
    USING (mindpalace_allowed_corpus(corpus_id))
    WITH CHECK (mindpalace_allowed_corpus(corpus_id))
```

`memory_documents` has *both* an `id` (the document id) and a `corpus_id`.
`mindpalace_allowed_corpus(candidate)` answers "is this candidate in the tenant
scope", and the tenant scope holds **corpus ids**. So `…(id)` asks whether a
*document* id appears in a list of *corpus* ids. It never does. The predicate is
`FALSE` for every row, so the least-privileged role sees an empty table and
every write fails its `WITH CHECK`.

```
$ psql -U mindpalace_runtime -d mindpalace \
    -c "SELECT count(*) FROM memory_documents;"
rows visible to runtime role on memory_documents: 0
```

Migration `008` is not ambiguous about this. Line 121:

```python
column = "id" if table == "corpora" else "corpus_id"
```

`corpora` is keyed by `id`; the other eleven tables are keyed by `corpus_id`. The
hand repair used the `corpora` shape everywhere, which is correct for exactly one
table out of twelve.

**Proof.** On a copy of the working database, with every table truncated to
empty:

| State | The 5 tests |
|---|---|
| as found | 5 failed |
| after `DROP POLICY` + `CREATE POLICY` with `corpus_id` | **5 passed** |

Data volume, row counts and the 108 corpora / 306 principals were all red
herrings: truncation changed nothing, and the repair changed everything with no
rows in the table at all.

### How this survived the earlier schema diff

The previous session compared policy *names* across databases and compared policy
*expressions* for two tables — `memory_claims` and `documents`. It never printed
`memory_documents`. `memory_documents` is the table every write goes through
first, so it is the one whose policy being wrong fails the loudest, and it is the
one that was not compared.

The generalisable lesson: a diff that samples rows is not a diff. Comparing
"does this table have a non-null USING clause" cannot tell `…(id)` from
`…(corpus_id)`.

### Other debris in the working database, inert

* `probe_recurse()` — a leftover SQL function referencing nothing in the repo.
* `mp_inherit_test=r` grants on `corpora` and `memory_claims` — a probe role with
  SELECT. In the *permissive* direction, so it cannot explain the failures.
* `mp_bypass_parent` / `mp_inherit_test` role pair in `pg_auth_members`.

None of these are in any migration. All of them are evidence that the working
database has been used as a scratchpad. That is the argument for Phase 1(b):
make every session create and drop its own database.

## Phase 1 finding, as first stated — and why it was wrong

The five runtime-role failures do not reproduce on a database built from
`migrations/` plus `scripts/db/runtime_roles.sql`. On that database the whole
suite passes: **1600 passed, 1 skipped, 0 failed**, measured twice.

That much is right. The reasoning was not. This section originally concluded the
cause was **data** — 108 corpora, 306 principals, 231 documents and 316 claims
accumulated across sessions — and reported the specific cause as **not
identified**, with the claim that the schema was identical between the two
databases.

Both of those are wrong, and they are corrected above: the cause is a single
policy expression, and the schemas were *not* identical. The earlier comparison
printed policy names for every table but policy expressions for only two of
them, and `memory_documents` was not one of the two.

The data volume was a genuine confound and worth knowing about, but it is not
the cause. Truncating the copy to zero rows changed nothing.

What generalises: when a failure disappears on a fresh database, "it was the
data" is a hypothesis with a cheap experiment behind it, not a finding. Truncate
and retest before writing it down.
