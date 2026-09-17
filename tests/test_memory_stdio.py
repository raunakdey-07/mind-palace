"""Real MCP stdio protocol smoke test; read-only against a migrated database."""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from api.models.memory import MemoryResponse


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="requires migrated DATABASE_URL")
async def test_memory_tools_over_stdio():
    corpus = os.getenv("MCP_SMOKE_CORPUS", "default")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "HF_HUB_OFFLINE": "1"},
    )
    async with asyncio.timeout(30):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {"context", "search", "sync", "list_corpora", "memory_pack"} <= {
                    t.name for t in tools.tools
                }
                result = await session.call_tool("memory_current", {"request": {"corpus": corpus}})
                assert not result.is_error
                current = MemoryResponse.model_validate(result.structured_content)
                assert current.corpus == corpus
                assert all(c.status == "CURRENT" for c in current.current_memories)
                packed = await session.call_tool(
                    "memory_pack", {"request": {"corpus": corpus, "budget": 8000}}
                )
                assert not packed.is_error
                pack = MemoryResponse.model_validate(packed.structured_content)
                assert len(pack.canonical_json()) <= 8000
                assert packed.content[0].text == pack.canonical_json()
                bad = await session.call_tool(
                    "memory_evidence", {"request": {"corpus": corpus, "claim_id": "0" * 64}}
                )
                assert bad.is_error
                assert bad.structured_content["detail"]["code"] == "claim_not_found"
                invalid = await session.call_tool(
                    "memory_pack", {"request": {"corpus": corpus, "budget": -1}}
                )
                assert invalid.is_error
