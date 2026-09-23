"""Additive query adapters; execute is mocked, with no database or semantic model."""

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from typer.testing import CliRunner

import mcp_server
from api.main import app
from api.models.memory import Evidence, MemoryRequest, MemoryResponse
from api.routers import query as ask_router
from cli.memory import app as memory_app
from mindpalace_sdk import MemoryClientError, MindPalace

QUESTION = "What changed about the deployment?"
SELECTORS = {
    "budget": 4096,
    "as_of": "2026-09-01T00:00:00Z",
    "valid_at": "2026-08-01T00:00:00Z",
    "path": "deployment.md",
    "claim_id": "a" * 64,
}
INTENTS = ("auto", "current", "historical", "temporal", "change", "conflict", "provenance")


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
            query=QUESTION, corpus="docs", truncated=True, constraints=["Evidence omitted."]
        )
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def intent_contract():
    if "intent" not in MemoryRequest.model_fields:
        pytest.skip("Awaiting core-owned MemoryRequest.intent field")


@pytest.fixture
def remote(monkeypatch):
    # Exercise SDK/CLI -> real HTTP router -> mocked execute, without a server.
    from fastapi.testclient import TestClient

    client = TestClient(app)
    post = Mock(side_effect=client.post)
    monkeypatch.setattr(httpx, "post", post)
    yield post
    client.close()


@pytest.mark.parametrize("selectors", [SELECTORS, {"snapshot_id": "b" * 64, "budget": 2048}])
async def test_rest_query_dispatch(client, service, selectors):
    body = {"query": QUESTION, "corpus": "docs", **selectors}
    response = await client.post("/api/memory/query", json=body)
    assert response.status_code == 200
    assert response.json() == service.execute.return_value.model_dump(mode="json")
    service.execute.assert_awaited_once_with("query", MemoryRequest(**body))


@pytest.mark.parametrize("intent", INTENTS)
async def test_rest_and_mcp_forward_intent(client, service, intent_contract, intent):
    body = {"query": QUESTION, "corpus": "docs", "intent": intent, **SELECTORS}
    response = await client.post("/api/memory/query", json=body)
    assert response.status_code == 200
    service.execute.assert_awaited_once_with("query", MemoryRequest(**body))
    service.execute.reset_mock()
    result = await mcp_server.mcp.call_tool("memory_query", {"request": body})
    assert not result.is_error
    service.execute.assert_awaited_once_with("query", MemoryRequest(**body))


async def test_query_schemas_are_shared():
    route = app.openapi()["paths"]["/api/memory/query"]["post"]
    assert route["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MemoryRequest"
    }
    assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MemoryResponse"
    }
    tools = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    tool = tools["memory_query"]
    assert tool.input_schema["required"] == ["request"]
    assert tool.input_schema["$defs"]["MemoryRequest"] == MemoryRequest.model_json_schema()
    assert tool.output_schema == MemoryResponse.model_json_schema()
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.open_world_hint is False
    assert {"memory_pack", "memory_current", "search", "context"} <= tools.keys()


async def test_mcp_query_dispatch(service, monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", Mock(side_effect=AssertionError("legacy SDK")))
    body = {"query": QUESTION, "corpus": "docs", **SELECTORS}
    result = await mcp_server.mcp.call_tool("memory_query", {"request": body})
    service.execute.assert_awaited_once_with("query", MemoryRequest(**body))
    assert not result.is_error
    assert result.structured_content == service.execute.return_value.model_dump(mode="json")
    assert result.content[0].text == service.execute.return_value.canonical_json()


@pytest.mark.parametrize(
    "body", [{}, {"corpus": "docs", "budget": 511}, {"corpus": "docs", "intent": "invalid"}]
)
async def test_invalid_query_never_executes(client, service, body):
    response = await client.post("/api/memory/query", json=body)
    assert response.status_code == 422
    with pytest.raises(ToolError):
        await mcp_server.mcp.call_tool("memory_query", {"request": body})
    service.execute.assert_not_called()


async def test_query_errors_preserved(client, service):
    service.execute.side_effect = service.MemoryError("corpus_not_found", "Missing corpus", 404)
    body = {"query": QUESTION, "corpus": "docs"}
    expected = {"detail": {"code": "corpus_not_found", "message": "Missing corpus"}}
    response = await client.post("/api/memory/query", json=body)
    assert response.status_code == 404
    assert response.json() == expected
    result = await mcp_server.mcp.call_tool("memory_query", {"request": body})
    assert result.is_error
    assert result.structured_content == expected


@pytest.mark.parametrize("intent", INTENTS)
def test_sdk_local_query(service, intent_contract, intent):
    response = MindPalace().memory.query(query=QUESTION, corpus="docs", intent=intent, **SELECTORS)
    assert response is service.execute.return_value
    service.execute.assert_awaited_once_with(
        "query", MemoryRequest(query=QUESTION, corpus="docs", intent=intent, **SELECTORS)
    )


@pytest.mark.parametrize("override", [None, "other-docs"])
def test_sdk_remote_query(service, intent_contract, remote, override):
    mp = MindPalace("docs", base_url="http://test/", headers={"X-Test": "adapter"}, timeout=7)
    response = mp.memory.query(query=QUESTION, corpus=override, snapshot_id="b" * 64)
    service.execute.assert_awaited_once_with(
        "query", MemoryRequest(query=QUESTION, corpus=override or "docs", snapshot_id="b" * 64)
    )
    assert response == service.execute.return_value
    assert remote.call_args.args == ("http://test/api/memory/query",)
    assert remote.call_args.kwargs["headers"] == {"X-Test": "adapter"}
    assert remote.call_args.kwargs["timeout"] == 7
    assert remote.call_args.kwargs["json"]["intent"] == "auto"


@pytest.mark.parametrize("intent", [None, "change"])
def test_cli_query(service, intent_contract, remote, intent):
    args = ["query", "--corpus", "docs", "--query", QUESTION, "--base-url", "http://test"]
    for field, value in SELECTORS.items():
        args.extend(["--" + field.replace("_", "-"), str(value)])
    if intent:
        args.extend(["--intent", intent])
    result = CliRunner().invoke(memory_app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == service.execute.return_value.model_dump(mode="json")
    service.execute.assert_awaited_once_with(
        "query", MemoryRequest(query=QUESTION, corpus="docs", intent=intent or "auto", **SELECTORS)
    )


def test_sdk_and_cli_query_errors(service, intent_contract, remote):
    service.execute.side_effect = service.MemoryError("corpus_not_found", "Missing corpus", 404)
    with pytest.raises(MemoryClientError) as error:
        MindPalace(base_url="http://test").memory.query(query=QUESTION, corpus="docs")
    assert error.value.code == "corpus_not_found"
    assert error.value.status_code == 404
    result = CliRunner().invoke(memory_app, ["query", "--corpus", "docs", "--query", QUESTION])
    assert result.exit_code == 1
    assert "corpus_not_found: Missing corpus" in result.output


@pytest.mark.parametrize("args", [["query", "--corpus", "docs"], ["query", "--query", QUESTION]])
def test_cli_query_requires_question_and_corpus(service, args):
    result = CliRunner().invoke(memory_app, args)
    assert result.exit_code == 2
    service.execute.assert_not_called()


@pytest.mark.parametrize("path", ["/api/query/ask", "/api/query"])
@pytest.mark.parametrize("with_evidence", [False, True])
async def test_only_opt_in_ask_dispatches_query(client, service, monkeypatch, path, with_evidence):
    if with_evidence:
        service.execute.return_value.evidence = [
            Evidence(
                id="e1",
                claim_id="c1",
                version_id="v1",
                document_id="d1",
                path="deployment.md",
                source_hash="hash",
                chunk_id="chunk",
                heading=None,
                text="Deployment changed.",
                start_offset=0,
                end_offset=19,
                observed_at="2026-09-01T00:00:00Z",
            )
        ]
    generate = AsyncMock(return_value="Changed [evidence:e1].")
    monkeypatch.setattr(ask_router.llm_service, "generate", generate)
    monkeypatch.setattr(
        ask_router.embedder, "embed_single", Mock(side_effect=AssertionError("RAG"))
    )
    response = await client.post(
        path,
        json={
            "question": QUESTION,
            "mode": "memory",
            "corpus": "docs",
            **SELECTORS,
        },
    )
    assert response.status_code == 200
    service.execute.assert_awaited_once_with(
        "query", MemoryRequest(query=QUESTION, corpus="docs", **SELECTORS)
    )
    assert response.json()["memory"] == service.execute.return_value.model_dump(mode="json")
    if with_evidence:
        assert service.execute.return_value.canonical_json() in generate.await_args.args[0]
        assert response.json()["sources"] == ["deployment.md"]
    else:
        generate.assert_not_called()
