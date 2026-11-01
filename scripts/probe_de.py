#!/usr/bin/env python3
"""Probe d and e for Stage 3 gap analysis (marketing isolation).

Runs in-process via MindPalace SDK. Prints RAW status codes and JSON bodies.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os

import mindpalace_sdk as sdk
from mindpalace_sdk import MindPalace

SCOPE_SECRET = os.environ.get("MIND_PALACE_SCOPE_SECRET", "")
ADMIN_DB_URL = os.environ.get("ADMIN_DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")


def corpus_id_for(name):
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def ensure_corpus(corpus_name):
    cid = corpus_id_for(corpus_name)
    async def _ensure():
        from sqlalchemy import text
        from api.services.db import admin_session_scope, principal_scope
        from api.services.authz import ADMIN_PRINCIPAL
        async with admin_session_scope(url=ADMIN_DB_URL) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                row = await db.execute(text("SELECT id FROM corpora WHERE id = :c"), {"c": cid})
                if row.scalar() is None:
                    await db.execute(text(f"INSERT INTO corpora (id, name) VALUES ('{cid}', '{corpus_name}') ON CONFLICT (id) DO NOTHING"))
                    await db.commit()
        return cid
    return asyncio.run(_ensure())


def issue_cred(label, corpora, role="read_only"):
    async def _issue():
        from api.services import authz
        from api.services.db import admin_session_scope, principal_scope
        from api.services.authz import ADMIN_PRINCIPAL
        async with admin_session_scope(url=ADMIN_DB_URL) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cred = await authz.issue_credential(db, label, list(corpora), role)
                return cred["secret"]
    return asyncio.run(_issue())


def seed_doc(corpus, doc_path, claim_text, state="approved"):
    cid = corpus_id_for(corpus)
    async def _seed():
        from sqlalchemy import text
        from api.services.db import admin_session_scope, principal_scope
        from api.services.authz import ADMIN_PRINCIPAL
        async with admin_session_scope(url=ADMIN_DB_URL) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                doc_id = hashlib.sha256((cid + ":" + doc_path).encode()).hexdigest()
                ver_id = hashlib.sha256(f"ver:{cid}:{doc_path}".encode()).hexdigest()
                chunk_id = hashlib.sha256(f"chunk:{cid}:{doc_path}".encode()).hexdigest()
                claim_id = hashlib.sha256(f"claim:{cid}:{doc_path}".encode()).hexdigest()
                eid = hashlib.sha256(f"ev:{cid}:{doc_path}".encode()).hexdigest()
                await db.execute(text(f"INSERT INTO memory_documents (corpus_id, id, path) VALUES ('{cid}', '{doc_id}', '{doc_path}') ON CONFLICT DO NOTHING"))
                await db.execute(text(f"INSERT INTO memory_versions (corpus_id, memory_document_id, id, version_number, event, observed_at, content, metadata, fingerprint, document_id) VALUES ('{cid}', '{doc_id}', '{ver_id}', 1, 'NEW', now(), 'c', '{{}}'::jsonb, 'fp', '{doc_id}') ON CONFLICT DO NOTHING"))
                await db.execute(text(f"INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index) VALUES ('{cid}', '{ver_id}', '{chunk_id}', 'campaign tone text here', 0) ON CONFLICT DO NOTHING"))
                await db.execute(text(f"INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id, key, claim, value, info_state, extraction_mode, provenance_state) VALUES ('{cid}', '{doc_id}', '{ver_id}', '{claim_id}', 'tone', '{claim_text}', '{{}}'::jsonb, '{state}', 'deterministic', 'direct_claim') ON CONFLICT DO NOTHING"))
                await db.execute(text(f"INSERT INTO memory_evidence (corpus_id, version_id, id, claim_id, chunk_id, quote, start_offset, end_offset) VALUES ('{cid}', '{ver_id}', '{eid}', '{claim_id}', '{chunk_id}', 'campaign', 0, 8) ON CONFLICT DO NOTHING"))
                await db.commit()
                return doc_id, ver_id
    return asyncio.run(_seed())


def sdk_call(label, fn):
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
        if hasattr(e, "status_code"):
            print(f"status_code: {e.status_code}")


def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


# ============================================================
# PROBE D: one corpus, campaign-a/ and campaign-b/ paths
# ============================================================
section("D. one corpus, documents under campaign-a/ and campaign-b/")
corpus = "probe-delta"
ensure_corpus(corpus)
client = MindPalace(api_key=issue_cred("probe-delta-rw", [corpus], "read_write"), timeout=30)

# Seed documents under campaign-a/ and campaign-b/
seed_doc(corpus, "campaign-a/brief.md", "Campaign A uses playful tone.")
seed_doc(corpus, "campaign-b/brief.md", "Campaign B uses formal tone.")

# Print history to see stored path format
section("D-0. history (to see stored path format)")
sdk_call("history (all)", lambda: client.memory.history(corpus=corpus, query=""))

# Now query/pack/current/conflicts with and without path scoping
section("D-1. query with path=campaign-a/")
sdk_call("query path=campaign-a/", lambda: client.memory.query(corpus=corpus, query="tone", path="campaign-a/"))

section("D-2. query without path filter")
sdk_call("query no path", lambda: client.memory.query(corpus=corpus, query="tone"))

section("D-3. pack with path=campaign-a/")
sdk_call("pack path=campaign-a/", lambda: client.memory.pack(corpus=corpus, query="tone", path="campaign-a/"))

section("D-4. pack without path filter")
sdk_call("pack no path", lambda: client.memory.pack(corpus=corpus, query="tone"))

section("D-5. current with path=campaign-a/")
sdk_call("current path=campaign-a/", lambda: client.memory.current(corpus=corpus, query="", path="campaign-a/"))

section("D-6. current without path filter")
sdk_call("current no path", lambda: client.memory.current(corpus=corpus, query=""))

section("D-7. conflicts with path=campaign-a/")
sdk_call("conflicts path=campaign-a/", lambda: client.memory.conflicts(corpus=corpus, query="tone", path="campaign-a/"))

section("D-8. conflicts without path filter")
sdk_call("conflicts no path", lambda: client.memory.conflicts(corpus=corpus, query="tone"))

# ============================================================
# PROBE E: two corpora, credential limited to one
# ============================================================
section("E. two corpora, credential limited to corpus A")
corpus_a = "probe-epsilon-a"
corpus_b = "probe-epsilon-b"
ensure_corpus(corpus_a)
ensure_corpus(corpus_b)
# Seed corpus B with a secret fact
seed_doc(corpus_b, "doc.md", "Corpus B secret fact.")

# Credential limited to corpus A only (read-only)
cred_a = issue_cred("probe-epsilon-a-ro", [corpus_a], "read_only")
client_a = MindPalace(api_key=cred_a, timeout=30)

# As corpus-A credential, call various methods on corpus B
section("E-1. history on corpus B as corpus-A credential")
sdk_call("history corpus B as A", lambda: client_a.memory.history(corpus=corpus_b, query=""))

section("E-2. as-of on corpus B as corpus-A credential")
sdk_call("as-of corpus B as A", lambda: client_a.memory.as_of(corpus=corpus_b, query="", as_of="2026-01-01T00:00:00Z"))

section("E-3. evidence on corpus B as corpus-A credential")
sdk_call("evidence corpus B as A", lambda: client_a.memory.evidence(corpus=corpus_b, query=""))

section("E-4. conflicts on corpus B as corpus-A credential")
sdk_call("conflicts corpus B as A", lambda: client_a.memory.conflicts(corpus=corpus_b, query=""))

section("E-5. changes on corpus B as corpus-A credential")
sdk_call("changes corpus B as A", lambda: client_a.memory.changes(corpus=corpus_b, query=""))

section("E-6. pack on corpus B as corpus-A credential")
sdk_call("pack corpus B as A", lambda: client_a.memory.pack(corpus=corpus_b, query=""))

section("E-7. query on corpus B as corpus-A credential")
sdk_call("query corpus B as A", lambda: client_a.memory.query(corpus=corpus_b, query=""))

section("E-8. snapshot on corpus B as corpus-A credential")
sdk_call("snapshot corpus B as A", lambda: client_a.memory.snapshot(corpus=corpus_b, query=""))

section("E-9. replay on corpus B as corpus-A credential")
sdk_call("replay corpus B as A", lambda: client_a.memory.replay(corpus=corpus_b, query=""))

section("E-10. feed on corpus B as corpus-A credential")
sdk_call("feed corpus B as A", lambda: client_a.memory.feed(corpus=corpus_b))

# ============================================================
# RETIRE ONE CLAIM WITHOUT A REPLACEMENT
# ============================================================
section("RETIRE. try each route that could retire a claim without replacement")
# Try 1: remember with superseded_claim_id but no statement (should fail validation)
section("RETIRE-1. remember with superseded_claim_id, no statement")
sdk_call("remember supersede no statement", lambda: client.memory.remember(corpus=corpus, key=None, supersedes_claim_id="nonexistent"))

# Try 2: revalidate endpoint
section("RETIRE-2. revalidate")
from api.models.memory import MemoryRequest
sdk_call("revalidate", lambda: client.memory.revalidate(corpus=corpus, query=""))

print("\n\nPROBE DE COMPLETE")
