# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""MCP (Model Context Protocol) server for Mind Palace.

A thin adapter exposing the Mind Palace core over MCP so any MCP-compatible
AI client can use corpus memory as tools. Deliberately minimal:
context, search, sync, list_corpora.

Run with:
    python -m mcp_server

Configure in an MCP client (example):
    {
      "mcpServers": {
        "mind-palace": {
          "command": "python",
          "args": ["-m", "mcp_server"],
          "env": {"DATABASE_URL": "postgresql://..."}
        }
      }
    }
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from mindpalace_sdk import MindPalace

mcp = MCPServer("mind-palace")

# One client per corpus name, cached; MindPalace handles corpus creation.
_clients: dict[str, MindPalace] = {}


def _client(corpus: str) -> MindPalace:
    if corpus not in _clients:
        _clients[corpus] = MindPalace(corpus)
    return _clients[corpus]


@mcp.tool()
def context(query: str, corpus: str, budget_tokens: int = 4096) -> str:
    """Retrieve model-ready context for a query from a named corpus.

    Returns bounded text with source attribution. Prefer this over raw
    search when feeding an LLM.
    """
    pack = _client(corpus).context(query, budget_tokens=budget_tokens)
    return json.dumps(pack.to_dict(), indent=2)


@mcp.tool()
def search(query: str, corpus: str, k: int = 5) -> str:
    """Raw semantic search over a corpus. Returns ranked chunks with scores."""
    results = _client(corpus).search(query, k=k)
    payload = [
        {
            "title": r.source_title,
            "path": r.source_path,
            "heading_path": r.heading_path,
            "score": round(r.score, 4),
            "text": r.text[:500],
        }
        for r in results
    ]
    return json.dumps(payload, indent=2)


@mcp.tool()
def sync(corpus: str, path: str) -> str:
    """Synchronize a corpus with a directory of Markdown documents."""
    summary = _client(corpus).sync(path)
    return json.dumps(summary.__dict__, indent=2)


@mcp.tool()
def list_corpora() -> str:
    """List available corpora with document counts."""
    from api.services import corpora as corpora_svc
    from api.services.db import session_scope

    async def _list():
        async with session_scope() as db:
            return await corpora_svc.list_corpora(db)

    import asyncio

    items = asyncio.run(_list())
    return json.dumps(items, indent=2, default=str)


def main() -> None:
    mcp.run_stdio_async()


if __name__ == "__main__":
    main()
