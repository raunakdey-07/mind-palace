"""The default corpus has to be writable.

## The bug

`mindpalace remember` -- step 3 of the README's five-minute path, the first thing
a new user runs after `init` -- failed on a clean install with:

```
new row for relation "memory_documents" violates check constraint
"memory_documents_check"
```

## Why

`corpora.id` is `CHAR(64)`. The default corpus's id is 56 zeroes followed by
"default": **63 characters**, one short of the column width. PostgreSQL therefore
blank-pads it on the way in, and the padded 64-character form is what the
application sees when it reads the row back.

The database derives a document's identity itself:

```sql
CHECK (id = encode(sha256(convert_to(rtrim(corpus_id::text) || ':' || path,
                                       'UTF8')), 'hex'))
```

`rtrim` strips the padding. `api/services/memory.py:memory_document_id` hashed the
*padded* string. The two agreed for every corpus whose id is a 64-character
digest -- that is every corpus created from a name, because `sha256("corpus:" +
name)` is always 64 hex characters -- and disagreed by exactly one byte for the
default corpus.

## Why no test caught it

Every test that touches the default corpus either reads from it or seeds it.
`tests/test_tenant_isolation.py` inserts the same 63-character id as a literal and
never writes a document into it. The corpus ids the retrieval tests use are all
64-character digests. The one corpus whose id is not 64 characters long is the
one corpus nobody writes a document into.

It was found by following the README in a container with nothing preinstalled,
which is the only way this shows up.

## What is asserted here

* the derivation agrees with the database constraint, for the default corpus and
  for a hashed corpus;
* a document can actually be written into the default corpus end to end, and the
  write satisfies the constraint rather than being computed to satisfy it.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import text

from api.services.corpora import DEFAULT_CORPUS_ID, DEFAULT_CORPUS_NAME, corpus_id_for_name
from api.services.memory import memory_document_id

pytestmark = [pytest.mark.asyncio]


def test_the_default_corpus_id_is_not_wider_than_its_column():
    """The whole bug in one assertion: 63 characters in a CHAR(64) column."""
    assert len(DEFAULT_CORPUS_ID) <= 64, (
        f"DEFAULT_CORPUS_ID is {len(DEFAULT_CORPUS_ID)} characters; anything under 64 "
        "is blank-padded by CHAR(64) and comes back padded"
    )
    assert (
        DEFAULT_CORPUS_ID.strip() == DEFAULT_CORPUS_ID
    ), "the default corpus id must not carry its own padding"


@pytest.mark.parametrize(
    "corpus_id",
    [DEFAULT_CORPUS_ID, corpus_id_for_name("alpha-brand"), corpus_id_for_name("a")],
    ids=["default", "hashed", "short-name"],
)
def test_the_derivation_matches_the_database_constraint(corpus_id, path="memory/x.md"):
    """Reproduce the CHECK expression in Python and require agreement.

    Mirrors `rtrim(corpus_id::text) || ':' || path`, which is what PostgreSQL
    evaluates -- `corpus_id::text` on a blank-padded CHAR drops the padding, and
    the explicit rtrim is belt and braces.
    """
    padded = corpus_id.ljust(64)  # what CHAR(64) stores
    constraint = hashlib.sha256(f"{padded.rstrip()}:{path}".encode("utf-8")).hexdigest()
    assert memory_document_id(padded, path) == constraint, (
        "memory_document_id disagrees with memory_documents_check for "
        f"corpus_id={corpus_id!r}; every write into this corpus will be refused"
    )


async def test_a_document_can_be_written_into_the_default_corpus():
    """End to end, against a real database, through the real write path.

    The row is written under the default corpus's own id and then removed, and
    the removal is not cosmetic.

    `tests/test_ingestion_lifecycle.py::test_metadata_only_change_is_detected`
    depends on the default corpus having no archived versions -- its own
    docstring says so and explains why. Before this bug was fixed *nothing* could
    write to the default corpus, so that premise held by accident. Now that
    writes succeed, a test that leaves a document there breaks a different test
    that has nothing to do with it.

    That is worth stating rather than working around quietly: this suite has a
    shared corpus, and the test that relied on it being empty was only reliable
    because the product was broken. The removal below keeps this file from
    re-introducing the coupling; it does not remove the coupling.
    """
    import os

    from api.services import authz
    from api.services import db as db_module
    from api.services.db import admin_session_scope
    from api.services.remember import remember

    if not os.getenv("DATABASE_URL"):
        pytest.skip("no DATABASE_URL")

    marker = "default-corpus-write-probe"
    corpus_id = DEFAULT_CORPUS_ID.strip()
    try:
        async with admin_session_scope() as db:
            with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
                result = await remember(
                    "The default corpus accepts writes.",
                    corpus=DEFAULT_CORPUS_NAME,
                    key=marker,
                )
                await db.commit()
                rows = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM memory_documents "
                            "WHERE corpus_id = :c AND path LIKE :p"
                        ),
                        {"c": corpus_id, "p": f"%{marker}%"},
                    )
                ).scalar()
        # `remember()` returns the identity it wrote and does not echo the corpus,
        # so the assertion is that the row landed under the default corpus's id.
        assert result["success"] and result["document_id"], f"remember() returned {result!r}"
        assert rows and rows > 0, (
            "remember() reported success but wrote no document into the default corpus; "
            "the CHECK constraint would have refused it"
        )
    finally:
        # Append-only: erasure is the only way back, and it is the shipped path.
        await _erase(corpus_id)
        await _restore_default_corpus()


async def _erase(corpus_id: str) -> None:
    from api.services import authz
    from api.services import db as db_module
    from api.services.db import admin_session_scope

    async with admin_session_scope() as db:
        with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                text("SELECT set_config('mindpalace.tenant_scope', '{*}', true)"),
            )
            await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
        await db.commit()


async def _restore_default_corpus() -> None:
    """Put the row back, with the id it had.

    Not `create_corpus`: that derives the id as `sha256("corpus:" + name)`, and
    the default corpus's id is a hand-written constant rather than that digest.
    Restoring it any other way would leave the corpus unwritable again, which is
    the bug this file exists to catch.
    """
    from api.services import authz
    from api.services import db as db_module
    from api.services.db import admin_session_scope

    async with admin_session_scope() as db:
        with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                text(
                    "INSERT INTO corpora (id, name, description) "
                    "VALUES (:i, :n, '') ON CONFLICT (id) DO NOTHING"
                ),
                {"i": DEFAULT_CORPUS_ID, "n": DEFAULT_CORPUS_NAME},
            )
        await db.commit()
