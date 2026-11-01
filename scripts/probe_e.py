#!/usr/bin/env python3
"""Probe e for Stage 3 gap analysis (cross-corpus isolation over HTTP).

Runs over HTTP against a real uvicorn server. Creates two corpora and a
credential limited to corpus A. Calls history, as_of, evidence, snapshot,
replay_snapshot, changes, feed, pack, query on corpus B as the corpus-A
credential. Prints real HTTP status + body per call.

For conflicts: conflicts is a field on MemoryResponse (exposed via query/pack/
current), not a separate route. We report it from query responses.

Does NOT swallow exceptions. Prints traceback and exits 1 on unexpected failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import traceback

import httpx

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


def call_http(method: str, url: str, payload: dict | None, headers: dict, label: str) -> dict | None:
    """Make an HTTP call and print status + body. Fail on unexpected exceptions."""
    global FAILED
    print(f"--- {label} ---")
    print(f"  {method.upper()} {url}")
    try:
        if method == "get":
            resp = httpx.get(url, params=payload, headers=headers, timeout=30)
        else:
            resp = httpx.post(url, json=payload, headers=headers, timeout=30)
        print(f"  HTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2, default=str))
        except Exception:
            print(resp.text)
        return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
    except Exception as e:
        print(f"  Exception type: {type(e).__name__}")
        print(f"  Exception: {e}")
        traceback.print_exc()
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
# PROBE E: two corpora, credential limited to corpus A (over HTTP)
# ============================================================
section("E. two corpora, credential limited to corpus A (over HTTP)")
corpus_a = "probe-epsilon-a"
corpus_b = "probe-epsilon-b"
ensure_corpus(corpus_a)
ensure_corpus(corpus_b)

# Seed corpus B via remember (in-process SDK)
client_b_write = MindPalace(api_key=issue_cred("probe-epsilon-b-rw", [corpus_b], "read_write"), timeout=30)
rb = client_b_write.memory.remember(
    "Corpus B secret fact.",
    corpus=corpus_b,
    key="secret.fact",
)
print(f"seed corpus B: version_id={rb.version_id}, path={rb.path}")

# Credential limited to corpus A only (read-only)
cred_a = issue_cred("probe-epsilon-a-ro", [corpus_a], "read_only")

# Start real HTTP server
port = 19876
env = dict(os.environ)
env["DATABASE_URL"] = os.environ.get("DATABASE_URL", "postgresql://mindpalace_runtime:ci-runtime-role-password@localhost:5432/mindpalace")
env["ADMIN_DATABASE_URL"] = ADMIN_DB_URL
env["MIND_PALACE_SCOPE_SECRET"] = SCOPE_SECRET or "dev-scope-secret"
env["MIND_PALACE_ENFORCE_SCOPE_TOKEN"] = "on"
env["MIND_PALACE_AUTH_MODE"] = "required"
env["MIND_PALACE_DEPLOYMENT"] = "server"
env["MIND_PALACE_RUNTIME"] = "0"
env["MIND_PALACE_INGEST_ROOTS"] = "/tmp/ingest"

proc = subprocess.Popen(
    [
        sys.executable, "-m", "uvicorn", "api.main:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--log-level", "warning",
    ],
    cwd=os.getcwd(),
    env=env,
    stdout=open("/tmp/server_stdout.txt", "w"),
    stderr=open("/tmp/server_stderr.txt", "w"),
)

base_url = f"http://127.0.0.1:{port}"
headers = {"Authorization": f"Bearer {cred_a}"}

# Wait for server
try:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            try:
                with open("/tmp/server_stderr.txt") as f:
                    err_text = f.read()
                print(f"  server stderr (full):\n{err_text}")
            except Exception:
                pass
            fail(f"test server exited with {proc.returncode}")
        try:
            if httpx.get(f"{base_url}/health/live", timeout=2).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        fail("server did not become healthy")
except Exception:
    proc.terminate()
    raise

try:
    # Over HTTP calls on corpus B as corpus-A credential (expect denial)
    section("E-1. history on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/history", {"corpus": corpus_b, "query": "tone"}, headers, "history corpus B as A (HTTP)")

    section("E-2. as-of on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/as-of", {"corpus": corpus_b, "query": "tone", "as_of": "2026-01-01T00:00:00Z"}, headers, "as-of corpus B as A (HTTP)")

    section("E-3. evidence on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/evidence", {"corpus": corpus_b, "query": "tone", "claim_id": rb.version_id}, headers, "evidence corpus B as A (HTTP)")

    section("E-4. snapshot on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/snapshot", {"corpus": corpus_b, "query": "tone"}, headers, "snapshot corpus B as A (HTTP)")

    section("E-5. replay_snapshot on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/replay", {"corpus": corpus_b, "query": "tone", "snapshot_id": rb.version_id}, headers, "replay_snapshot corpus B as A (HTTP)")

    section("E-6. changes on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/changes", {"corpus": corpus_b, "query": "tone"}, headers, "changes corpus B as A (HTTP)")

    section("E-7. pack on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/pack", {"corpus": corpus_b, "query": "tone"}, headers, "pack corpus B as A (HTTP)")

    section("E-8. query on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/query", {"corpus": corpus_b, "query": "tone"}, headers, "query corpus B as A (HTTP)")

    section("E-9. current on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/current", {"corpus": corpus_b, "query": "tone"}, headers, "current corpus B as A (HTTP)")

    section("E-10. feed on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("get", f"{base_url}/api/memory/feed", {"corpus": corpus_b}, headers, "feed corpus B as A (HTTP)")

    # Conflicts: exposed as a field in query/pack responses, not a separate route
    section("E-11. conflicts field in query response (not a separate route)")
    expected("conflicts field exists in query response schema; check content")
    data = call_http("post", f"{base_url}/api/memory/query", {"corpus": corpus_a, "query": "tone"}, headers, "query corpus A as A (HTTP, to inspect conflicts field)")
    if data:
        conflicts = data.get("conflicts", "ABSENT")
        print(f"  conflicts field present: {conflicts is not 'ABSENT'}")
        print(f"  conflicts value: {conflicts}")

finally:
    proc.terminate()
    proc.wait(timeout=10)

print("\n\nPROBE E COMPLETE")

if FAILED:
    sys.exit(1)
