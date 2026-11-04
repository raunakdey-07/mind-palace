"""Bounded retry and backoff for the remote SDK, against a fake HTTP server.

No mocks of the SDK: a real ``ThreadingHTTPServer`` answers real HTTP over
localhost, and the SDK's only remote entry point is ``MemoryClient._send``,
which looks ``httpx`` up by method name at call time.

The fake server consumes ``.responses`` in order and repeats the last one
forever, records every ``.requests`` as ``(method, path, body_bytes)``, and can
delay each answer through ``.delay``.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from api.models.memory import MemoryResponse
from api.models.remember import RememberResult
from mindpalace_sdk import MemoryClientError, MindPalace


class _Handler(BaseHTTPRequestHandler):
    def _record(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append((self.command, self.path, body))
        if self.server.delay:
            time.sleep(self.server.delay)

    def next_response(self):
        responses = self.server.responses
        if len(responses) > 1:
            return responses.pop(0)
        # The last answer is repeated forever, so a server that always fails
        # keeps failing and a server that once answered keeps answering.
        return responses[-1]

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self._record()
        status, body = self.next_response()
        self._respond(status, body)

    def do_GET(self) -> None:
        self._record()
        status, body = self.next_response()
        self._respond(status, body)

    def log_message(self, fmt, *args) -> None:
        return  # keep the test output quiet


@pytest.fixture
def server():
    responses: list = []
    requests: list = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.responses = responses
    httpd.requests = requests
    httpd.delay = 0.0
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def _memory_body(corpus: str) -> bytes:
    return json.dumps(MemoryResponse(query="", corpus=corpus).model_dump(mode="json")).encode()


def _remember_body(corpus: str) -> bytes:
    return json.dumps(
        RememberResult(
            corpus=corpus,
            version_id="a" * 64,
            event="NEW",
            path="memory/x.md",
            key="x",
            changed=True,
        ).model_dump(mode="json")
    ).encode()


def _client(server, *, name=None, retries=2, backoff=0.2, timeout=5):
    return MindPalace(
        name or ("t" + uuid4().hex[:12]),
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        retries=retries,
        backoff=backoff,
        timeout=timeout,
    )


def test_retries_then_success(server):
    """503, 503, then a valid 200: the call succeeds after 3 requests."""
    server.responses = [(503, b"{}"), (503, b"{}"), (200, _memory_body("t1"))]
    mp = _client(server, name="t1")
    result = mp.memory.current(query="")
    assert isinstance(result, MemoryResponse)
    assert len(server.requests) == 3


def test_always_503_exhausts_retries(server):
    """Always 503, retries=2: 3 attempts, sleeps 0.2 + 0.4, then an error."""
    server.responses = [(503, b"{}")]
    mp = _client(server)
    started = time.monotonic()
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.current(query="")
    elapsed = time.monotonic() - started
    assert caught.value.status_code == 503
    assert caught.value.attempts == 3
    assert len(server.requests) == 3
    assert 0.6 <= elapsed < 2.0, elapsed


def test_400_is_not_retried(server):
    """A 4xx is returned once, never retried."""
    server.responses = [(400, b'{"detail":{"code":"x","message":"y"}}')]
    mp = _client(server)
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.current(query="")
    assert caught.value.attempts == 1
    assert len(server.requests) == 1


def test_500_is_not_retried(server):
    """A 5xx that is not 502/503/504 is returned once, never retried."""
    server.responses = [(500, b"{}")]
    mp = _client(server)
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.current(query="")
    assert caught.value.attempts == 1
    assert len(server.requests) == 1


def test_closed_port_transport_error(server):
    """A closed port fails fast with transport_error, one attempt."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    mp = MindPalace("t5", base_url=f"http://127.0.0.1:{port}", retries=0, timeout=1)
    started = time.monotonic()
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.current(query="")
    elapsed = time.monotonic() - started
    assert caught.value.code == "transport_error"
    assert caught.value.attempts == 1
    assert elapsed < 1.5, elapsed


def test_timeout_retries_once(server):
    """A 5 s delay with a 0.5 s timeout, retries=1: 2 attempts, then timeout."""
    server.delay = 5
    server.responses = [(200, _memory_body("t6"))]
    mp = _client(server, name="t6", timeout=0.5, retries=1, backoff=0.2)
    started = time.monotonic()
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.current(query="")
    elapsed = time.monotonic() - started
    assert caught.value.code == "timeout"
    assert caught.value.status_code == 504
    assert caught.value.attempts == 2
    assert len(server.requests) == 2
    assert elapsed < 1.6, elapsed


def test_remember_retries_with_identical_body(server):
    """A retried remember re-sends the exact same body, so no second version."""
    corpus = "t7"
    server.responses = [(503, b"{}"), (200, _remember_body(corpus))]
    mp = _client(server, name=corpus)
    result = mp.memory.remember(statement="Our voice is warm and consistent.", key="brand.voice")
    assert result.event == "NEW"
    assert len(server.requests) == 2
    assert server.requests[0][2] == server.requests[1][2]


@pytest.mark.parametrize("kwargs", [{"retries": -1}, {"timeout": 0}, {"backoff": -0.1}])
def test_constructor_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        MindPalace("t8", **kwargs)


def test_invalid_response_not_retried(server):
    """A 200 with a non-JSON body is invalid_response, attempted once."""
    server.responses = [(200, b"not json")]
    mp = _client(server)
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.current(query="")
    assert caught.value.code == "invalid_response"
    assert caught.value.attempts == 1
    assert len(server.requests) == 1


def test_feed_retries_get(server):
    """feed uses GET and retries the same way: 3 GET requests on always-503."""
    server.responses = [(503, b"{}")]
    mp = _client(server, name="t10")
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.feed()
    assert caught.value.attempts == 3
    assert len(server.requests) == 3
    assert all(method == "GET" for method, _, _ in server.requests)


def test_retries_zero_single_attempt(server):
    """retries=0: exactly one attempt, no sleep, no retry."""
    server.responses = [(503, b"{}")]
    mp = _client(server, retries=0)
    started = time.monotonic()
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.current(query="")
    elapsed = time.monotonic() - started
    assert caught.value.attempts == 1
    assert len(server.requests) == 1
    assert elapsed < 1.5, elapsed


def test_remote_remember_refuses_file_before_sending(server):
    """file= with a base_url raises invalid_request without any HTTP call."""
    mp = _client(server, name="t12")
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.remember(statement="Our voice is warm and consistent.", file="note.md")
    assert caught.value.code == "invalid_request"
    assert caught.value.status_code == 422
    assert len(server.requests) == 0


def test_remember_timeout_message_is_per_call(server):
    """A timed-out remember carries its own message, not the default one."""
    server.delay = 5
    server.responses = [(200, _memory_body("t13"))]
    mp = _client(server, name="t13", timeout=0.5, retries=0)
    with pytest.raises(MemoryClientError) as caught:
        mp.memory.remember(statement="Our voice is warm and consistent.", key="k")
    assert caught.value.code == "timeout"
    assert caught.value.status_code == 504
    assert caught.value.message == "Memory write timed out"
