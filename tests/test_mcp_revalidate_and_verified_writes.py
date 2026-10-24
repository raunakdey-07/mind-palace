"""MCP revalidation and quote-verified writes (Issue #41)."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

CORPUS = "mcp-reval-test-corpus"


def _skip_without_db() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")


@pytest.fixture(scope="module")
def loop():
    created = asyncio.new_event_loop()
    yield created
    created.close()


def _call(loop, **kwargs):
    import mcp_server

    return loop.run_until_complete(mcp_server.mcp.call_tool("memory_revalidate", kwargs))


def _payload(result):
    return json.loads(result.content[0].text)


def _skip_if_no_db():
    _skip_without_db()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_memory_revalidate_is_registered():
    import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert "memory_revalidate" in names


# ---------------------------------------------------------------------------
# Quote verification (MCP)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(root))
    return root


def test_quote_exact_accepted(tmp_root, loop):
    _skip_without_db()
    doc = tmp_root / "note.md"
    doc.write_text("The production datastore is PostgreSQL.\n")
    result = loop.run_until_complete(
        __import__("mcp_server").mcp.call_tool(
            "memory_remember",
            {
                "statement": "The production datastore is PostgreSQL.",
                "corpus": CORPUS,
                "file": str(doc),
            },
        )
    )
    assert not result.is_error, json.loads(result.content[0].text)


def test_quote_mutation_rejected(tmp_root, loop):
    _skip_without_db()
    doc = tmp_root / "note.md"
    doc.write_text("The production datastore is PostgreSQL.\n")
    result = loop.run_until_complete(
        __import__("mcp_server").mcp.call_tool(
            "memory_remember",
            {
                "statement": "The production datastore is Postgres.",
                "corpus": CORPUS,
                "file": str(doc),
            },
        )
    )
    assert result.is_error


def test_unresolved_supersedes_rejected(loop):
    _skip_without_db()
    result = loop.run_until_complete(
        __import__("mcp_server").mcp.call_tool(
            "memory_remember",
            {
                "statement": "Production uses CockroachDB.",
                "corpus": CORPUS,
                "supersedes_claim_id": "deadbeef1234",
            },
        )
    )
    assert result.is_error


# ---------------------------------------------------------------------------
# SDK revalidation parity
# ---------------------------------------------------------------------------


def test_sdk_revalidate_current_then_stale():
    _skip_without_db()
    os.environ.setdefault("DATABASE_URL", os.environ["DATABASE_URL"])
    from mindpalace_sdk import MindPalace

    corpus = "sdk-reval-corpus"
    mp = MindPalace(corpus)
    mp.remember("Production uses PostgreSQL.", key="datastore")
    r = mp.explain("What datastore does production use?")
    receipt = r.receipt
    cid = r.current_memories[0].id
    out = mp.revalidate(receipt=receipt)
    assert out.get("verdict") == "CURRENT" or out.get("status") == "CURRENT"
    mp.remember("Production uses CockroachDB.", supersedes_claim_id=cid[:12])
    out2 = mp.revalidate(receipt=receipt)
    assert out2.get("verdict") == "STALE" or out2.get("status") == "STALE"
    claims = out2.get("claims") or out2.get("results") or []
    assert any((c.get("status") == "superseded") for c in claims)
    drift = out2.get("drift") or {}
    counts = drift.get("counts") or {}
    assert counts.get("superseded", 0) >= 1
