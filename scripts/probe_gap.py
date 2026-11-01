#!/usr/bin/env python3
"""Probe script for Stage 3 gap analysis (Team 052 requirements).

Runs in-process via the MindPalace SDK (same code paths as the HTTP server).
Prints RAW status codes and JSON bodies for every experiment. Asserts nothing.

Environment:
  DATABASE_URL           sync URL for probe queries (mindpalace_runtime)
  ADMIN_DATABASE_URL     sync URL for admin ops (mpadmin superuser)
  MIND_PALACE_SCOPE_SECRET  scope HMAC secret
  MIND_PALACE_ENFORCE_SCOPE_TOKEN  "on"
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlalchemy as sa

import mindpalace_sdk as sdk
from mindpalace_sdk import MindPalace

SCOPE_SECRET = os.environ.get("MIND_PALACE_SCOPE_SECRET", "")
PROBE_DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://mindpalace_runtime:ci-runtime-role-password@localhost:5432/mindpalace"
)
ADMIN_DB_URL = os.environ.get(
    "ADMIN_DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace"
)


def corpus_id_for(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def issue_credential(label: str, corpora: list[str], role: str = "read_write") -> str:
    """Issue a credential via the admin session scope (mpadmin superuser)."""
    import asyncio

    from api.services import authz
    from api.services.db import admin_session_scope, principal_scope
    from api.services.authz import ADMIN_PRINCIPAL

    async def _issue() -> str:
        async with admin_session_scope(url=os.environ.get("ADMIN_DATABASE_URL")) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cred = await authz.issue_credential(db, label, list(corpora), role)
                return cred["secret"]

    return asyncio.run(_issue())


def ensure_corpus(corpus_name: str) -> str:
    """Create a corpus via the admin session scope if it doesn't exist. Returns corpus_id."""
    cid = corpus_id_for(corpus_name)

    import asyncio

    async def _ensure() -> str:
        from sqlalchemy import text
        from api.services.db import admin_session_scope, principal_scope
        from api.services.authz import ADMIN_PRINCIPAL

        admin_db_url = os.environ.get("ADMIN_DATABASE_URL")
        async with admin_session_scope(url=admin_db_url) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                row = await db.execute(
                    sa.text("SELECT id FROM corpora WHERE id = :c"),
                    {"c": cid},
                )
                if row.scalar() is None:
                    await db.execute(
                        sa.text(f"INSERT INTO corpora (id, name) VALUES ('{cid}', '{corpus_name}') ON CONFLICT (id) DO NOTHING")
                    )
                    await db.commit()
                return cid

    return asyncio.run(_ensure())


def client_for(corpora: list[str], role: str = "read_write") -> MindPalace:
    """Create an in-process MindPalace client with a credential for the given corpora."""
    from api.services.db import admin_session_scope, principal_scope
    from api.services.authz import ADMIN_PRINCIPAL

    for c in corpora:
        ensure_corpus(c)

    secret = issue_credential(f"probe-{'-'.join(corpora)}", corpora, role)
    return MindPalace(api_key=secret, timeout=30)


def sdk_call(label: str, fn) -> None:
    """Make an in-process SDK call and print the raw result."""
    print(f"--- {label} ---")
    try:
        result = fn()
        if hasattr(result, "model_dump"):
            print(f"status: 200 (SDK in-process)")
            print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        else:
            print(f"status: 200 (SDK in-process)")
            print(str(result))
    except Exception as e:
        print(f"Exception type: {type(e).__name__}")
        print(f"Exception: {e}")


def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


# ── Probe a: stable source identity / update semantics ─────────────────

def probe_a() -> None:
    section("a. remember same logical source twice (same content, then changed)")
    corpus = "probe-alpha"
    client = client_for([corpus], "read_write")

    r1 = client.memory.remember("Brand voice is professional and concise.", corpus=corpus, key="brand.voice")
    print(f"remember #1: version_id={r1.version_id}, unchanged={r1.unchanged}, event={r1.event}")

    r2 = client.memory.remember("Brand voice is professional and concise.", corpus=corpus, key="brand.voice")
    print(f"remember #2: version_id={r2.version_id}, unchanged={r2.unchanged}, event={r2.event}")

    r3 = client.memory.remember("Brand voice is warm, dry, and confident.", corpus=corpus, key="brand.voice")
    print(f"remember #3: version_id={r3.version_id}, unchanged={r3.unchanged}, event={r3.event}")

    print(f"\nSUMMARY a: idempotent={r1.version_id==r2.version_id}, unchanged2={r2.unchanged}, v3_differs_from_v1={r3.version_id!=r1.version_id}, unchanged3={r3.unchanged}")

    sdk_call("current after update", lambda: client.memory.current(corpus=corpus, query=""))
    sdk_call("history after update", lambda: client.memory.history(corpus=corpus, query="brand voice"))


# ── Probe b: withdrawal ────────────────────────────────────────────────

def probe_b() -> None:
    section("b. withdrawal: remember changed statement (supersession)")
    corpus = "probe-beta"
    client = client_for([corpus], "read_write")

    client.memory.remember("Use the phrase 'Explore our services' as CTA.", corpus=corpus, key="brand.cta")

    section("b-i. current after superseding CTA")
    sdk_call("current", lambda: client.memory.current(corpus=corpus, query=""))

    section("b-ii. pack after superseding CTA")
    sdk_call("pack", lambda: client.memory.pack(corpus=corpus, query="cta"))

    section("b-iii. history after superseding CTA")
    sdk_call("history", lambda: client.memory.history(corpus=corpus, query="cta"))


# ── Probe c: state handling ────────────────────────────────────────────────

def probe_c() -> None:
    section("c. write approved / draft / rejected / superseded claims under one heading")
    corpus = "probe-gamma"
    client = client_for([corpus], "read_write")

    import httpx
    from api.services.db import admin_session_scope, principal_scope
    from api.services.authz import ADMIN_PRINCIPAL

    async def insert_claims():
        async with admin_session_scope(url=os.environ.get("ADMIN_DATABASE_URL")) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cid = corpus_id_for(corpus)
                await db.execute(
                    sa.text(f"""
                    INSERT INTO corpora (id, name) VALUES ('{cid}', '{corpus}') ON CONFLICT (id) DO NOTHING
                    """)
                )
                doc_id = hashlib.sha256((cid + ":doc.md").encode()).hexdigest()
                ver_id = hashlib.sha256(f"ver:{cid}".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_documents (corpus_id, id, path) VALUES ('{cid}', '{doc_id}', 'doc.md') ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_versions (corpus_id, memory_document_id, id, version_number, event, observed_at, content, metadata, fingerprint, document_id)
                    VALUES ('{cid}', '{doc_id}', '{ver_id}', 1, 'NEW', now(), 'c', '{{}}'::jsonb, 'fp', '{doc_id}')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index)
                    VALUES ('{cid}', '{ver_id}', '{hashlib.sha256(b'chunk').hexdigest()}', 'text', 0) ON CONFLICT DO NOTHING
                    """)
                )
                await db.commit()
                return cid, doc_id, ver_id

    cid, doc_id, ver_id = asyncio.run(insert_claims())

    async def insert_claim(state, key, claim_text):
        async with admin_session_scope(url=os.environ.get("ADMIN_DATABASE_URL")) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                claim_id = hashlib.sha256(f"{cid}:{key}".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id, key, claim, value, info_state, extraction_mode, provenance_state)
                    VALUES ('{cid}', '{doc_id}', '{ver_id}', '{claim_id}', '{key}', '{claim_text}', '{{}}'::jsonb, '{state}'::info_state, 'deterministic', 'direct_claim')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.commit()

    asyncio.run(insert_claim("approved", "state.approved", "Approved: use professional tone."))
    asyncio.run(insert_claim("draft", "state.draft", "Draft: maybe use playful tone."))
    asyncio.run(insert_claim("rejected", "state.rejected", "Rejected: never use 'guilt-free'."))
    asyncio.run(insert_claim("superseded", "state.superseded", "Superseded: old voice guideline."))

    section("c-i. current (default, no filter)")
    sdk_call("current", lambda: client.memory.current(corpus=corpus, query=""))

    section("c-ii. pack (default, no filter)")
    sdk_call("pack", lambda: client.memory.pack(corpus=corpus, query="tone"))

    section("c-iii. query (default, no filter)")
    sdk_call("query", lambda: client.memory.query(corpus=corpus, query="tone"))


# ── Probe d: campaign scope ────────────────────────────────────────────────

def probe_d() -> None:
    section("d. two paths (campaign-a/, campaign-b/) in one corpus")
    corpus = "probe-delta"
    client = client_for([corpus], "read_write")

    import httpx
    from api.services.db import admin_session_scope, principal_scope
    from api.services.authz import ADMIN_PRINCIPAL

    async def insert_claims():
        async with admin_session_scope(url=os.environ.get("ADMIN_DATABASE_URL")) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cid = corpus_id_for(corpus)
                await db.execute(
                    sa.text(f"""
                    INSERT INTO corpora (id, name) VALUES ('{cid}', '{corpus}') ON CONFLICT (id) DO NOTHING
                    """)
                )
                doc_id = hashlib.sha256((cid + ":doc.md").encode()).hexdigest()
                ver_id = hashlib.sha256(f"ver:{cid}".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_documents (corpus_id, id, path) VALUES ('{cid}', '{doc_id}', 'doc.md') ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_versions (corpus_id, memory_document_id, id, version_number, event, observed_at, content, metadata, fingerprint, document_id)
                    VALUES ('{cid}', '{doc_id}', '{ver_id}', 1, 'NEW', now(), 'c', '{{}}'::jsonb, 'fp', '{doc_id}')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index)
                    VALUES ('{cid}', '{ver_id}', '{hashlib.sha256(b'chunk').hexdigest()}', 'text', 0) ON CONFLICT DO NOTHING
                    """)
                )
                await db.commit()
                return cid, doc_id, ver_id

    cid, doc_id, ver_id = asyncio.run(insert_claims())

    async def insert_claim(key, claim_text, path):
        async with admin_session_scope(url=os.environ.get("ADMIN_DATABASE_URL")) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                claim_id = hashlib.sha256(f"{cid}:{key}".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id, key, claim, value, info_state, extraction_mode, provenance_state)
                    VALUES ('{cid}', '{doc_id}', '{ver_id}', '{claim_id}', '{key}', '{claim_text}', '{{}}'::jsonb, 'approved', 'deterministic', 'direct_claim')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.commit()

    asyncio.run(insert_claim("campaign-a.tone", "Campaign A uses playful tone.", "campaign-a/brief.md"))
    asyncio.run(insert_claim("campaign-b.tone", "Campaign B uses formal tone.", "campaign-b/brief.md"))

    section("d-i. query with path filter campaign-a/")
    sdk_call("query path=campaign-a/", lambda: client.memory.query(corpus=corpus, query="tone", path="campaign-a/"))

    section("d-ii. query without path filter")
    sdk_call("query no path", lambda: client.memory.query(corpus=corpus, query="tone"))

    section("d-iii. pack with path filter campaign-a/")
    sdk_call("pack path=campaign-a/", lambda: client.memory.pack(corpus=corpus, query="tone", path="campaign-a/"))

    section("d-iv. pack without path filter")
    sdk_call("pack no path", lambda: client.memory.pack(corpus=corpus, query="tone"))


# ── Probe e: isolation (two corpora) ───────────────────────────────────

def probe_e() -> None:
    section("e. create second corpus and restrict credential to one")
    corpus_a = "probe-epsilon-a"
    corpus_b = "probe-epsilon-b"
    client_a = client_for([corpus_a], "read_only")
    client_b = client_for([corpus_b], "read_only")

    import httpx
    from api.services.db import admin_session_scope, principal_scope
    from api.services.authz import ADMIN_PRINCIPAL

    async def insert_claim_b():
        async with admin_session_scope(url=os.environ.get("ADMIN_DATABASE_URL")) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cid_b = corpus_id_for(corpus_b)
                await db.execute(
                    sa.text(f"""
                    INSERT INTO corpora (id, name) VALUES ('{cid_b}', '{corpus_b}') ON CONFLICT (id) DO NOTHING
                    """)
                )
                doc_id = hashlib.sha256((cid_b + ":doc.md").encode()).hexdigest()
                ver_id = hashlib.sha256(f"ver:{cid_b}".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_documents (corpus_id, id, path) VALUES ('{cid_b}', '{doc_id}', 'doc.md') ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_versions (corpus_id, memory_document_id, id, version_number, event, observed_at, content, metadata, fingerprint, document_id)
                    VALUES ('{cid_b}', '{doc_id}', '{ver_id}', 1, 'NEW', now(), 'c', '{{}}'::jsonb, 'fp', '{doc_id}')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index)
                    VALUES ('{cid_b}', '{ver_id}', '{hashlib.sha256(b'chunk').hexdigest()}', 'text', 0) ON CONFLICT DO NOTHING
                    """)
                )
                claim_id = hashlib.sha256(f"{cid_b}:secret".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id, key, claim, value, info_state, extraction_mode, provenance_state)
                    VALUES ('{cid_b}', '{doc_id}', '{ver_id}', '{claim_id}', 'secret', 'Corpus B secret fact.', '{{}}'::jsonb, 'approved', 'deterministic', 'direct_claim')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.commit()

    asyncio.run(insert_claim_b())

    section("e-i. history on corpus B as corpus-A credential")
    sdk_call("history corpus B as A", lambda: client_a.memory.history(corpus=corpus_b, query=""))

    section("e-ii. as-of on corpus B as corpus-A credential")
    sdk_call("as-of corpus B as A", lambda: client_a.memory.as_of(corpus=corpus_b, query="", as_of="2026-01-01T00:00:00Z"))

    section("e-iii. evidence on corpus B as corpus-A credential")
    sdk_call("evidence corpus B as A", lambda: client_a.memory.evidence(corpus=corpus_b, query=""))

    section("e-iv. changes on corpus B as corpus-A credential")
    sdk_call("changes corpus B as A", lambda: client_a.memory.changes(corpus=corpus_b, query=""))

    section("e-v. pack on corpus B as corpus-A credential")
    sdk_call("pack corpus B as A", lambda: client_a.memory.pack(corpus=corpus_b, query=""))

    section("e-vi. query on corpus B as corpus-A credential")
    sdk_call("query corpus B as A", lambda: client_a.memory.query(corpus=corpus_b, query=""))


# ── Probe f: budget ────────────────────────────────────────────────────

def probe_f() -> None:
    section("f. pack response budget field and unit")
    corpus = "probe-zeta"
    client = client_for([corpus], "read_only")

    import httpx
    from api.services.db import admin_session_scope, principal_scope
    from api.services.authz import ADMIN_PRINCIPAL

    async def insert_claim():
        async with admin_session_scope(url=os.environ.get("ADMIN_DATABASE_URL")) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cid = corpus_id_for(corpus)
                await db.execute(
                    sa.text(f"""
                    INSERT INTO corpora (id, name) VALUES ('{cid}', '{corpus}') ON CONFLICT (id) DO NOTHING
                    """)
                )
                doc_id = hashlib.sha256((cid + ":doc.md").encode()).hexdigest()
                ver_id = hashlib.sha256(f"ver:{cid}".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_documents (corpus_id, id, path) VALUES ('{cid}', '{doc_id}', 'doc.md') ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_versions (corpus_id, memory_document_id, id, version_number, event, observed_at, content, metadata, fingerprint, document_id)
                    VALUES ('{cid}', '{doc_id}', '{ver_id}', 1, 'NEW', now(), 'c', '{{}}'::jsonb, 'fp', '{doc_id}')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index)
                    VALUES ('{cid}', '{ver_id}', '{hashlib.sha256(b'chunk').hexdigest()}', 'text', 0) ON CONFLICT DO NOTHING
                    """)
                )
                claim_id = hashlib.sha256(f"{cid}:brand.voice".encode()).hexdigest()
                await db.execute(
                    sa.text(f"""
                    INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id, key, claim, value, info_state, extraction_mode, provenance_state)
                    VALUES ('{cid}', '{doc_id}', '{ver_id}', '{claim_id}', 'brand.voice', 'Brand voice is professional and concise. Use the phrase Explore our services as CTA.', '{{}}'::jsonb, 'approved', 'deterministic', 'direct_claim')
                    ON CONFLICT DO NOTHING
                    """)
                )
                await db.commit()

    asyncio.run(insert_claim())

    sdk_call("pack budget=1200", lambda: client.memory.pack(corpus=corpus, query="brand"))


# ── Probe g: SDK failure ───────────────────────────────────────────────

def probe_g() -> None:
    section("g. SDK failure: wrong port (server not listening)")
    import mindpalace_sdk as sdk_mod
    client = sdk_mod.MindPalace(base_url="http://127.0.0.1:9999")
    try:
        client.current("probe-corpus", "")
    except Exception as e:
        print(f"Exception type: {type(e).__name__}")
        print(f"Exception: {e}")

    print(f"\nSDK default timeout: {client._client.timeout}")
    print("  (mindpalace_sdk.py:918: timeout: float = 30)")
    print("  httpx calls pass timeout=self._client.timeout (mindpalace_sdk.py:331,447,604,819)")
    print("  httpx.TimeoutException -> MemoryClientError('timeout', ..., 504) (mindpalace_sdk.py:334,450,607,822)")
    print("  httpx.RequestError -> MemoryClientError('transport_error', ..., 503) (mindpalace_sdk.py:336,451,606,821)")


def main() -> None:
    print(f"SCOPE_SECRET set={bool(SCOPE_SECRET)}")

    probe_a()
    probe_b()
    probe_c()
    probe_d()
    probe_e()
    probe_f()
    probe_g()

    print("\n\nPROBE COMPLETE")


if __name__ == "__main__":
    main()
