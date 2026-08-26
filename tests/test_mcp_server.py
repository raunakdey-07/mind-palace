"""MCP server smoke tests: tool registration and tool-call behavior."""

from __future__ import annotations

import json

import pytest


def test_mcp_server_registers_four_tools():
    from mcp_server import mcp

    tools = __import__("asyncio").run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"context", "search", "sync", "list_corpora"}


def test_mcp_context_tool_returns_attributed_pack(tmp_path, monkeypatch):
    """The MCP context tool returns the same contract as the SDK."""
    if not __import__("os").getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")

    import asyncio

    import sqlalchemy as sa

    url = (
        __import__("os")
        .environ["DATABASE_URL"]
        .replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )

    def purge():
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DELETE FROM ingestion_manifest WHERE corpus_id IN "
                        "(SELECT id FROM corpora WHERE name = 'mcp-test-corpus')"
                    )
                )
                conn.execute(
                    sa.text(
                        "DELETE FROM chunks WHERE doc_id IN "
                        "(SELECT id FROM documents WHERE corpus_id IN "
                        "(SELECT id FROM corpora WHERE name = 'mcp-test-corpus'))"
                    )
                )
                conn.execute(
                    sa.text(
                        "DELETE FROM documents WHERE corpus_id IN "
                        "(SELECT id FROM corpora WHERE name = 'mcp-test-corpus')"
                    )
                )
                conn.execute(sa.text("DELETE FROM corpora WHERE name = 'mcp-test-corpus'"))
        finally:
            engine.dispose()

    purge()
    try:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("""---
title: "MCP Guide"
date: 2024-07-01
document_type: "note"
---

# Guide

Zephyr quantum calibration requires hourly wobble checks.
""")

        from mindpalace_sdk import MindPalace

        mp = MindPalace("mcp-test-corpus")
        mp.sync(str(docs))

        from mcp_server import mcp

        raw = asyncio.run(
            mcp.call_tool(
                "context",
                {"query": "zephyr quantum calibration", "corpus": "mcp-test-corpus"},
            )
        )
        # MCP 2.x returns (content, structured) or content list depending on version
        if hasattr(raw, "content"):
            block = raw.content[0]
        elif isinstance(raw, tuple):
            block = raw[0][0]
        else:
            block = raw[0]
        data = json.loads(block.text)

        assert data["token_estimate"] > 0
        assert "zephyr" in data["context"].lower() or "wobble" in data["context"].lower()
        assert any(s["title"] == "MCP Guide" for s in data["sources"])
    finally:
        purge()


def test_mcp_search_tool_shape(tmp_path):
    if not __import__("os").getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")

    import asyncio

    from mcp_server import mcp, _clients

    _clients.clear()  # drop cached clients holding stale corpus state

    raw = asyncio.run(
        mcp.call_tool("search", {"query": "anything", "corpus": "mcp-test-corpus", "k": 3})
    )
    if hasattr(raw, "content"):
        block = raw.content[0]
    elif isinstance(raw, tuple):
        block = raw[0][0]
    else:
        block = raw[0]
    data = json.loads(block.text)
    assert isinstance(data, list)
