# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The memory client this integration uses: HTTP and the public REST API.

Nothing here imports `api.services`, a repository, a table, or an embedding
internals. That is not tidiness -- it is the claim the example exists to
demonstrate. An application that has to know how Mind Palace stores things
cannot be moved to a different deployment of it, and would break the day the
storage changed while the contract did not.

So the whole client is three things: a URL, a bearer credential, and the public
JSON contract. `MIND_PALACE_BASE_URL` names a remote service; `MIND_PALACE_APP_URL`
is the in-process ASGI application, used by the demo and the tests so they need
no listening port. Both go through the same middleware, the same authorization
choke point and the same row-level policies, so nothing here is a shortcut that
production would not also take.

`httpx` is the only dependency. It is the same library a Node, Go or Rust client
would use against `GET /openapi.json`, which is the point: this file is a
reference for an HTTP contract, not for Python.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

#: Where the Mind Palace service is. A host application sets this to its own
#: deployment; the demo leaves it unset and uses the in-process app.
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def base_url() -> str:
    """The service this client talks to.

    Raises rather than defaulting: a memory client that silently invented its
    own endpoint would fail open somewhere less obvious.
    """
    url = os.getenv("MIND_PALACE_BASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "MIND_PALACE_BASE_URL is not set. Point it at the Mind Palace service, "
            "for example http://mindpalace.internal:8000"
        )
    return url.rstrip("/")


class MemoryDenied(Exception):
    """Mind Palace refused the request.

    Carries the public `code` so the application can tell "you may not see this
    corpus" from "that corpus does not exist", without parsing prose. Both arrive
    as the same `404 corpus_not_found`, by design: whether another tenant's corpus
    exists is not this application's business either.
    """

    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class Claim(dict):
    """One memory, as the public contract describes it.

    A `dict` subclass rather than a dataclass, because the contract is JSON: a
    field added to the response is reachable here without this file changing, and
    nothing pretends to validate a shape the server owns. Attribute access is
    added for readability only.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class MemoryResponse(dict):
    """A memory response, with `current_memories` as the ergonomic name.

    Same reasoning as `Claim`: the response body is the contract, and this only
    saves the caller from writing `["current_memories"]`.
    """

    def __getattr__(self, name):
        try:
            value = self[name]
        except KeyError:
            raise AttributeError(name) from None
        if name == "current_memories":
            return [Claim(item) for item in value]
        return value


class MemoryClient:
    """One workspace's view of memory, bound to its own credential.

    The credential belongs to the *workspace*, not to a user. Mind Palace is
    therefore told which workspace this is by which corpora the credential covers,
    and never by a name in a request body.
    """

    def __init__(self, credential: str, *, client: httpx.AsyncClient | None = None):
        self.credential = credential
        # An injected client is used by the demo and tests so they need no port.
        # A real deployment omits it and gets one per client, or its own pool.
        self._client = client
        self._owns_client = client is None

    async def _post(self, path: str, payload: dict[str, Any]) -> MemoryResponse:
        headers = {"Authorization": f"Bearer {self.credential}"}
        if self._client is not None:
            return await self._send(self._client, path, payload, headers)
        async with httpx.AsyncClient(base_url=base_url(), timeout=DEFAULT_TIMEOUT) as client:
            return await self._send(client, path, payload, headers)

    @staticmethod
    async def _send(client, path, payload, headers) -> MemoryResponse:
        try:
            response = await client.post(path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # Transport failures are not refusals. Saying otherwise would let an
            # application treat an outage as a permission decision.
            raise MemoryDenied("memory_unavailable", f"cannot reach Mind Palace: {exc}", 503)
        if response.status_code >= 400:
            raise MemoryDenied(*_refusal(response))
        return MemoryResponse(response.json())

    async def remember(self, corpus: str, statement: str, *, key: str | None = None):
        """Record a fact as authoritative memory. The product write path."""
        body = {"statement": statement, "corpus": corpus}
        if key:
            body["key"] = key
        return await self._post("/api/memory/remember", body)

    async def current(self, corpus: str, query: str = ""):
        """Every memory in a corpus that is current, optionally narrowed by query."""
        return await self._post("/api/memory/current", {"corpus": corpus, "query": query})

    async def query(self, corpus: str, question: str, *, include_receipt: bool = False):
        """Ask the archive. `include_receipt` adds proof of what answered."""
        return await self._post(
            "/api/memory/query",
            {"corpus": corpus, "query": question, "include_receipt": include_receipt},
        )

    async def explain(self, corpus: str, question: str):
        """The same answer with its receipt, for an application that must show it."""
        return await self.query(corpus, question, include_receipt=True)

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()


def _refusal(response: httpx.Response) -> tuple[str, str, int]:
    """Read the public error envelope, falling back without inventing detail.

    The contract is `{"detail": {"code", "message"}}`. A proxy or a crash may
    answer with something else; that becomes a generic code rather than a
    fabricated one, so an application cannot branch on a shape that was never
    promised.
    """
    code, message = "http_error", f"Mind Palace returned {response.status_code}"
    try:
        detail = response.json()
        if isinstance(detail, dict):
            detail = detail.get("detail", detail)
            if isinstance(detail, dict):
                code = str(detail.get("code", code))
                message = str(detail.get("message", message))
    except ValueError:
        pass
    return code, message, response.status_code


def http_client(base_url: str) -> httpx.AsyncClient:
    """An HTTP client pointed at a running Mind Palace, over a real socket.

    This is the whole integration surface. There is no in-process shortcut left in
    the example -- no `ASGITransport`, no import of the server's `app` object --
    because a shortcut there would mean the demo proves the example works against
    a private interface rather than against the documented one. A consumer
    integrating with Mind Palace talks to a URL; so does this.
    """
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=DEFAULT_TIMEOUT)
