"""The reproduction for the withdrawn migration's write failure.

Kept beside the withdrawn migration rather than in `tests/`, because it describes
a capability that is **not shipped**: migration 011 is out of the chain, so this
test cannot run without it, and a test that cannot run is not a test.

To run it:

    # 1. apply the withdrawn migration to a disposable database
    cp docs/withdrawn/011_resource_level_authorization.py migrations/versions/
    alembic upgrade head
    # 2. put this file back in tests/ (it imports tenant_harness by name)
    cp docs/withdrawn/test_insert_on_conflict_rls.py tests/
    pytest tests/test_insert_on_conflict_rls.py
    # 3. remove it again
    rm tests/test_insert_on_conflict_rls.py migrations/versions/011_*.py

## What it proves

A row-level policy whose `USING` clause evaluates the *row's own existence* makes
`INSERT ... ON CONFLICT DO NOTHING` fail for a row that does not exist yet, with
SQLSTATE `42501`, even though the `WITH CHECK` clause passes.

That is what broke `POST /api/memory/remember` under migration 011. The archive
insert is exactly this statement:

```sql
INSERT INTO memory_documents (corpus_id, id, path) VALUES (...)
ON CONFLICT (corpus_id, id) DO NOTHING
```

and the policy 011 installed was:

```sql
USING (mindpalace_allowed_corpus(corpus_id)
       AND mindpalace_allowed_document(corpus_id, id))
WITH CHECK (mindpalace_allowed_corpus(corpus_id))
```

`mindpalace_allowed_document` looks the document up and returns `FALSE` when the
lookup finds nothing:

```sql
COALESCE((SELECT d.owner_principal_id IS NULL OR ... FROM memory_documents d
          WHERE d.corpus_id = target_corpus AND d.id = target_document), FALSE)
```

For a brand-new document the subquery yields NULL, `COALESCE` yields `FALSE`, and
the `USING` side is `FALSE` while the `WITH CHECK` side is `TRUE`.

A plain `INSERT` passes, because PostgreSQL evaluates only `WITH CHECK` for it.
`ON CONFLICT` — and `RETURNING` — evaluate `USING` as well, because both must
*select* the row to decide whether a conflict occurred or to return it. A row that
has just been inserted but is not yet visible to the `USING` predicate therefore
fails the check the plain statement never applies.

This is not a PostgreSQL bug and it is not fixable by reordering the policy: a
policy that hides a row before it exists cannot also be the policy that admits it.

## The two shapes that work

* **Corpus-only `USING`** (what migration 008 has, and what ships today):
  `USING (mindpalace_allowed_corpus(corpus_id))`. No existence dependency, so the
  insert path is unaffected. Ownership is then only enforceable on `SELECT`.
* **Ownership enforced on write via `WITH CHECK`**, with `USING` kept
  corpus-only, plus a second *restrictive* policy for reads:

  ```sql
  CREATE POLICY read_owner ON memory_documents
      FOR SELECT USING (mindpalace_allowed_corpus(corpus_id)
                        AND mindpalace_allowed_document(corpus_id, id));
  CREATE POLICY insert_allowed ON memory_documents
      FOR INSERT WITH CHECK (mindpalace_allowed_corpus(corpus_id));
  ```

  Restrictive policies are AND-ed with permissive ones, so a row that no policy
  lets you read is invisible, and a row nobody granted is not writable. The
  existence-dependent predicate is confined to `FOR SELECT`, which never runs on
  the insert path.

The second shape is what reinstating 011 should use. It is **not** implemented —
doing so is the Phase 4 decision.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tenant_harness import apply_as_runtime, requires_tenant_db, runtime_url

pytestmark = [pytest.mark.asyncio, requires_tenant_db]


def _corpus_id(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def _document_id(corpus_id: str, path: str) -> str:
    return hashlib.sha256(f"{corpus_id}:{path}".encode()).hexdigest()


@pytest.fixture
async def corpus():
    from api.services import authz
    from api.services.corpora import create_corpus, get_corpus_by_name
    from api.services.db import admin_session_scope, principal_scope

    name = "insert-on-conflict-repro"
    corpus_id = _corpus_id(name)
    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
            await db.commit()
            if await get_corpus_by_name(db, name) is None:
                await create_corpus(db, name, "repro")
    yield corpus_id
    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
        await db.commit()


async def _insert(engine, corpus_id: str, path: str, *, on_conflict: bool):
    """The archive's insert, with or without the ON CONFLICT clause."""
    document = _document_id(corpus_id, path)
    suffix = " ON CONFLICT (corpus_id, id) DO NOTHING" if on_conflict else ""
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                {"s": corpus_id},
            )
            await conn.execute(
                text(
                    f"INSERT INTO memory_documents (corpus_id, id, path) "
                    f"VALUES (:c, :i, :p){suffix}"
                ),
                {"c": corpus_id, "i": document, "p": path},
            )


async def test_a_plain_insert_succeeds_under_the_ownership_policy(corpus):
    """The control: this half of the statement is fine.

    Establishes that the failure is specific to `ON CONFLICT`, not to the policy
    being wrong about the corpus.
    """
    engine = apply_as_runtime(runtime_url())
    try:
        await _insert(engine, corpus, "notes/plain.md", on_conflict=False)
    finally:
        await engine.dispose()


async def test_on_conflict_do_nothing_fails_with_42501(corpus):
    """The defect, and the exact statement production issues.

    This is the whole regression in one test: same role, same corpus, same
    document, differing only by the `ON CONFLICT` clause.
    """
    engine = apply_as_runtime(runtime_url())
    try:
        with pytest.raises(ProgrammingError) as caught:
            await _insert(engine, corpus, "notes/conflict.md", on_conflict=True)
    finally:
        await engine.dispose()

    message = str(caught.value)
    assert "42501" in message or "row-level security policy" in message
    assert (
        caught.value.orig.sqlstate == "42501"
    ), f"expected insufficient_privilege, got {caught.value.orig.sqlstate}"


async def test_the_two_policy_sides_disagree_for_a_row_that_does_not_exist(corpus):
    """The mechanism, asserted directly rather than inferred from the failure.

    `WITH CHECK` passes and `USING` fails for the same row. That asymmetry is the
    entire explanation: PostgreSQL applies only `WITH CHECK` to a plain insert,
    and applies `USING` as well to `ON CONFLICT` and `RETURNING`, both of which
    must select the row.
    """
    document = _document_id(corpus, "notes/absent.md")
    engine = apply_as_runtime(runtime_url())
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
                    {"s": corpus},
                )
                with_check_side = (
                    await conn.execute(text("SELECT mindpalace_allowed_corpus(:c)"), {"c": corpus})
                ).scalar()
                using_side = (
                    await conn.execute(
                        text("SELECT mindpalace_allowed_document(:c, :d)"),
                        {"c": corpus, "d": document},
                    )
                ).scalar()
    finally:
        await engine.dispose()

    assert with_check_side is True, "the corpus predicate should pass"
    assert using_side is False, (
        "the ownership predicate returns FALSE for a document that does not exist "
        "yet, because it is COALESCE(..., FALSE) over a lookup that finds nothing"
    )
