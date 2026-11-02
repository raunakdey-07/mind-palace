# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Corpus management: explicit namespaces for isolated document collections.

A corpus is the unit of tenancy in Mind Palace. Documents, chunks, and
embeddings all belong to exactly one corpus; retrieval and ingestion are
always corpus-scoped so data cannot leak across corpora.

Scope in the SQL is correctness. Permission to *name* a corpus is
authorization, and it is decided in `get_corpus_by_name`: the one function
that turns a corpus name into a corpus id. Every route that reaches tenant-owned
rows resolves its corpus here -- the nine memory operations, the feed,
`remember`, search, context, query and the corpora routes -- so there is exactly
one place to get right and exactly one place to test.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.authz import AuthorizationError, require_corpus, require_permission
from api.services.db import current_principal

# The migration-created corpus that holds all pre-corpus documents.
DEFAULT_CORPUS_ID = "00000000000000000000000000000000000000000000000000000000default"
DEFAULT_CORPUS_NAME = "default"

_NAME_RE = re.compile(r"[A-Za-z0-9._\-]+")


def corpus_id_for_name(name: str) -> str:
    """Deterministic corpus ID derived from the unique corpus name."""
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def validate_corpus_name(name: str) -> str:
    """Validate and normalize a corpus name.

    Names become part of identifiers and URIs, so restrict to a conservative
    charset: letters, digits, hyphen, underscore, dot. 1-128 chars.
    """
    name = name.strip()
    if not (1 <= len(name) <= 128):
        raise ValueError("corpus name must be 1-128 characters")
    if not re_fullmatch(name):
        raise ValueError("corpus name may contain only letters, digits, '-', '_', '.'")
    return name


def re_fullmatch(name: str) -> bool:
    import re

    return re.fullmatch(r"[A-Za-z0-9._\-]+", name) is not None


async def create_corpus(
    db: AsyncSession,
    name: str,
    description: str = "",
) -> dict:
    """Create a corpus. Raises ValueError if the name is taken or invalid.

    Requires the unrestricted administrative identity, and that is not an
    oversight. A corpus row is only insertable when its id is inside the caller's
    own tenant scope -- the row-level policy's `WITH CHECK` refuses anything else
    -- and a principal cannot hold a grant to a corpus that does not exist yet.
    So a corpus-scoped credential can never be the first to create one, however
    its role is spelled.

    The permission that does work for a tenant is erasure: `manage_corpus` on a
    corpus you already hold a grant to. That is the half of the pair that has an
    unambiguous meaning, and it is the half an offboarding needs.
    """
    name = validate_corpus_name(name)
    principal = current_principal()
    if not principal.unrestricted:
        raise AuthorizationError(
            "forbidden",
            "Creating a corpus over HTTP is not permitted for this credential. "
            "Corpora are created by the operator with `mindpalace corpus create`.",
            403,
        )
    require_permission(principal, "manage_corpus", "create a corpus")
    corpus_id = corpus_id_for_name(name)

    existing = await get_corpus_by_name(db, name)
    if existing:
        raise ValueError(f"corpus '{name}' already exists")

    await db.execute(
        text("""
            INSERT INTO corpora (id, name, description)
            VALUES (:id, :name, :description)
        """),
        {"id": corpus_id, "name": name, "description": description},
    )
    await db.commit()
    return {"id": corpus_id, "name": name, "description": description}


async def get_corpus_by_name(db: AsyncSession, name: str, *, write: bool = False) -> Optional[dict]:
    """Resolve a corpus name to its id, if this principal may use it.

    The authorization choke point. A name that is not in the caller's scope
    raises rather than returning `None`: a request that names a corpus it cannot
    use is refused the same way whether the corpus exists or not, so a caller
    cannot enumerate tenants by watching 404s turn into 200s.

    `write=True` additionally requires the read-write role. It is passed by the
    paths that mutate -- `remember`, corpus creation and deletion, sync, and the
    snapshot operation, which creates immutable state.
    """
    require_corpus(current_principal(), name, write=write)
    result = await db.execute(
        text("SELECT id, name, description FROM corpora WHERE name = :name"),
        {"name": name},
    )
    row = result.first()
    if not row:
        return None
    # `corpora.id` is CHAR(64), and the default corpus's id is 63 characters, so
    # PostgreSQL blank-pads it on the way in and hands the pad back on the way
    # out. The pad is storage, not identity: every id the product hashes is the
    # unpadded form, and `memory_documents_check` derives document identity from
    # `rtrim(corpus_id::text)`. Returning the padded value here put one extra
    # byte into every document id derived from the default corpus, and every
    # write was refused by the constraint.
    #
    # Strip at the boundary where the padding is introduced, rather than at each
    # of the many places that consume the id. See
    # tests/test_default_corpus_is_writable.py.
    return {"id": row[0].strip(), "name": row[1], "description": row[2]}


class CorpusScopeNotFound(LookupError):
    """The explicitly requested corpus does not exist."""


class CorpusScopeRequired(ValueError):
    """An operation cannot choose safely among multiple corpora."""


async def resolve_corpus_scope(
    db: AsyncSession, corpus: str | None
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a safe corpus scope for live retrieval routes.

    A single corpus remains ergonomic when the caller omits the name. Multiple
    corpora require an explicit name so retrieval cannot cross namespaces.

    The un-named path is scoped to what the caller may use, not to the whole
    installation: the implicit default is the single corpus *this principal* can
    reach. A caller granted two corpora still has to name one, so an omitted
    corpus can never silently mean "the other tenant's".
    """
    if corpus is not None:
        row = await get_corpus_by_name(db, corpus)
        if row is None:
            raise CorpusScopeNotFound(f"corpus '{corpus}' not found")
        return row["id"], row["name"]

    principal = current_principal()
    result = await db.execute(
        text(
            "SELECT id, name FROM corpora "
            "WHERE (:unrestricted OR name = ANY(:names)) ORDER BY name LIMIT 2"
        ),
        {"unrestricted": principal.unrestricted, "names": sorted(principal.corpora) or [""]},
    )
    rows = result.fetchall()
    if len(rows) > 1:
        raise CorpusScopeRequired("corpus is required when multiple corpora exist")
    if rows:
        return rows[0][0], rows[0][1]
    return None, None


async def get_or_create_corpus(db: AsyncSession, name: str) -> dict:
    """Return the corpus, creating it if needed. Used by sync-style flows."""
    existing = await get_corpus_by_name(db, name)
    if existing:
        return existing
    return await create_corpus(db, name)


async def list_corpora(db: AsyncSession) -> list[dict]:
    """List the corpora this principal may use, with document counts.

    Under row-level security the corpus table is already filtered to the
    caller's scope, so this is scoped by construction rather than by an extra
    `WHERE` that could later be dropped. The redundant predicate for a
    tenant-scoped principal is kept because it costs nothing and because it is
    what makes this correct even against a runtime role for which the policy is
    not enforced (a superuser, a `BYPASSRLS` role, or the table owner).
    """
    principal = current_principal()
    result = await db.execute(
        text("""
            SELECT c.id, c.name, c.description, count(d.id) AS document_count
            FROM corpora c
            LEFT JOIN documents d ON d.corpus_id = c.id
            WHERE (:unrestricted OR c.name = ANY(:names))
            GROUP BY c.id, c.name, c.description
            ORDER BY c.name
        """),
        {
            "unrestricted": principal.unrestricted,
            "names": sorted(principal.corpora) or [""],
        },
    )
    return [
        {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "document_count": row[3],
        }
        for row in result.fetchall()
    ]


async def reset_corpus(db: AsyncSession, name: str, description: str = "") -> dict:
    """Erase a corpus and re-create it empty. Every other corpus is untouched.

    A scratch corpus that has collected a test claim and a file that is no
    longer in the directory needs to start again, and the way out of that used
    to be dropping the whole PostgreSQL database, which took every other corpus
    in it with the one that was dirty.

    The corpus id is derived from the name, so the corpus that comes back is
    the same corpus. Its grants are read before the erasure and put back
    afterwards: a reset that quietly revoked every credential's access would
    leave a corpus that exists and that nobody can reach.

    The erasure is the same one `delete_corpus` performs, so what a reset
    removes is exactly what a delete removes.
    """
    corpus = await get_corpus_by_name(db, name)
    if not corpus:
        raise ValueError(f"corpus '{name}' not found")
    grants = [
        row[0]
        for row in (
            await db.execute(
                text("SELECT principal_id FROM memory_principal_corpora WHERE corpus_id = :c"),
                {"c": corpus["id"]},
            )
        ).all()
    ]
    report = await delete_corpus(db, name)
    if not report["deleted"]:
        raise ValueError(f"corpus '{name}' not found")
    fresh = await create_corpus(db, name, description or corpus.get("description") or "")
    for principal_id in grants:
        await db.execute(
            text(
                "INSERT INTO memory_principal_corpora (principal_id, corpus_id) "
                "VALUES (:p, :c) ON CONFLICT DO NOTHING"
            ),
            {"p": principal_id, "c": fresh["id"]},
        )
    await db.commit()
    return {
        "corpus": name,
        "corpus_id": fresh["id"],
        "removed": report["removed"],
        "grants": len(grants),
        "not_removed": report["not_removed"],
    }


async def delete_corpus(db: AsyncSession, name: str) -> dict:
    """Erase a corpus and every row of memory the server holds for it.

    Returns a report: which tables lost how many rows, and whether anything was
    deleted at all. The report carries table *names* and counts only -- never a
    claim, a quote, a path or any other content -- so an erasure audit trail
    cannot itself become a copy of what it erased.

    Authorization is `manage_corpus` on a corpus this principal can already see.
    The corpus is resolved through `get_corpus_by_name`, so naming a corpus this
    identity may not reach is a refusal before any statement runs; the erasure
    function then re-checks the same transaction-local scope itself, so the two
    cannot disagree.

    The work is done by `mindpalace_erase_corpus`, a `SECURITY DEFINER`
    function. The runtime role holds no `DELETE` on any archive table, and the
    archive's append-only triggers still reject a `DELETE` that does not come
    through that function -- so there is exactly one way to erase, and it is this
    one. See `docs/security.md` for what this does *not* remove.
    """
    corpus = await get_corpus_by_name(db, name)
    if not corpus:
        return {"corpus": name, "deleted": False, "reason": "not_found", "removed": {}}
    require_permission(current_principal(), "manage_corpus", "delete a corpus")

    result = await db.execute(
        text("SELECT mindpalace_erase_corpus(:cid)"),
        {"cid": corpus["id"]},
    )
    report = result.scalar_one()
    await db.commit()
    return {
        "corpus": name,
        "corpus_id": report["corpus_id"],
        "deleted": bool(report["deleted"]),
        "reason": report.get("reason"),
        "removed": report.get("removed", {}),
        # Stated rather than implied. Receipts are files on customer machines and
        # backups are PostgreSQL's own; neither is reachable from here, and a
        # deletion report that implied otherwise would be worse than none.
        "not_removed": (
            "receipts and Memory Packs already exported by a customer, database "
            "backups and replicas, and any copy the customer stored elsewhere"
        ),
    }


async def corpus_stats(db: AsyncSession, name: str) -> Optional[dict]:
    """Ingestion/retrieval statistics for one corpus."""
    corpus = await get_corpus_by_name(db, name)
    if not corpus:
        return None
    result = await db.execute(
        text("""
            SELECT
                (SELECT count(*) FROM documents WHERE corpus_id = :cid),
                (SELECT count(*) FROM chunks c JOIN documents d ON c.doc_id = d.id
                 WHERE d.corpus_id = :cid),
                (SELECT coalesce(max(last_ingested), NULL) FROM ingestion_manifest m
                 JOIN documents d ON m.doc_id = d.id WHERE d.corpus_id = :cid)
        """),
        {"cid": corpus["id"]},
    )
    row = result.first()
    return {
        **corpus,
        "document_count": row[0],
        "chunk_count": row[1],
        "last_ingested": row[2],
    }
