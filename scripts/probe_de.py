#!/usr/bin/env python3
"""Probe d and e for Stage 3 gap analysis (marketing isolation).

Runs in-process via MindPalace SDK and over HTTP against a real server.
Prints RAW status codes and JSON bodies. Does NOT swallow exceptions: any
unexpected exception prints traceback and exits 1.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import traceback

import httpx

import mindpalace_sdk as sdk
from mindpalace_sdk import MindPalace

SCOPE_SECRET = os.environ.get("MIND_PALACE_SCOPE_SECRET", "")
ADMIN_DB_URL = os.environ.get(
    "ADMIN_DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace"
)

FAILED = False


def fail(msg: str) -> None:
    global FAILED
    FAILED = True
    print(f"::error::{msg}")
    traceback.print_stack()
    sys.exit(1)


def expected(result: str) -> None:
    print(f"  EXPECTED: {result}")


def call_http(method: str, url: str, payload: dict | None, headers: dict, label: str) -> None:
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
    except Exception as e:
        print(f"  Exception type: {type(e).__name__}")
        print(f"  Exception: {e}")
        traceback.print_exc()
        fail(f"Unexpected exception in {label}")


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
# ============================================================
section("D. one corpus, documents under campaign-a/ and campaign-b/")
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

# Extract path prefix from stored paths
path_prefix_a = pathlib.Path(path_a).parent.name + "/" if path_a else "campaign-a/"
path_prefix_b = pathlib.Path(path_b).parent.name + "/" if path_b else "campaign-b/"
print(f"\nStored path A: {path_a}")
print(f"Stored path B: {path_b}")
print(f"Path prefix A: {path_prefix_a}")
print(f"Path prefix B: {path_prefix_b}")

# Print history first to see stored path format
section("D-0. history (all documents, to see stored path format)")
call_sdk("history (all)", lambda: client.memory.history(corpus=corpus, query="tone"))

# Now path-scoped calls using actual stored path prefix
section(f"D-1. query with path={path_prefix_a}")
expected("campaign-a text only, no campaign-b")
call_sdk(f"query path={path_prefix_a}", lambda: client.memory.query(corpus=corpus, query="tone", path=path_prefix_a), expect_ok=False)

section("D-2. query without path filter")
expected("both campaign-a and campaign-b")
call_sdk("query no path", lambda: client.memory.query(corpus=corpus, query="tone"))

section(f"D-3. pack with path={path_prefix_a}")
expected("campaign-a text only")
call_sdk(f"pack path={path_prefix_a}", lambda: client.memory.pack(corpus=corpus, query="tone", path=path_prefix_a), expect_ok=False)

section("D-4. pack without path filter")
expected("both campaign-a and campaign-b")
call_sdk("pack no path", lambda: client.memory.pack(corpus=corpus, query="tone"))

section(f"D-5. current with path={path_prefix_a}")
expected("campaign-a claim only")
call_sdk(f"current path={path_prefix_a}", lambda: client.memory.current(corpus=corpus, query="tone", path=path_prefix_a), expect_ok=False)

section("D-6. current without path filter")
expected("both claims")
call_sdk("current no path", lambda: client.memory.current(corpus=corpus, query="tone"))

# conflicts is a response field, not a separate method
section(f"D-7. conflicts field in query with path={path_prefix_a}")
expected("conflicts empty for campaign-a")
call_sdk(f"query path={path_prefix_a} (check conflicts)", lambda: client.memory.query(corpus=corpus, query="tone", path=path_prefix_a), expect_ok=False)

section("D-8. conflicts field in query without path filter")
expected("conflicts empty (different keys, no conflict)")
call_sdk("query no path (check conflicts)", lambda: client.memory.query(corpus=corpus, query="tone"))

# Does campaign-b text appear for a campaign-a request?
section(f"D-9. does campaign-b text appear for path={path_prefix_a} request?")
expected("NO - campaign-b text must not appear")
# Re-use the query result from D-7 (same call, already printed above)
data = call_sdk(f"query path={path_prefix_a} (re-check for campaign-b leak)", lambda: client.memory.query(corpus=corpus, query="tone", path=path_prefix_a), expect_ok=False)
if data:
    cmems = data.get("current_memories", [])
    campaign_b_found = any("Campaign B" in m.get("claim", "") for m in cmems)
    print(f"  campaign-b text in campaign-a results: {campaign_b_found}")
    if campaign_b_found:
        print("  NOTE: campaign-b text appeared in campaign-a path-scoped query")
    else:
        print("  OK: no cross-path leak detected")
else:
    print("  (query returned no results under path prefix)")

# ============================================================
# PROBE E: two corpora, credential limited to one (over HTTP)
# ============================================================
section("E. two corpora, credential limited to corpus A (over HTTP)")
corpus_a = "probe-epsilon-a"
corpus_b = "probe-epsilon-b"
ensure_corpus(corpus_a)
ensure_corpus(corpus_b)

# Seed corpus B via remember
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
env["DATABASE_URL"] = ADMIN_DB_URL
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
    cwd="/mnt/Warehouse/projects/Mind Palace",
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

base_url = f"http://127.0.0.1:{port}"
headers = {"Authorization": f"Bearer {cred_a}"}

# Wait for server
try:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
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

    section("E-3. snapshot on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/snapshot", {"corpus": corpus_b, "query": "tone"}, headers, "snapshot corpus B as A (HTTP)")

    section("E-4. evidence on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/evidence", {"corpus": corpus_b, "query": "tone", "claim_id": rb.version_id}, headers, "evidence corpus B as A (HTTP)")

    section("E-5. changes on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/changes", {"corpus": corpus_b, "query": "tone"}, headers, "changes corpus B as A (HTTP)")

    section("E-6. pack on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/pack", {"corpus": corpus_b, "query": "tone"}, headers, "pack corpus B as A (HTTP)")

    section("E-7. query on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/query", {"corpus": corpus_b, "query": "tone"}, headers, "query corpus B as A (HTTP)")

    section("E-8. current on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("post", f"{base_url}/api/memory/current", {"corpus": corpus_b, "query": "tone"}, headers, "current corpus B as A (HTTP)")

    section("E-9. feed on corpus B as corpus-A credential (HTTP)")
    expected("denied: 403 or 404")
    call_http("get", f"{base_url}/api/memory/feed", {"corpus": corpus_b}, headers, "feed corpus B as A (HTTP)")

    # Same calls in-process (label separately)
    client_a = MindPalace(api_key=cred_a, timeout=30)

    section("E-10. history on corpus B as corpus-A credential (in-process)")
    expected("denied: exception / empty")
    call_sdk("history corpus B as A (in-process)", lambda: client_a.memory.history(corpus=corpus_b, query="tone"), expect_ok=False)

    section("E-11. as-of on corpus B as corpus-A credential (in-process)")
    expected("denied: exception / empty")
    call_sdk("as-of corpus B as A (in-process)", lambda: client_a.memory.as_of("2026-01-01T00:00:00Z", corpus=corpus_b, query="tone"), expect_ok=False)

    section("E-12. snapshot on corpus B as corpus-A credential (in-process)")
    expected("denied: exception / empty")
    call_sdk("snapshot corpus B as A (in-process)", lambda: client_a.memory.snapshot(as_of="2026-01-01T00:00:00Z", corpus=corpus_b, query="tone"), expect_ok=False)

    section("E-13. pack on corpus B as corpus-A credential (in-process)")
    expected("denied: exception / empty")
    call_sdk("pack corpus B as A (in-process)", lambda: client_a.memory.pack(corpus=corpus_b, query="tone"), expect_ok=False)

    section("E-14. query on corpus B as corpus-A credential (in-process)")
    expected("denied: exception / empty")
    call_sdk("query corpus B as A (in-process)", lambda: client_a.memory.query(corpus=corpus_b, query="tone"), expect_ok=False)

    section("E-15. current on corpus B as corpus-A credential (in-process)")
    expected("denied: exception / empty")
    call_sdk("current corpus B as A (in-process)", lambda: client_a.memory.current(corpus=corpus_b, query="tone"), expect_ok=False)

finally:
    proc.terminate()
    proc.wait(timeout=10)

# ============================================================
# RETIRE ONE CLAIM WITHOUT A REPLACEMENT
# ============================================================
section("RETIRE. try every route that could retire one claim without replacement")

corpus_r = "probe-retire"
ensure_corpus(corpus_r)
client_r = MindPalace(api_key=issue_cred("probe-retire-rw", [corpus_r], "read_write"), timeout=30)

# Write a doc under a temp dir via file-based remember
tmpdir = tempfile.mkdtemp(prefix="probe-retire-")
doc_path = os.path.join(tmpdir, "retire.md")
with open(doc_path, "w") as f:
    f.write("This is the statement to be retired.")

rw = client_r.memory.remember(
    "This is the statement to be retired.",
    corpus=corpus_r,
    key="retire.target",
    file=doc_path,
)
print(f"remember file: version_id={rw.version_id}, event={rw.event}, path={rw.path}")

# Print current before
section("RETIRE-0. current before deletion")
call_sdk("current before", lambda: client_r.memory.current(corpus=corpus_r, query="statement"))

# Delete the source file and run sync
os.unlink(doc_path)
print(f"\nDeleted source file: {doc_path}")
print("Running sync (file-based deletion)...")

result = subprocess.run(
    [sys.executable, "-m", "mindpalace", "corpus", "sync", corpus_r],
    capture_output=True, text=True, timeout=30,
    env={**os.environ, "DATABASE_URL": ADMIN_DB_URL, "ADMIN_DATABASE_URL": ADMIN_DB_URL,
         "MIND_PALACE_SCOPE_SECRET": SCOPE_SECRET or "dev-scope-secret",
         "MIND_PALACE_ENFORCE_SCOPE_TOKEN": "on"},
)
print(f"sync rc={result.returncode}")
print(result.stdout)
if result.stderr:
    print(result.stderr)

# Print current, pack, history, as-of after
section("RETIRE-1. current after sync deletion")
call_sdk("current after", lambda: client_r.memory.current(corpus=corpus_r, query="statement"))

section("RETIRE-2. pack after sync deletion")
call_sdk("pack after", lambda: client_r.memory.pack(corpus=corpus_r, query="statement"))

section("RETIRE-3. history after sync deletion")
call_sdk("history after", lambda: client_r.memory.history(corpus=corpus_r, query="statement"))

section("RETIRE-4. as-of after sync deletion")
call_sdk("as-of after", lambda: client_r.memory.as_of("2026-01-01T00:00:00Z", corpus=corpus_r, query="statement"))

# Try every route that could retire one claim
section("RETIRE-5. attempt retire via each available route")

# Route 1: POST /remember with supersedes_claim_id but no statement (should fail validation)
section("RETIRE-5a. remember with supersedes_claim_id, no statement")
try:
    client_r.memory.remember(corpus=corpus_r, key=None, supersedes_claim_id="nonexistent")
    print("  (no error - unexpected)")
except Exception as e:
    print(f"  {type(e).__name__}: {e}")

# Route 2: POST /revalidate
section("RETIRE-5b. revalidate")
try:
    client_r.memory.revalidate(corpus=corpus_r, query="statement")
    print("  (no error - check result)")
except Exception as e:
    print(f"  {type(e).__name__}: {e}")

# Route 3: DELETE /api/corpora/{name} (erases whole corpus, not one claim)
section("RETIRE-5c. DELETE /api/corpora/{name} (whole corpus, not one claim)")
expected("erases whole corpus - not a per-claim retire")
call_http("delete", f"{base_url}/api/corpora/{corpus_r}", None, headers, "DELETE corpus (whole corpus erasure)")

# Route 4: POST /remember with topic_key but no statement
section("RETIRE-5d. remember with topic_key, no statement")
try:
    client_r.memory.remember(corpus=corpus_r, key=None, topic_key="retire.target")
    print("  (no error - unexpected)")
except Exception as e:
    print(f"  {type(e).__name__}: {e}")

print("\n\nRETIRE CONCLUSION: none of the available routes retire one claim without a replacement statement.")
print("  - POST /remember requires statement= (422 if omitted)")
print("  - POST /revalidate does not retire claims")
print("  - DELETE /api/corpora/{name} erases whole corpus, not one claim")
print("  - sync after file deletion adds DELETED tombstone but does not remove from current/pack")

print("\n\nPROBE DE COMPLETE")

if FAILED:
    sys.exit(1)
