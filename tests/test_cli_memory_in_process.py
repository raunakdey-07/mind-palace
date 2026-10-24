"""`mindpalace memory ...` runs in-process unless a remote URL is given.

`memory pack` used to default to ``http://127.0.0.1:8000`` while `recall`,
`explain`, `history`, `receipt` and `ingest-repo` ran in-process. A user who
followed the quickstart for a bounded context therefore needed a server that
none of the other core commands needed, and got a bare connection refusal when
there was none.

These tests pin both halves of the boundary: no server and no HTTP call when
`--base-url` is absent, and the same HTTP client when it is present (or when
`MIND_PALACE_BASE_URL` names a remote service).
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from mindpalace_sdk import MindPalace

CORPUS = "cli-memory-in-process"
STATEMENT = "The default embedding backend is ONNX Runtime."


@pytest.fixture(scope="module")
def seeded() -> None:
    """A corpus with one fact, written through the same local SDK path."""
    MindPalace(CORPUS).remember(STATEMENT, key="default-embedding-backend")


def _invoke(args, base_url_env: str | None = None):
    from cli.main import app

    env = dict(os.environ)
    env["MIND_PALACE_RUNTIME"] = "0"
    env.pop("MIND_PALACE_BASE_URL", None)
    if base_url_env is not None:
        env["MIND_PALACE_BASE_URL"] = base_url_env
    return CliRunner().invoke(app, args, env=env)


@pytest.mark.parametrize(
    "command,args",
    [
        ("pack", ["--query", "embedding backend", "--budget", "4000"]),
        ("current", []),
        ("history", []),
        ("feed", []),
    ],
)
def test_memory_commands_need_no_server(seeded, command, args):
    """The regression: no HTTP hop exists when no base URL was asked for.

    `httpx.post` and `httpx.get` are patched to raise, so the test asserts the
    code path rather than whether something happens to be listening on :8000.
    """
    with (
        patch("httpx.post", side_effect=AssertionError("must not use HTTP")),
        patch("httpx.get", side_effect=AssertionError("must not use HTTP")),
    ):
        result = _invoke(["memory", command, "--corpus", CORPUS, *args])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["corpus"] == CORPUS


def test_explicit_base_url_still_uses_the_http_client(monkeypatch):
    """`--base-url` selects HTTP even when a local database is available.

    The fake transport answers 503, so the assertion is not "the output looks
    local" but "one POST went to /api/memory/pack and its structured error was
    reported".
    """
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"code": "service_unavailable", "message": "not ready"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "post", client.post)
    try:
        result = _invoke(
            [
                "memory",
                "pack",
                "--corpus",
                CORPUS,
                "--query",
                "embedding backend",
                "--base-url",
                "http://127.0.0.1:8000",
            ]
        )
    finally:
        client.close()

    assert result.exit_code == 1
    assert len(calls) == 1
    assert str(calls[0].url) == "http://127.0.0.1:8000/api/memory/pack"
    assert "service_unavailable: not ready" in result.stderr


def test_base_url_environment_variable_selects_http(monkeypatch):
    """A remote service named for the process is a deliberate choice too."""
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"code": "service_unavailable", "message": "not ready"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "post", client.post)
    try:
        result = _invoke(
            ["memory", "pack", "--corpus", CORPUS, "--query", "embedding"],
            base_url_env="https://memory.example",
        )
    finally:
        client.close()

    assert result.exit_code == 1
    assert str(calls[0].url) == "https://memory.example/api/memory/pack"


def test_serve_states_the_in_process_boundary():
    """`serve` names both the direct form and why the CLI does not need it."""
    result = _invoke(["serve"])

    assert result.exit_code == 0, result.output
    assert "mindpalace serve --run" in result.output
    assert "in-process" in result.output


def test_serve_help_offers_run():
    result = _invoke(["serve", "--help"])

    assert result.exit_code == 0, result.output
    # Help output may include ANSI codes in some environments
    clean = result.output
    # Strip ANSI escape sequences
    import re

    clean = re.sub(r"\x1b\[[0-9;]*m", "", clean)
    assert "--run" in clean
