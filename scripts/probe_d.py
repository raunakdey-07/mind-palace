#!/usr/bin/env python3
"""Probe d for Stage 3 gap analysis (marketing isolation, path scoping).

path is an EXACT document-path match (api/services/memory.py:1250 `d.path = :path`;
api/services/memory_public.py:445 raises document_not_found unless a version
path equals request.path). It is NOT a prefix/directory match.

Runs in-process via MindPalace SDK. Prints RAW status codes and JSON bodies.
Does NOT swallow exceptions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import sys
import traceback

import mindpalace_sdk as sdk
from mindpalace_sdk import MindPalace

ADMIN_DB_URL = os.environ.get(
    "ADMIN_DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace"
)
SCOPE_SECRET = os.environ.get("MIND_PALACE_SCOPE_SECRET", "")

FAILED = False


def fail(msg: str) -> None:
    global FAILED
    FAILED = True
    print(f"::error::{msg}")
    traceback.print_stack()
    sys.exit(1)


def expected(result: str) -> None:
    print(f"  EXPECTED: {result}")


def call_sdk(label: str, fn, expect_ok: bool = True) -> dict | None:
    """Make an in-process SDK call and print result. Return parsed dict or None."""
    global FAILED
    print(f"--- {label} (in-process) ---")
    try:
        result = fn()
        if hasattr(result, "model_dump"):
            data = result.model_dump(mode="json")
            print(f"  status: 200 (SDK in-process)")
            print(json.dumps(data, indent=2, default=str))
            return data
        else:
            print(f"  status: 200 (SDK in-process)")
            print(str(result))
            return None
    except Exception as e:
        print(f"  Exception type: {type(e).__name__}")
        print(f"  Exception: {e}")
        if hasattr(e, "status_code"):
            print(f"  status_code: {e.status_code}")
        traceback.print_exc()
        if expect_ok:
            fail(f"Unexpected exception in {label}")
        return None


def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def corpus_id_for(name: str) -> str:
    return hashlib.sha256(f"corpus:{name}".encode()).hexdigest()


def ensure_corpus(corpus_name: str) -> str:
    cid = corpus_id_for(corpus_name)
    async def _ensure():
        from sqlalchemy import text
        from api.services.db import admin_session_scope, principal_scope
        from api.services.authz import ADMIN_PRINCIPAL
        async with admin_session_scope(url=ADMIN_DB_URL) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                row = await db.execute(text("SELECT id FROM corpora WHERE id = :c"), {"c": cid})
                if row.scalar() is None:
                    await db.execute(
                        text(f"INSERT INTO corpora (id, name) VALUES ('{cid}', '{corpus_name}') ON CONFLICT (id) DO NOTHING")
                    )
                    await db.commit()
        return cid
    return asyncio.run(_ensure())


def issue_cred(label: str, corpora: list[str], role: str = "read_write") -> str:
    async def _issue():
        from api.services import authz
        from api.services.db import admin_session_scope, principal_scope
        from api.services.authz import ADMIN_PRINCIPAL
        async with admin_session_scope(url=ADMIN_DB_URL) as db:
            with principal_scope(ADMIN_PRINCIPAL):
                cred = await authz.issue_credential(db, label, list(corpora), role)
                return cred["secret"]
    return asyncio.run(_issue())


# ============================================================
# PROBE D: one corpus, campaign-a/ and campaign-b/ paths
# Seed through product write path (remember with file=)
# path is EXACT match, not prefix
# ============================================================
section("D. one corpus, documents under campaign-a/ and campaign-b/ (exact path match)")
corpus = "probe-delta"
ensure_corpus(corpus)

# Create ingest directories and write files
ingest_root = "/tmp/ingest"
pathlib.Path(f"{ingest_root}/probe-delta/campaign-a").mkdir(parents=True, exist_ok=True)
pathlib.Path(f"{ingest_root}/probe-delta/campaign-b").mkdir(parents=True, exist_ok=True)
pathlib.Path(f"{ingest_root}/probe-delta/campaign-a/brief.md").write_text("Campaign A uses playful tone.")
pathlib.Path(f"{ingest_root}/probe-delta/campaign-b/brief.md").write_text("Campaign B uses formal tone.")

client = MindPalace(api_key=issue_cred("probe-delta-rw", [corpus], "read_write"), timeout=30)

# Seed via remember with file=
r1 = client.memory.remember(
    "Campaign A uses playful tone.",
    corpus=corpus,
    key="campaign-a.tone",
    file=f"{ingest_root}/probe-delta/campaign-a/brief.md",
)
print(f"remember campaign-a: version_id={r1.version_id}, event={r1.event}, path={r1.path}")
path_a = r1.path

r2 = client.memory.remember(
    "Campaign B uses formal tone.",
    corpus=corpus,
    key="campaign-b.tone",
    file=f"{ingest_root}/probe-delta/campaign-b/brief.md",
)
print(f"remember campaign-b: version_id={r2.version_id}, event={r2.event}, path={r2.path}")
path_b = r2.path

print(f"\nStored path A: {path_a}")
print(f"Stored path B: {path_b}")

# Print history first to see stored path format
section("D-0. history (all documents, to see stored path format)")
call_sdk("history (all)", lambda: client.memory.history(corpus=corpus, query="tone"))

# Path-scoped calls using EXACT stored paths
section(f"D-1. query with EXACT path={path_a}")
expected("campaign-a text only")
call_sdk(f"query path={path_a}", lambda: client.memory.query(corpus=corpus, query="tone", path=path_a))

section("D-2. query without path filter")
expected("both campaign-a and campaign-b")
call_sdk("query no path", lambda: client.memory.query(corpus=corpus, query="tone"))

section(f"D-3. pack with EXACT path={path_a}")
expected("campaign-a text only")
call_sdk(f"pack path={path_a}", lambda: client.memory.pack(corpus=corpus, query="tone", path=path_a))

section("D-4. pack without path filter")
expected("both campaign-a and campaign-b")
call_sdk("pack no path", lambda: client.memory.pack(corpus=corpus, query="tone"))

section(f"D-5. current with EXACT path={path_a}")
expected("campaign-a claim only")
call_sdk(f"current path={path_a}", lambda: client.memory.current(corpus=corpus, query="tone", path=path_a))

section("D-6. current without path filter")
expected("both claims")
call_sdk("current no path", lambda: client.memory.current(corpus=corpus, query="tone"))

# Directory prefix (NOT exact path) - should return document_not_found
section(f"D-7. query with directory prefix 'campaign-a/' (NOT exact path)")
expected("404 document_not_found - path is exact match, not prefix")
call_sdk(f"query path=campaign-a/ (prefix)", lambda: client.memory.query(corpus=corpus, query="tone", path="campaign-a/"), expect_ok=False)

section(f"D-8. pack with directory prefix 'campaign-a/' (NOT exact path)")
expected("404 document_not_found - path is exact match, not prefix")
call_sdk(f"pack path=campaign-a/ (prefix)", lambda: client.memory.pack(corpus=corpus, query="tone", path="campaign-a/"), expect_ok=False)

# Does campaign-b text appear for a campaign-a exact-path request?
section(f"D-9. does campaign-b text appear for exact path={path_a} request?")
expected("NO - campaign-b text must not appear with exact path scoping")
data_a = call_sdk(f"query path={path_a} (re-check for campaign-b leak)", lambda: client.memory.query(corpus=corpus, query="tone", path=path_a), expect_ok=False)
if data_a:
    cmems = data_a.get("current_memories", [])
    campaign_b_found = any("Campaign B" in m.get("claim", "") for m in cmems)
    print(f"  campaign-b text in campaign-a exact-path results: {campaign_b_found}")
    if campaign_b_found:
        print("  NOTE: campaign-b text appeared in campaign-a exact-path query")
    else:
        print("  OK: no cross-path leak detected")
else:
    print("  (query returned no results under exact path)")

# Check conflicts field in query without path filter
section("D-10. conflicts field in query without path filter")
expected("conflicts empty (different keys, no conflict)")
call_sdk("query no path (check conflicts)", lambda: client.memory.query(corpus=corpus, query="tone"))

print("\n\nPROBE D COMPLETE")

if FAILED:
    sys.exit(1)
