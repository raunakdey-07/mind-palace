# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""MCP (Model Context Protocol) server for Mind Palace.

A thin adapter exposing the Mind Palace core over MCP so any MCP-compatible
AI client can use corpus memory as tools. Deliberately minimal:
context, search, sync, list_corpora, and the public memory operations.
The memory tools do not expose arbitrary writes; snapshot creates immutable state.
Authentication is not currently provided.

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
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from api.models.memory import MemoryRequest, MemoryResponse
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


# Advertise the central response schema while allowing MCP-native error results.
MemoryToolResult = Annotated[CallToolResult, MemoryResponse]
_READ_MEMORY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


async def _execute_memory(operation: str, request: MemoryRequest) -> CallToolResult:
    from api.services.memory_public import MemoryError, execute

    try:
        response = await execute(operation, request)
    except MemoryError as exc:
        detail = {"code": exc.code, "message": exc.message}
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=json.dumps({"detail": detail}))],
            structuredContent={"detail": detail},
        )
    payload = response.model_dump(mode="json")
    return CallToolResult(
        content=[TextContent(type="text", text=response.canonical_json())],
        structuredContent=payload,
    )


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_current(request: MemoryRequest) -> MemoryToolResult:
    """Read current memories from an explicitly named corpus."""
    return await _execute_memory("current", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_history(request: MemoryRequest) -> MemoryToolResult:
    """Read historical memories, preserving status and evidence attribution."""
    return await _execute_memory("history", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_changes(request: MemoryRequest) -> MemoryToolResult:
    """Read memory changes and their provenance."""
    return await _execute_memory("changes", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_evidence(request: MemoryRequest) -> MemoryToolResult:
    """Read source evidence for memories in a corpus."""
    return await _execute_memory("evidence", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_as_of(request: MemoryRequest) -> MemoryToolResult:
    """Read memory as of an observation time, not present-day state."""
    return await _execute_memory("as-of", request)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        openWorldHint=False,
    ),
    structured_output=True,
)
async def memory_snapshot(request: MemoryRequest) -> MemoryToolResult:
    """Create an immutable snapshot; cannot overwrite claims or arbitrary memory."""
    return await _execute_memory("snapshot", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_replay(request: MemoryRequest) -> MemoryToolResult:
    """Replay an immutable snapshot within its corpus."""
    return await _execute_memory("replay", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_pack(request: MemoryRequest) -> MemoryToolResult:
    """Pack attributed memory evidence; budget is Unicode characters, not tokens."""
    return await _execute_memory("pack", request)


def main() -> None:
    import asyncio

    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
