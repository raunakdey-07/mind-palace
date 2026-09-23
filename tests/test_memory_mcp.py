"""Async MCP adapter contract, independent of the DB and synchronous SDK."""

import inspect
import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import mcp_server
from api.models.memory import MemoryRequest, MemoryResponse, Snapshot, State

OPERATIONS = ("current", "history", "changes", "evidence", "as-of", "snapshot", "replay", "pack")


@pytest.fixture
def service(monkeypatch):
    module = ModuleType("api.services.memory_public")

    class MemoryError(Exception):
        def __init__(self, code, message, status_code):
            super().__init__(message)
            self.code, self.message, self.status_code = code, message, status_code

    module.MemoryError = MemoryError
    module.execute = AsyncMock(
        return_value=MemoryResponse(
            query="雪",
            corpus="docs",
            state=State(as_of="2026-01-01T00:00:00Z"),
        )
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(mcp_server, "_client", Mock(side_effect=AssertionError("sync SDK used")))
    return module


def tool_name(operation):
    return "memory_" + operation.replace("-", "_")


async def test_tools_are_async_typed_and_preserve_legacy_tools():
    tools = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    assert {"context", "search", "sync", "list_corpora"} <= tools.keys()
    assert {name for name in tools if name.startswith("memory_")} == {
        tool_name(operation) for operation in (*OPERATIONS, "query")
    }
    for operation in (*OPERATIONS, "query"):
        tool = tools[tool_name(operation)]
        assert inspect.iscoroutinefunction(getattr(mcp_server, tool.name))
        schema = tool.input_schema
        request_schema = schema["$defs"]["MemoryRequest"]
        assert "corpus" in request_schema["required"]
        assert request_schema["additionalProperties"] is False
        assert request_schema["properties"]["budget"]["minimum"] == 512
        assert request_schema["properties"]["budget"]["maximum"] == 128000
        assert request_schema["properties"]["intent"]["default"] == "auto"
        assert tool.output_schema == MemoryResponse.model_json_schema()
        assert tool.annotations.read_only_hint == (operation != "snapshot")
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is False
    assert "immutable" in tools["memory_snapshot"].description


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_tool_dispatches_typed_request_and_returns_json(service, operation):
    body = {
        "corpus": "docs",
        "query": "雪",
        "budget": 9000,
        "as_of": "2026-01-01T00:00:00Z",
        "valid_at": "2025-12-01T00:00:00Z",
        "path": "notes.md",
        "claim_id": "a" * 64,
        "snapshot_id": "b" * 64,
    }
    result = await mcp_server.mcp.call_tool(tool_name(operation), {"request": body})
    assert not result.is_error
    assert result.structured_content == service.execute.return_value.model_dump(mode="json")
    assert json.loads(result.content[0].text) == result.structured_content
    assert isinstance(result.structured_content["state"]["as_of"], str)
    service.execute.assert_awaited_once_with(operation, MemoryRequest(**body))


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"corpus": "bad corpus"},
        {"corpus": "docs", "budget": True},
        {"corpus": "docs", "budget": "8000"},
        {"corpus": "docs", "budget": 511},
        {"corpus": "docs", "budget": 128001},
        {"corpus": "docs", "as_of": "2026-01-01"},
        {"corpus": "docs", "snapshot_id": "bad"},
        {"corpus": "docs", "write": "claim"},
    ],
)
async def test_invalid_tool_input_never_reaches_service(service, operation, body):
    with pytest.raises(ToolError):
        await mcp_server.mcp.call_tool(tool_name(operation), {"request": body})
    service.execute.assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "code,status",
    [
        ("invalid_request", 422),
        ("corpus_not_found", 404),
        ("snapshot_not_found", 404),
        ("backend_unavailable", 503),
    ],
)
async def test_service_errors_are_structured_mcp_errors(service, operation, code, status):
    service.execute.side_effect = service.MemoryError(code, "service message", status)
    result = await mcp_server.mcp.call_tool(tool_name(operation), {"request": {"corpus": "docs"}})
    assert result.is_error
    assert result.structured_content == {"detail": {"code": code, "message": "service message"}}
    assert json.loads(result.content[0].text) == result.structured_content


async def test_snapshot_and_replay_are_distinct_service_operations(service):
    snapshot = Snapshot(
        id="a" * 64,
        as_of="2026-01-01T00:00:00Z",
        observed_at="2026-01-02T00:00:00Z",
        version_ids=["v1"],
    )
    service.execute.return_value = MemoryResponse(query="", corpus="docs", snapshot=snapshot)
    created = await mcp_server.mcp.call_tool("memory_snapshot", {"request": {"corpus": "docs"}})
    service.execute.assert_awaited_once_with("snapshot", MemoryRequest(corpus="docs"))
    assert created.structured_content["snapshot"] == snapshot.model_dump(mode="json")
    service.execute.reset_mock()
    replayed = await mcp_server.mcp.call_tool(
        "memory_replay", {"request": {"corpus": "docs", "snapshot_id": snapshot.id}}
    )
    service.execute.assert_awaited_once_with(
        "replay", MemoryRequest(corpus="docs", snapshot_id=snapshot.id)
    )
    assert replayed.structured_content == created.structured_content
