"""Resource-level isolation: two people, one shared corpus, one private document.

Corpus-level isolation was the whole authorization model until migration `011`,
and it means that two principals holding the same corpus hold the same facts. That
is right for a team memory and wrong for anything else, so a document can now
carry an owner.

These tests assert the property against the **real least-privileged role**, over
**real pooled connections**, with the **acting principal** set the way the
application sets it. A test that set the scope itself, or queried with the
migration superuser, would prove nothing -- and the negative tests at the bottom
exist because that mistake is easy to make by accident.

The canary is a string shaped like the thing it stands in for. If it appears in a
response to the wrong principal, the failure names what leaked rather than only
that something did.
"""

from __future__ import annotations

import contextlib

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tenant_harness import (
    apply_as_runtime,
    requires_tenant_db,
    runtime_url,
)

pytestmark = [pytest.mark.asyncio, requires_tenant_db]

#: Alice writes this; Bob must never see it. Named so a failure says what leaked.
ALPHA_PRIVATE = "ALPHA_PRIVATE_RESOURCE the launch date is the 14th."
#: Alice writes this too, as shared. Bob must see it, or the feature is broken in
#: the other direction -- which is the failure a privacy feature usually has.
ALPHA_SHARED = "ALPHA_SHARED_RESOURCE the returns policy is 30 days."
BOB_PRIVATE = "BOB_PRIVATE_RESOURCE his roadmap moved to March."


def _corpus_id(name: str) -> str:
    import hashlib

    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def _hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def _principal(name: str, *corpora: str, role: str = "read_write"):
    """A principal holding exactly `corpora`, as the authenticator would build it."""
    from api.services import authz

    return authz.Principal(
        id=name,
        name=name,
        role=role,
        corpora=frozenset(corpora),
        corpus_ids=frozenset(corpora),
    )


async def _write(db, corpus: str, path: str, statement: str, owner: str | None) -> str:
    """Archive one document through the real ingestion service.

    The service, not hand-written SQL. A test that inserts rows directly can pass
    while the write path that production uses leaves ownership unset -- and
    ownership unset means *shared*, so the failure would be a silent privacy
    leak rather than an error.
    """
    from api.services.ingestion import IngestionService

    # `memory_enabled=True` so the archive path is taken. Without it a plain
    # Markdown file only populates the live projection and no
    # `memory_documents` row exists at all -- which would make every
    # assertion below pass against an empty table.
    # A `claims:` block, so the archive has a claim row to protect. Plain prose
    # ingests into the live projection only, and a test about claim isolation
    # that never creates a claim asserts nothing.
    return await IngestionService(memory_enabled=True).ingest_file(
        content=(
            f"---\ntitle: {path}\nclaims:\n"
            f"  - key: fact\n    value: {path}\n    claim: {statement}\n"
            f"    evidence: {statement}\n---\n# {path}\n\n{statement}"
        ),
        path=path,
        corpus_id=_corpus_id(corpus),
        owner_principal_id=owner,
    )


@pytest.fixture
async def shared_world():
    """One shared corpus, two principals, one private and one shared document.

    Also a *different* corpus that neither principal holds a grant on, so the
    cross-tenant tests below are about a boundary that is not the one under test.
    """
    from api.services import authz
    from api.services.corpora import create_corpus, get_corpus_by_name
    from api.services.db import admin_session_scope, principal_scope

    alpha, beta, gamma = "res-alpha", "res-beta", "res-gamma"
    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for name in (alpha, beta, gamma):
                # A previous run may have left the corpus behind. Erasing it
                # first keeps the fixture's starting state honest instead of
                # letting assertions run against a document from an earlier run.
                corpus_id = _corpus_id(name)
                await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
                await db.commit()
                if await get_corpus_by_name(db, name) is None:
                    await create_corpus(db, name, "resource isolation fixture")

            # Real principal rows. `documents.owner_principal_id` is a foreign key,
            # which is correct: ownership must name a principal that exists, or a
            # deleted account could orphan every document it owned.
            for who in ("alice", "bob"):
                await db.execute(
                    text(
                        "INSERT INTO memory_principals (id, name, key_hash, role) "
                        "VALUES (:i, :n, :h, 'read_write') ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "i": who,
                        "n": who,
                        "h": __import__("hashlib").sha256(who.encode()).hexdigest(),
                    },
                )
                for name in (alpha, beta):
                    await db.execute(
                        text(
                            "INSERT INTO memory_principal_corpora (principal_id, corpus_id) "
                            "VALUES (:p, :c) ON CONFLICT DO NOTHING"
                        ),
                        {"p": who, "c": _corpus_id(name)},
                    )
            await db.commit()

        alice = _principal("alice", _corpus_id(alpha))
        with principal_scope(alice):
            await _write(db, alpha, "notes/private.md", ALPHA_PRIVATE, owner="alice")
            await _write(db, alpha, "notes/shared.md", ALPHA_SHARED, owner=None)

        bob = _principal("bob", _corpus_id(alpha))
        with principal_scope(bob):
            await _write(db, alpha, "notes/bobs-private.md", BOB_PRIVATE, owner="bob")

    yield {
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "alice": alice,
        "bob": bob,
    }

    async with admin_session_scope() as db:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            for name in (alpha, beta, gamma):
                corpus_id = (
                    await db.execute(text("SELECT id FROM corpora WHERE name = :n"), {"n": name})
                ).scalar_one_or_none()
                if corpus_id:
                    await db.execute(text("SELECT mindpalace_erase_corpus(:c)"), {"c": corpus_id})
        await db.commit()


@contextlib.asynccontextmanager
async def _as(principal, scope: str):
    """One pooled connection, acting as `principal`, inside `scope`'s corpus."""
    engine = apply_as_runtime(runtime_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            with principal_scope_of(principal):
                async with db.begin():
                    yield db
    finally:
        await engine.dispose()


def principal_scope_of(principal):
    from api.services.db import principal_scope

    return principal_scope(principal)


async def test_the_owner_reads_their_own_private_document(shared_world):
    """The feature must work, not just deny.

    A privacy control that hides everything is indistinguishable from a broken
    one until somebody checks the positive case.
    """
    from api.services.resources import visible_document_paths

    async with _as(shared_world["alice"], shared_world["alpha"]) as db:
        paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
        claims = [row[0] for row in (await db.execute(text("SELECT claim FROM memory_claims")))]

    assert "notes/private.md" in paths, "the owner cannot read their own private document"
    assert "notes/shared.md" in paths, "a shared document must remain visible"
    assert any(
        ALPHA_PRIVATE in c for c in claims
    ), f"the owner's own claim did not come back; got {claims}"


async def test_a_peer_cannot_read_a_private_document_in_the_same_corpus(shared_world):
    """The gap this feature exists to close.

    Bob holds a grant on the same corpus, which under corpus-level isolation was
    sufficient to read everything Alice had written. It is no longer.
    """
    from api.services.resources import visible_document_paths

    async with _as(shared_world["bob"], shared_world["alpha"]) as db:
        paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
        claims = [row[0] for row in (await db.execute(text("SELECT claim FROM memory_claims")))]
        evidence = [row[0] for row in (await db.execute(text("SELECT quote FROM memory_evidence")))]
        versions = [
            row[0] for row in (await db.execute(text("SELECT content FROM memory_versions")))
        ]

    assert (
        "notes/private.md" not in paths
    ), "a peer's grant on the corpus must not expose a document another principal owns"
    assert "notes/shared.md" in paths, "shared documents must stay shared"
    for canary in (ALPHA_PRIVATE,):
        assert not any(canary in text for text in claims), f"{canary} leaked through claims"
        assert not any(canary in text for text in evidence), f"{canary} leaked through evidence"
        assert not any(canary in text for text in versions), f"{canary} leaked through history"


async def test_a_private_document_is_not_reachable_by_guessing_its_id(shared_world):
    """A document id derived from a name is not authorization.

    `memory_document_id` is `sha256(corpus || ':' || path)`, so anyone who knows or
    guesses the path can compute the id. This is the test that would catch that
    being mistaken for a capability.
    """
    from api.services.resources import document_identity

    identity = document_identity(_corpus_id(shared_world["alpha"]), "notes/private.md")

    async with _as(shared_world["bob"], shared_world["alpha"]) as db:
        found = (
            await db.execute(
                text("SELECT count(*) FROM memory_documents WHERE id = :i"), {"i": identity}
            )
        ).scalar()
        versions = (
            await db.execute(
                text("SELECT count(*) FROM memory_versions WHERE memory_document_id = :i"),
                {"i": identity},
            )
        ).scalar()

    assert found == 0, "a derived document id resolved for a principal that holds no grant"
    assert versions == 0, "history resolved through a derived document id"


async def test_derived_rows_inherit_the_boundary(shared_world):
    """Every descendant is checked, not only the root.

    A control applied to `memory_documents` alone would leave claims, evidence,
    chunks and history readable, which is the shape this failure usually takes.
    """
    identity_sql = "encode(sha256(convert_to(:c || ':' || 'notes/private.md', 'UTF8')), 'hex')"

    async with _as(shared_world["bob"], shared_world["alpha"]) as db:
        corpus = _corpus_id(shared_world["alpha"])
        for table, column in (
            ("memory_claims", "memory_document_id"),
            ("memory_versions", "memory_document_id"),
            ("memory_chunks", "version_id"),
            ("memory_evidence", "version_id"),
            ("memory_claim_embeddings", "claim_id"),
        ):
            if table in ("memory_chunks", "memory_evidence"):
                # Reached through a version rather than naming a document column.
                count = (
                    await db.execute(
                        text(
                            f"SELECT count(*) FROM {table} t WHERE EXISTS ("
                            "  SELECT 1 FROM memory_versions v WHERE v.id = t."
                            f"{column} AND v.memory_document_id = {identity_sql})"
                        ),
                        {"c": corpus},
                    )
                ).scalar()
            elif table == "memory_claim_embeddings":
                count = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM memory_claim_embeddings t WHERE EXISTS ("
                            "  SELECT 1 FROM memory_claims cl WHERE cl.id = t.claim_id "
                            f"  AND cl.memory_document_id = {identity_sql})"
                        ),
                        {"c": corpus},
                    )
                ).scalar()
            else:
                count = (
                    await db.execute(
                        text(f"SELECT count(*) FROM {table} WHERE {column} = {identity_sql}"),
                        {"c": corpus},
                    )
                ).scalar()
            assert count == 0, f"{table} leaked rows from a private document"


async def test_an_explicit_grant_shares_one_document_and_nothing_else(shared_world):
    """The "shared" half: naming a document, never a prefix, never a corpus."""
    from api.services.db import admin_session_scope, principal_scope
    from api.services.resources import share_document, unshare_document, visible_document_paths

    async with admin_session_scope() as db:
        with principal_scope(shared_world["alice"]):  # noqa: F841
            assert await share_document(
                db, _corpus_id(shared_world["alpha"]), "notes/private.md", "bob"
            )

    async with _as(shared_world["bob"], shared_world["alpha"]) as db:
        paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
    assert "notes/private.md" in paths, "an explicit grant did not take effect"

    # Revocation is immediate, and leaves the shared document alone.
    async with admin_session_scope() as db:
        with principal_scope(shared_world["alice"]):
            assert await unshare_document(
                db, _corpus_id(shared_world["alpha"]), "notes/private.md", "bob"
            )

    async with _as(shared_world["bob"], shared_world["alpha"]) as db:
        paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
    assert "notes/private.md" not in paths, "revocation did not take effect on the next request"
    assert "notes/shared.md" in paths, "revoking one grant removed an unrelated shared document"


async def test_a_shared_document_is_visible_to_everyone_holding_the_corpus(shared_world):
    """The default must not have silently become private.

    Privacy is opt-in. If it were not, every existing corpus would have become
    unreadable to its own peers, which is a data-loss bug wearing a privacy hat.
    """
    from api.services.resources import visible_document_paths

    for who in ("alice", "bob"):
        async with _as(shared_world[who], shared_world["alpha"]) as db:
            paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
        assert "notes/shared.md" in paths, f"{who} lost access to a shared document"


async def test_a_missing_acting_principal_fails_closed(shared_world):
    """No resource identity means no access to anything owned, not full access.

    The failure mode to guard is a query path that forgets to establish the
    principal and thereby silently *widens* access. It must narrow it.

    Driven through `principal_scope` rather than raw `set_config`, because both
    settings are established together by `after_begin` on every transaction.
    Setting them by hand races that, and a test which won the race by luck would
    not be testing the mechanism it claims to test.
    """
    from api.services import authz
    from api.services.resources import document_identity, visible_document_paths

    identityless = authz.Principal(
        id="",
        name="",
        role="read_write",
        corpora=frozenset({shared_world["alpha"]}),
        corpus_ids=frozenset({_corpus_id(shared_world["alpha"])}),
    )

    async with _as(identityless, shared_world["alpha"]) as db:
        paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
        owned = document_identity(_corpus_id(shared_world["alpha"]), "notes/private.md")
        resolved = (
            await db.execute(
                text("SELECT count(*) FROM memory_documents WHERE id = :i"), {"i": owned}
            )
        ).scalar()

    assert (
        "notes/private.md" not in paths
    ), "a document with an owner must not be visible to a caller with no identity"
    assert resolved == 0, (
        "a derived document id resolved for a caller with no identity; a path "
        "that forgets who is asking must narrow access, never widen it"
    )
    assert (
        "notes/shared.md" in paths
    ), f"a shared document must stay visible to a corpus holder; saw {paths}"


async def test_ownership_cannot_be_moved_after_the_fact(shared_world):
    """The archive is append-only, and that now covers ownership too.

    Not hypothetical: the obvious way to write these fixtures -- insert, then
    `UPDATE ... SET owner_principal_id` -- is refused by the trigger migration
    `004` installed. That is the correct outcome. Ownership records who first
    wrote the content; a later write by a peer does not re-home it, and sharing
    is an explicit grant rather than a side effect of writing.
    """
    from api.services.db import admin_session_scope, principal_scope

    from api.services.resources import document_identity

    corpus = _corpus_id(shared_world["alpha"])
    # The schema checks that a document's id *is* the hash of its (corpus, path),
    # so the row has to be written with the derived id -- another constraint that
    # makes a fabricated identifier impossible.
    identity = document_identity(corpus, "notes/borrowed.md")

    async with admin_session_scope() as db:
        with principal_scope(shared_world["alice"]):
            await db.execute(
                text(
                    "INSERT INTO memory_documents (corpus_id, id, path) "
                    "VALUES (:c, :i, 'notes/borrowed.md') ON CONFLICT DO NOTHING"
                ),
                {"c": corpus, "i": identity},
            )
            await db.commit()
            with pytest.raises(sa.exc.IntegrityError, match="append-only"):
                await db.execute(
                    text(
                        "UPDATE memory_documents SET owner_principal_id = :o "
                        "WHERE corpus_id = :c AND id = :i"
                    ),
                    {"o": "alice", "c": corpus, "i": identity},
                )
            await db.rollback()


# ---------------------------------------------------------------------------
# Negative controls
# ---------------------------------------------------------------------------


async def test_the_same_assertion_fails_when_the_policy_is_disabled(shared_world):
    """The control is load-bearing: disable it and the leak reappears.

    Disabling row-level security on the archive root is the crudest possible
    removal. If the peer test above still passed with it off, then the peer test
    was asserting something other than isolation -- which is the failure mode a
    hand-written policy test has, and the reason this one exists.
    """
    from api.services.resources import visible_document_paths
    from tenant_harness import sync_url

    def _set(enabled: bool):
        engine = sa.create_engine(sync_url(), isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(
                    text(
                        f"ALTER TABLE memory_documents "
                        f"{'ENABLE' if enabled else 'DISABLE'} ROW LEVEL SECURITY"
                    )
                )
        finally:
            engine.dispose()

    _set(False)
    try:
        async with _as(shared_world["bob"], shared_world["alpha"]) as db:
            paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
        assert "notes/private.md" in paths, (
            "with row-level security disabled the private document must be "
            "visible, or the isolation tests prove nothing"
        )
    finally:
        _set(True)

    # And the guarantee is restored, which is the half that matters to the suite.
    async with _as(shared_world["bob"], shared_world["alpha"]) as db:
        paths = await visible_document_paths(db, _corpus_id(shared_world["alpha"]))
    assert "notes/private.md" not in paths
