#!/usr/bin/env python3
"""Probe script for Stage 3 gap analysis (Team 052).

Runs against a live Mind Palace server at MIND_PALACE_URL (default
http://127.0.0.1:8000). Prints raw JSON for each experiment. Asserts nothing.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.environ.get("MIND_PALACE_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("MIND_PALACE_TOKEN", "")

def H():
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

def post(path, body=None, **kw):
    r = httpx.post(f"{BASE}{path}", json=body, headers=H(), **kw)
    print(f"--- {path} ---")
    print(f"status: {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2, default=str))
    except Exception:
        print(r.text)
    return r

def get(path, **kw):
    r = httpx.get(f"{BASE}{path}", headers=H(), **kw)
    print(f"--- {path} ---")
    print(f"status: {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2, default=str))
    except Exception:
        print(r.text)
    return r

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

# ── a. Stable source identity and update semantics ──────────────────
section("a. remember same source twice (same content, then changed)")

post("/api/memory/remember", {
    "statement": "Brand voice is professional and concise.",
    "corpus": "probe-corpus",
    "key": "brand.voice",
})

post("/api/memory/remember", {
    "statement": "Brand voice is professional and concise.",
    "corpus": "probe-corpus",
    "key": "brand.voice",
})

post("/api/memory/remember", {
    "statement": "Brand voice is warm, dry, and confident.",
    "corpus": "probe-corpus",
    "key": "brand.voice",
})

section("a. current after update")
post("/api/memory/current", {"corpus": "probe-corpus", "query": ""})

section("a. history after update")
post("/api/memory/history", {"corpus": "probe-corpus", "query": "brand voice"})

# ── b. Withdrawal ───────────────────────────────────────────────────
section("b. remember with changed statement (supersession)")

post("/api/memory/remember", {
    "statement": "Use the phrase 'Explore our services' as CTA.",
    "corpus": "probe-corpus",
    "key": "brand.cta",
})

section("b. current")
post("/api/memory/current", {"corpus": "probe-corpus", "query": ""})

section("b. pack (budget 1200)")
post("/api/memory/pack", {"corpus": "probe-corpus", "query": "brand", "budget": 1200})

section("b. history")
post("/api/memory/history", {"corpus": "probe-corpus", "query": "cta"})

# ── c. State handling ───────────────────────────────────────────────
section("c. write approved claim")
post("/api/memory/remember", {
    "statement": "Approved: use professional tone.",
    "corpus": "probe-corpus",
    "key": "state.approved",
})

section("c. current (default)")
post("/api/memory/current", {"corpus": "probe-corpus", "query": ""})

section("c. pack (default)")
post("/api/memory/pack", {"corpus": "probe-corpus", "query": "tone", "budget": 1200})

# ── d. Campaign scope ──────────────────────────────────────────────
section("d. campaign-a path")
post("/api/memory/remember", {
    "statement": "Campaign A uses playful tone.",
    "corpus": "probe-corpus",
    "key": "campaign-a.tone",
    "path": "campaign-a/brief.md",
})

section("d. campaign-b path")
post("/api/memory/remember", {
    "statement": "Campaign B uses formal tone.",
    "corpus": "probe-corpus",
    "key": "campaign-b.tone",
    "path": "campaign-b/brief.md",
})

section("d. query with path filter campaign-a/")
post("/api/memory/query", {"corpus": "probe-corpus", "query": "tone", "path": "campaign-a/"})

section("d. query without path filter")
post("/api/memory/query", {"corpus": "probe-corpus", "query": "tone"})

# ── e. Isolation (two corpora) ─────────────────────────────────────
section("e. create second corpus")
post("/api/corpora", {"name": "probe-corpus-2", "description": "second corpus"})

section("e. remember in corpus 2")
post("/api/memory/remember", {
    "statement": "Corpus 2 secret fact.",
    "corpus": "probe-corpus-2",
    "key": "secret",
})

section("e. history on corpus 2")
post("/api/memory/history", {"corpus": "probe-corpus-2", "query": ""})

section("e. as-of on corpus 2")
post("/api/memory/as-of", {"corpus": "probe-corpus-2", "query": "", "as_of": "2026-01-01T00:00:00Z"})

section("e. evidence on corpus 2")
post("/api/memory/evidence", {"corpus": "probe-corpus-2", "query": ""})

section("e. changes on corpus 2")
post("/api/memory/changes", {"corpus": "probe-corpus-2", "query": ""})

section("e. pack on corpus 2")
post("/api/memory/pack", {"corpus": "probe-corpus-2", "query": "", "budget": 1200})

# ── f. Budget ───────────────────────────────────────────────────────
section("f. pack with budget 1200")
r = post("/api/memory/pack", {"corpus": "probe-corpus", "query": "brand", "budget": 1200})
try:
    print(f"\nBUDGET FIELD: {r.json().get('budget')}")
    print(f"TRUNCATED: {r.json().get('truncated')}")
except Exception:
    pass

# ── g. SDK failure ──────────────────────────────────────────────────
section("g. SDK failure (wrong port)")
import mindpalace_sdk as sdk
client = sdk.MindPalace(base_url="http://127.0.0.1:9999")
try:
    client.current("probe-corpus", "")
except Exception as e:
    print(f"Exception type: {type(e).__name__}")
    print(f"Exception: {e}")

print("\n\nPROBE COMPLETE")
