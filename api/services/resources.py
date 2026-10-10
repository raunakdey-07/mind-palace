"""Resource-level authorization: private documents, and explicit sharing.

Corpus-level isolation answers "may this caller see this corpus?". It does not
answer "may this caller see *this document*, when the corpus is shared?" Two
people in one workspace shared everything until this module existed.

## The model, in three sentences

A document's **owner** is a principal, or `NULL` for shared. A document with an
owner is visible to that owner, to any principal holding an explicit grant, and to
nobody else in the corpus -- including other members of the same workspace. Every
descendant row inherits: versions, claims, evidence, chunks, embeddings, snapshot
membership, and the seals over it.

## What this is not

It is not per-*claim* isolation. The archive's own identity function makes a
document's identity `(corpus, path)`, so a document is the resource, and a claim
inherits from the document that carries it. Making a claim private on its own
means threading ownership through supersession and conflict closure, which would
change what counts as authoritative memory. That is a separate decision and is not
smuggled in here.

It also does not contain a compromised application process. The acting principal
reaches PostgreSQL as a session setting the application writes, exactly like the
tenant scope, and carries exactly the same limitation. This defends against a
query that forgot a predicate, which is the failure that actually happens.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text

#: A shared document is the default and the documented shape. Naming it as a
#: constant keeps the two spellings from drifting in an API and a CLI.
SHARED = "shared"
PRIVATE = "private"
VISIBILITIES = (SHARED, PRIVATE)


def document_identity(corpus_id: str, path: str) -> str:
    """The archive's own document id for `(corpus, path)`.

    Computed rather than looked up, because the authorization path needs to know
    which document a grant names *before* it can be allowed to see it. That is
    the whole reason a corpus id is a hash of a name too: an identifier derived
    from a name can be checked without a lookup, and a lookup would itself be
    subject to the policy being enforced.
    """
    return hashlib.sha256(f"{corpus_id}:{path}".encode()).hexdigest()


def owner_for(principal, private: bool) -> str | None:
    """The `owner_principal_id` for a write, or `None` for a shared one.

    `None` is not "no owner" in the sense of "unowned": it is the marker that makes
    a document visible to everyone holding the corpus, which is what every
    document written before this feature existed already is.
    """
    if not private:
        return None
    principal_id = getattr(principal, "id", None)
    if not principal_id:
        # The local, unrestricted principal has no id and owns nothing. Marking
        # its writes private would produce a document nobody could read back,
        # including itself, because the predicate compares against an id that
        # does not exist. Refused loudly rather than silently shared, which would
        # be the opposite of what the caller asked for.
        raise ValueError(
            "a private document needs an authenticated principal to own it; the "
            "current one has no identity. Set MIND_PALACE_AUTH_MODE=required and "
            "present a credential, or write the document as shared."
        )
    return principal_id


async def share_document(db, corpus_id: str, path: str, principal_id: str) -> bool:
    """Grant one principal access to one document. Idempotent.

    Explicit and narrow: it names a document, never a prefix and never a corpus.
    A grant to a corpus would be the existing corpus grant, which already exists
    and is already explicit.
    """
    identity = document_identity(corpus_id, path)
    result = await db.execute(
        text("""
        INSERT INTO memory_resource_grants (principal_id, corpus_id, memory_document_id)
        VALUES (:p, :c, :d)
        ON CONFLICT DO NOTHING
    """),
        {"p": principal_id, "c": corpus_id, "d": identity},
    )
    await db.commit()
    return bool(result.rowcount)


async def unshare_document(db, corpus_id: str, path: str, principal_id: str) -> bool:
    """Revoke a document grant. Idempotent; returns whether one was removed.

    Takes effect on the next request, exactly like a corpus grant: the row is
    deleted, the policy reads no cache, and nothing holds an authorization
    decision past a transaction.
    """
    result = await db.execute(
        text("""
        DELETE FROM memory_resource_grants
        WHERE principal_id = :p AND corpus_id = :c AND memory_document_id = :d
    """),
        {"p": principal_id, "c": corpus_id, "d": document_identity(corpus_id, path)},
    )
    await db.commit()
    return bool(result.rowcount)


async def visible_document_paths(db, corpus_id: str) -> list[str]:
    """Every path in a corpus this caller may see.

    Routed through a plain `SELECT` rather than an administrative query on
    purpose: if this function could see a document the caller's own queries
    cannot, it would be a disclosure with a convenient name attached to it.
    """
    result = await db.execute(
        text("SELECT path FROM memory_documents WHERE corpus_id = :c ORDER BY path"),
        {"c": corpus_id},
    )
    return [row[0] for row in result]
