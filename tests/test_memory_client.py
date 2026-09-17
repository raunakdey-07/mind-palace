"""SDK/CLI adapters against real memory models, with only backend/transport fakes.

The public service may not exist yet. Only that missing module is substituted;
when present, its execute boundary is patched and its MemoryError is used as-is.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from api.models.memory import MemoryRequest, MemoryResponse
from mindpalace_sdk import MemoryClientError, MindPalace

STAMP = "2026-09-01T12:00:00Z"
CLAIM_ID = "a" * 64
SNAPSHOT_ID = "b" * 64
VERSION_ID = "c" * 64
EVIDENCE_ID = "d" * 64
DOCUMENT_ID = "e" * 64
SOURCE_HASH = "f" * 64


@pytest.fixture
def response_body():
    claim = {
        "id": CLAIM_ID,
        "key": "auth.method",
        "value": "clé",
        "claim": "Authentication uses a clé.",
        "status": "CURRENT",
        "version_id": VERSION_ID,
        "path": "notes.md",
        "observed_at": STAMP,
        "valid_from": STAMP,
        "valid_until": None,
        "supersedes_id": None,
        "evidence_ids": [EVIDENCE_ID],
    }
    previous = {**claim, "id": "1" * 64, "status": "SUPERSEDED"}
    uncertain = {**claim, "id": "2" * 64, "status": "UNCERTAIN"}
    conflicting = {**claim, "id": "3" * 64, "status": "CONFLICTING"}
    return {
        "schema_version": 1,
        "query": "auth",
        "corpus": "test",
        "state": {"as_of": STAMP, "valid_at": STAMP, "snapshot": SNAPSHOT_ID},
        "current_memories": [claim],
        "historical_memories": [previous],
        "uncertain_memories": [uncertain],
        "changes": [
            {
                "version_id": VERSION_ID,
                "predecessor_id": "4" * 64,
                "event": "modified",
                "path": "notes.md",
                "observed_at": STAMP,
                "memory_changed": True,
                "relationship": "SUPERSEDES",
                "previous": [previous],
                "current": [claim],
            }
        ],
        "conflicts": [{"id": "5" * 64, "key": "auth.method", "claims": [conflicting]}],
        "constraints": ["Use attributed evidence."],
        "evidence": [
            {
                "id": EVIDENCE_ID,
                "claim_id": CLAIM_ID,
                "version_id": VERSION_ID,
                "document_id": DOCUMENT_ID,
                "path": "notes.md",
                "source_hash": SOURCE_HASH,
                "chunk_id": "6" * 64,
                "heading": "Authentication",
                "text": "Authentication uses a clé.",
                "start_offset": 0,
                "end_offset": 26,
                "observed_at": STAMP,
            }
        ],
        "sources": [
            {
                "document_id": DOCUMENT_ID,
                "version_id": VERSION_ID,
                "path": "notes.md",
                "source_hash": SOURCE_HASH,
                "observed_at": STAMP,
            }
        ],
        "snapshot": {
            "id": SNAPSHOT_ID,
            "as_of": STAMP,
            "observed_at": STAMP,
            "version_ids": [VERSION_ID],
        },
        "truncated": False,
        "budget_unit": "unicode_characters",
    }


@pytest.fixture
def contract(monkeypatch, response_body):
    try:
        service = importlib.import_module("api.services.memory_public")
    except ModuleNotFoundError as exc:
        if exc.name != "api.services.memory_public":
            raise

        class MemoryError(Exception):
            def __init__(self, code, message, status_code):
                self.code, self.message, self.status_code = code, message, status_code
                super().__init__(message)

        service = ModuleType("api.services.memory_public")
        service.MemoryError = MemoryError
        service.execute = None
        monkeypatch.setitem(sys.modules, service.__name__, service)

    monkeypatch.setattr(
        service, "execute", AsyncMock(return_value=MemoryResponse.model_validate(response_body))
    )
    return SimpleNamespace(request=MemoryRequest, response=MemoryResponse, service=service)


@pytest.fixture
def transport(monkeypatch, response_body):
    calls = []
    reply = SimpleNamespace(status=200, body=response_body)

    def handle(request):
        calls.append(request)
        return httpx.Response(reply.status, json=reply.body)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "post", client.post)
    yield calls, reply
    client.close()


OPERATIONS = [
    ("current", {}, "current", {}),
    (
        "history",
        {"as_of": STAMP, "path": "notes.md"},
        "history",
        {"as_of": STAMP, "path": "notes.md"},
    ),
    ("changes", {"as_of": STAMP}, "changes", {"as_of": STAMP}),
    ("evidence", {"claim_id": CLAIM_ID}, "evidence", {"claim_id": CLAIM_ID}),
    (
        "as_of",
        {"timestamp": STAMP, "valid_at": STAMP},
        "as-of",
        {"as_of": STAMP, "valid_at": STAMP},
    ),
    ("snapshot", {"as_of": STAMP}, "snapshot", {"as_of": STAMP}),
    ("replay_snapshot", {"snapshot_id": SNAPSHOT_ID}, "replay", {"snapshot_id": SNAPSHOT_ID}),
    ("pack", {"budget": 1234, "path": "notes.md"}, "pack", {"budget": 1234, "path": "notes.md"}),
    (
        "replay_snapshot",
        {"snapshot_id": SNAPSHOT_ID, "path": "notes.md"},
        "replay",
        {"snapshot_id": SNAPSHOT_ID, "path": "notes.md"},
    ),
    (
        "pack",
        {"snapshot_id": SNAPSHOT_ID, "budget": 1234, "path": "notes.md"},
        "pack",
        {"snapshot_id": SNAPSHOT_ID, "budget": 1234, "path": "notes.md"},
    ),
]


@pytest.mark.parametrize("method,kwargs,operation,fields", OPERATIONS)
def test_remote_dispatch(contract, transport, method, kwargs, operation, fields):
    calls, reply = transport
    headers = {"Authorization": "Bearer test-token"}
    mp = MindPalace("default", base_url="https://memory.example/", headers=headers, timeout=17)
    headers.clear()
    result = getattr(mp.memory, method)(query="auth", **kwargs)
    assert isinstance(result, contract.response)
    assert result.model_dump(mode="json") == reply.body
    assert result.canonical_json() == json.dumps(
        reply.body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert result.current_memories[0].id == CLAIM_ID
    assert result.evidence[0].observed_at == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert result.snapshot.id == SNAPSHOT_ID
    assert len(calls) == 1
    assert str(calls[0].url) == f"https://memory.example/api/memory/{operation}"
    assert calls[0].method == "POST"
    assert calls[0].headers["Authorization"] == "Bearer test-token"
    assert calls[0].extensions["timeout"]["read"] == 17
    assert json.loads(calls[0].content) == contract.request(
        corpus="default", query="auth", **fields
    ).model_dump(mode="json")
    contract.service.execute.assert_not_called()


def test_defaults_override_and_datetime(contract, transport):
    calls, _ = transport
    mp = MindPalace("default", base_url="https://memory.example")
    mp.memory.current(corpus="override")
    assert json.loads(calls[-1].content)["corpus"] == "override"
    assert json.loads(calls[-1].content)["query"] == ""
    assert calls[-1].extensions["timeout"]["read"] == 30
    mp.memory.pack()
    assert json.loads(calls[-1].content)["budget"] == 8000
    MindPalace(base_url="https://memory.example").memory.as_of(
        datetime(2026, 9, 1, 12, tzinfo=UTC), corpus="explicit"
    )
    assert json.loads(calls[-1].content)["as_of"] == STAMP


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize(
    "method,kwargs,field",
    [
        ("current", {"corpus": None}, "corpus"),
        ("current", {"corpus": ""}, "corpus"),
        ("current", {"corpus": "bad name"}, "corpus"),
        ("current", {"corpus": "x" * 129}, "corpus"),
        ("current", {"query": "   "}, "query"),
        ("current", {"query": "x" * 2001}, "query"),
        ("current", {"path": ""}, "path"),
        ("current", {"path": "x" * 2049}, "path"),
        ("current", {"valid_at": "2026-09-01T12:00:00"}, "valid_at"),
        ("snapshot", {"as_of": "2026-09-01T12:00:00"}, "as_of"),
        ("as_of", {"timestamp": "not-a-timestamp"}, "as_of"),
        ("evidence", {"claim_id": "claim-1"}, "claim_id"),
        ("evidence", {"claim_id": "A" * 64}, "claim_id"),
        ("evidence", {"claim_id": "g" * 64}, "claim_id"),
        ("replay_snapshot", {"snapshot_id": "snapshot-1"}, "snapshot_id"),
        ("replay_snapshot", {"snapshot_id": "b" * 63}, "snapshot_id"),
        ("replay_snapshot", {"snapshot_id": "b" * 65}, "snapshot_id"),
        ("pack", {"snapshot_id": "snapshot-1"}, "snapshot_id"),
        ("replay_snapshot", {"snapshot_id": SNAPSHOT_ID, "path": ""}, "path"),
        ("pack", {"budget": 511}, "budget"),
        ("pack", {"budget": 128001}, "budget"),
        ("pack", {"budget": "8000"}, "budget"),
        ("pack", {"budget": 8000.0}, "budget"),
        ("pack", {"budget": True}, "budget"),
    ],
)
def test_central_request_validation_precedes_transport(
    contract, transport, remote, method, kwargs, field
):
    calls, _ = transport
    mp = MindPalace(base_url="https://memory.example" if remote else None)
    with pytest.raises(ValidationError) as caught:
        getattr(mp.memory, method)(**{"corpus": "test", **kwargs})
    assert caught.value.errors()[0]["loc"] == (field,)
    assert not calls
    contract.service.execute.assert_not_called()


@pytest.mark.parametrize("budget", [512, 128000])
def test_real_request_normalization_and_budget_boundaries(transport, budget):
    calls, _ = transport
    MindPalace("test", base_url="https://memory.example").memory.pack(
        query="  clé  ", budget=budget, as_of="2026-09-01T14:00:00+02:00"
    )
    request = MemoryRequest.model_validate_json(calls[0].content)
    assert request.query == "clé"
    assert request.budget == budget
    assert request.as_of == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert json.loads(calls[0].content)["query"] == "clé"


@pytest.mark.parametrize("envelope", [None, "detail", "error"])
def test_stable_server_errors(contract, transport, envelope):
    _, reply = transport
    detail = {"code": "snapshot_not_found", "message": "Snapshot does not exist"}
    reply.status = 404
    reply.body = {envelope: detail} if envelope else detail
    with pytest.raises(MemoryClientError) as caught:
        MindPalace("test", base_url="https://memory.example").memory.replay_snapshot(SNAPSHOT_ID)
    assert caught.value.code == "snapshot_not_found"
    assert caught.value.message == "Snapshot does not exist"
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "exception,code,status",
    [
        (httpx.ReadTimeout("secret URL"), "timeout", 504),
        (httpx.ConnectError("secret URL"), "transport_error", 503),
    ],
)
def test_transport_errors(contract, monkeypatch, exception, code, status):
    monkeypatch.setattr(httpx, "post", Mock(side_effect=exception))
    with pytest.raises(MemoryClientError) as caught:
        MindPalace("test", base_url="https://memory.example").memory.current()
    assert (caught.value.code, caught.value.status_code) == (code, status)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "status,body,code,error_status",
    [
        (503, b"upstream secret", "http_error", 503),
        (422, b'{"detail":[{"msg":"invalid"}]}', "http_error", 422),
        (200, b"not json", "invalid_response", 502),
        (200, b'{"wrong":true}', "invalid_response", 502),
    ],
)
def test_invalid_responses(contract, monkeypatch, status, body, code, error_status):
    monkeypatch.setattr(httpx, "post", Mock(return_value=httpx.Response(status, content=body)))
    with pytest.raises(MemoryClientError) as caught:
        MindPalace("test", base_url="https://memory.example").memory.current()
    assert (caught.value.code, caught.value.status_code) == (code, error_status)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "path,value",
    [
        (("result",), {"ok": True}),
        (("budget_unit",), "tokens"),
        (("state", "as_of"), "2026-09-01T12:00:00"),
        (("current_memories", 0, "status"), "INVALID"),
        (("evidence", 0, "observed_at"), "not-a-timestamp"),
        (("sources", 0, "unexpected"), True),
        (("snapshot", "version_ids"), "not-a-list"),
        (("changes", 0, "relationship"), "INVALID"),
        (("conflicts", 0, "claims", 0, "status"), "INVALID"),
    ],
)
def test_real_response_validation(transport, path, value):
    calls, reply = transport
    target = reply.body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(MemoryClientError) as caught:
        MindPalace("test", base_url="https://memory.example").memory.current()
    assert len(calls) == 1
    assert (caught.value.code, caught.value.status_code) == ("invalid_response", 502)
    assert isinstance(caught.value.__cause__, ValidationError)
    assert caught.value.__cause__.errors()[0]["loc"] == path


@pytest.mark.parametrize("method,kwargs,operation,fields", OPERATIONS)
def test_local_dispatch(contract, method, kwargs, operation, fields):
    result = getattr(MindPalace().memory, method)(corpus="test", query="auth", **kwargs)
    assert result is contract.service.execute.return_value
    contract.service.execute.assert_awaited_once()
    actual_operation, request = contract.service.execute.call_args.args
    assert actual_operation == operation
    assert isinstance(request, contract.request)
    assert request == contract.request(corpus="test", query="auth", **fields)


def test_local_error_propagates(contract):
    error = contract.service.MemoryError("corpus_not_found", "No corpus", 404)
    contract.service.execute.side_effect = error
    with pytest.raises(contract.service.MemoryError) as caught:
        MindPalace().memory.current(corpus="missing")
    assert caught.value is error


def test_local_rejects_running_loop_without_creating_coroutine(contract):
    async def call():
        with pytest.raises(RuntimeError, match="active event loop"):
            MindPalace().memory.current(corpus="test")

    asyncio.run(call())
    contract.service.execute.assert_not_called()


def test_import_and_remote_construction_do_not_load_local_services():
    script = """
import sys
class BlockServices:
    def find_spec(self, fullname, *args):
        if fullname.startswith('api.services'):
            raise AssertionError('Remote SDK imported ' + fullname)
sys.meta_path.insert(0, BlockServices())
from mindpalace_sdk import MindPalace
from cli.main import app
mp = MindPalace('test', base_url='https://memory.example')
assert not hasattr(mp, '_embedder')
assert not hasattr(mp, '_ingestion')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("create", [True, False])
def test_legacy_named_initialization_and_sync(monkeypatch, create):
    embedder = ModuleType("api.services.embedder")
    embedder.Embedder = Mock()
    ingestion = ModuleType("api.services.ingestion")
    ingestion.IngestionService = Mock()
    ingestion.IngestionService.return_value.sync_repo = AsyncMock(
        return_value={
            "success": True,
            "added": 1,
            "changed": 2,
            "unchanged": 3,
            "deleted": 4,
            "failed": 0,
            "chunk_count": 5,
            "duration_ms": 6,
        }
    )
    monkeypatch.setitem(sys.modules, embedder.__name__, embedder)
    monkeypatch.setitem(sys.modules, ingestion.__name__, ingestion)
    ensure = AsyncMock()
    monkeypatch.setattr(MindPalace, "_ensure_corpus", ensure)
    monkeypatch.setattr(MindPalace, "_run", staticmethod(asyncio.run))
    monkeypatch.setattr(MindPalace, "_corpus_id", Mock(return_value="corpus-id"))
    mp = MindPalace("legacy", create_if_missing=create)
    embedder.Embedder.assert_called_once_with()
    ingestion.IngestionService.assert_called_once_with()
    assert ensure.await_count == int(create)
    summary = mp.sync("docs", delete_removed=False)
    assert (summary.success, summary.added, summary.chunk_count) == (True, 1, 5)
    mp._ingestion.sync_repo.assert_awaited_once_with("docs", "corpus-id", delete_removed=False)


@pytest.mark.parametrize("strategy", ["vector", "hybrid", "hybrid_rrf"])
def test_legacy_search_and_context(monkeypatch, strategy):
    @asynccontextmanager
    async def session_scope():
        yield "session"

    db = ModuleType("api.services.db")
    db.session_scope = session_scope
    corpora = ModuleType("api.services.corpora")
    corpora.get_corpus_by_name = AsyncMock(return_value={"id": "corpus-id"})
    retrieval = ModuleType("api.services.retrieval")
    retrieval.RetrievalService = Mock()
    retrieval.RetrievalService.return_value.search = AsyncMock(return_value=["evidence"])
    packer = ModuleType("api.services.context_packer")
    packer.pack_context = Mock(return_value="packed")
    for module in (db, corpora, retrieval, packer):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(MindPalace, "_run", staticmethod(asyncio.run))
    mp = MindPalace()
    mp.name = "legacy"
    mp._embedder = Mock()
    mp._embedder.embed_single.return_value = [0.5]
    assert mp.search("question", k=3, strategy=strategy) == ["evidence"]
    assert mp.context("question", k=3, strategy=strategy, budget_tokens=1000) == "packed"
    corpora.get_corpus_by_name.assert_awaited_with("session", "legacy")
    retrieval.RetrievalService.assert_called_with("session")
    retrieval.RetrievalService.return_value.search.assert_awaited_with(
        [0.5],
        k=3,
        hybrid=strategy != "vector",
        rrf=strategy == "hybrid_rrf",
        query_text="question" if strategy != "vector" else None,
        corpus_id="corpus-id",
    )
    packer.pack_context.assert_called_once_with(
        "question",
        ["evidence"],
        budget_tokens=1000,
        strategy=strategy,
    )
    corpora.get_corpus_by_name.return_value = None
    from mindpalace_sdk import CorpusNotFoundError

    with pytest.raises(CorpusNotFoundError, match="legacy"):
        mp.search("question")


CLI_CASES = [
    ("current", [], "current", {}),
    (
        "history",
        ["--as-of", STAMP, "--path", "notes.md"],
        "history",
        {"as_of": STAMP, "path": "notes.md"},
    ),
    ("changes", ["--valid-at", STAMP], "changes", {"valid_at": STAMP}),
    ("evidence", ["--claim-id", CLAIM_ID], "evidence", {"claim_id": CLAIM_ID}),
    ("as-of", ["--as-of", STAMP], "as-of", {"as_of": STAMP}),
    ("snapshot", ["--as-of", STAMP], "snapshot", {"as_of": STAMP}),
    ("replay", ["--snapshot-id", SNAPSHOT_ID], "replay", {"snapshot_id": SNAPSHOT_ID}),
    ("pack", ["--budget", "1500"], "pack", {"budget": 1500}),
    (
        "replay",
        ["--snapshot-id", SNAPSHOT_ID, "--path", "notes.md"],
        "replay",
        {"snapshot_id": SNAPSHOT_ID, "path": "notes.md"},
    ),
    (
        "pack",
        ["--snapshot-id", SNAPSHOT_ID, "--budget", "1500", "--path", "notes.md"],
        "pack",
        {"snapshot_id": SNAPSHOT_ID, "budget": 1500, "path": "notes.md"},
    ),
]


@pytest.mark.parametrize("command,args,operation,fields", CLI_CASES)
def test_cli_dispatch(contract, transport, command, args, operation, fields):
    from cli.main import app

    calls, reply = transport
    result = CliRunner().invoke(
        app, ["memory", command, "--corpus", "test", "--query", "auth", *args]
    )
    assert result.exit_code == 0, result.output
    assert (
        result.stdout
        == json.dumps(reply.body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    )
    assert MemoryResponse.model_validate_json(result.stdout).model_dump(mode="json") == reply.body
    assert str(calls[-1].url) == f"http://127.0.0.1:8000/api/memory/{operation}"
    assert json.loads(calls[-1].content) == contract.request(
        corpus="test", query="auth", **fields
    ).model_dump(mode="json")


def test_cli_base_url_and_errors(contract, transport):
    from cli.main import app

    calls, reply = transport
    reply.status, reply.body = 404, {"code": "corpus_not_found", "message": "No corpus"}
    result = CliRunner().invoke(
        app,
        [
            "memory",
            "current",
            "--corpus",
            "test",
            "--base-url",
            "https://memory.example/root/",
        ],
    )
    assert str(calls[-1].url) == "https://memory.example/root/api/memory/current"
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "corpus_not_found: No corpus" in result.stderr
    assert "Traceback" not in result.output


@pytest.mark.parametrize("command,args,operation,fields", CLI_CASES)
def test_cli_requires_corpus(contract, transport, command, args, operation, fields):
    from cli.main import app

    result = CliRunner().invoke(app, ["memory", command, *args])
    assert result.exit_code == 2
    assert not transport[0]


@pytest.mark.parametrize(
    "args",
    [
        ["as-of"],
        ["evidence"],
        ["replay"],
        ["as-of", "--as-of", "2026-09-01T12:00:00"],
        ["pack", "--budget", "not-an-integer"],
        ["pack", "--budget", "511"],
        ["pack", "--budget", "128001"],
        ["evidence", "--claim-id", "claim-1"],
        ["replay", "--snapshot-id", "snapshot-1"],
        ["pack", "--snapshot-id", "snapshot-1"],
        ["replay", "--snapshot-id", SNAPSHOT_ID, "--path", ""],
        ["current", "--query", "   "],
        ["current", "--path", ""],
        ["current", "--valid-at", "2026-09-01T12:00:00"],
    ],
)
def test_cli_invalid_arguments(contract, transport, args):
    from cli.main import app

    result = CliRunner().invoke(app, ["memory", *args, "--corpus", "test"])
    assert result.exit_code == 2
    assert not transport[0]
