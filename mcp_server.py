# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""MCP (Model Context Protocol) server for Mind Palace.

A thin adapter exposing the Mind Palace core over MCP so any MCP-compatible
AI client can use corpus memory as tools. Deliberately minimal:
context, search, sync, list_corpora, and the public memory operations.
The memory tools do not expose arbitrary writes; snapshot creates immutable state.

Authentication is inherited from the HTTP surface, not reimplemented: the same
credential, the same corpus scope and the same read-write rule apply, because
the same choke point decides them. An MCP server is a stdio process launched by
one configured agent, so `MIND_PALACE_MCP_CREDENTIAL` carries that agent's
credential. It is an environment variable rather than a tool argument on purpose:
a tool argument is chosen by whoever calls the tool, and a caller who can choose
their own credential needs no authorization at all. With
`MIND_PALACE_AUTH_MODE=required` and no credential configured, every tool
refuses -- MCP is not a way around the HTTP boundary.

Run with:
    python -m mcp_server

Configure in an MCP client (example):
    {
      "mcpServers": {
        "mind-palace": {
          "command": "python",
          "args": ["-m", "mcp_server"],
          "env": {
            "DATABASE_URL": "postgresql://...",
            "MIND_PALACE_AUTH_MODE": "required",
            "MIND_PALACE_MCP_CREDENTIAL": "mpk_..."
          }
        }
      }
    }
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from api.models.memory import MemoryRequest, MemoryWire
from mindpalace_sdk import MindPalace

mcp = MCPServer("mind-palace")

# One client per corpus name, cached; MindPalace handles corpus creation.
_clients: dict[str, MindPalace] = {}


def _client(corpus: str) -> MindPalace:
    """A local client for this process's configured credential.

    The credential is the same one the tool scope resolves from. Without it,
    `MindPalace(corpus)` built an unnamed, unauthenticated client whose own
    authorization check ran as the default local principal -- so `context`,
    `search` and `sync` bypassed the very scope the tool had just established.
    """
    if corpus not in _clients:
        _clients[corpus] = MindPalace(corpus, api_key=os.getenv("MIND_PALACE_MCP_CREDENTIAL"))
    return _clients[corpus]


async def _resolved_principal():
    """The identity this MCP process acts as, from its configured credential."""
    from api.services.authz import AuthorizationError, auth_required, resolve_principal
    from api.services.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # As a header, not as a bare secret: `resolve_principal` takes the value an
        # `Authorization` header carries, and passing the secret alone failed the
        # scheme check -- so every MCP tool answered 401 even with a correctly
        # configured credential.
        credential = os.getenv("MIND_PALACE_MCP_CREDENTIAL") or None
        principal = await resolve_principal(db, f"Bearer {credential}" if credential else None)
    if auth_required() and principal.unrestricted:
        # `resolve_principal` returns the unrestricted principal only when the
        # mode is off, so this is reachable only if the mode changed underneath a
        # running process. Refusing is the safe reading of that.
        raise AuthorizationError(
            "unauthenticated",
            "MCP has no credential configured; set MIND_PALACE_MCP_CREDENTIAL",
            401,
        )
    return principal


@contextmanager
def _caller():
    """Run a synchronous tool as the configured identity, for its duration.

    These four tools are called outside any running loop, so `asyncio.run` is
    right here. It is a separate entry point from `_execute_memory` rather than a
    fallback inside one shared helper, because the memory tools run *inside* a
    loop and a single helper would have to choose and be wrong half the time.
    """
    import asyncio

    from api.services.db import principal_scope

    try:
        principal = asyncio.run(_resolved_principal())
    except Exception as exc:  # noqa: BLE001 - surfaced as a tool error, not a crash
        raise RuntimeError("could not resolve the configured credential") from exc
    with principal_scope(principal):
        yield principal


@mcp.tool()
def context(query: str, corpus: str, budget_tokens: int = 4096) -> str:
    """Retrieve model-ready context for a query from a named corpus.

    Returns bounded text with source attribution. Prefer this over raw
    search when feeding an LLM.
    """
    with _caller():
        pack = _client(corpus).context(query, budget_tokens=budget_tokens)
    return json.dumps(pack.to_dict(), indent=2)


@mcp.tool()
def search(query: str, corpus: str, k: int = 5) -> str:
    """Raw semantic search over a corpus. Returns ranked chunks with scores."""
    with _caller():
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
    with _caller():
        summary = _client(corpus).sync(path)
    return json.dumps(summary.__dict__, indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        openWorldHint=False,
    ),
)
def memory_remember(
    statement: str,
    corpus: str,
    key: str | None = None,
    file: str | None = None,
    supersedes_claim_id: str | None = None,
    topic_key: str | None = None,
) -> CallToolResult:
    """Record one statement as authoritative memory, with exact-substring evidence.

    The arguments mirror the SDK's `remember(statement, corpus=, key=, file=)`
    and the call goes through that same function, so an agent and a script cannot
    write memory differently.

    Without `file`, the evidence for the claim is the statement itself: it proves
    what you were told, not that it is true. With `file`, the statement must
    appear verbatim in that file, and the file must be inside
    MIND_PALACE_INGEST_ROOTS.

    Idempotent: submitting an identical statement again returns the same
    version_id with event UNCHANGED. Submitting the same `key` with new text
    supersedes the previous value.

    `supersedes_claim_id` retires one claim, wherever it lives: the full id, any
    unique prefix of it as `memory_recall` prints it, or a phrase from the claim's
    own text. A 64-character lowercase-hex string is an id, a shorter hex string is
    a prefix, and anything else is a phrase matched on content words -- so the
    agent holding "the primary datastore is PostgreSQL 15" does not have to copy an
    id out of a previous result. `topic_key` retires whatever is currently active
    under that key, so ADR-002 can replace ADR-001 without either file being
    edited. Either argument is refused, not guessed at, when it does not resolve to
    exactly one live claim, and a phrase that matches two is refused with their
    ids in the message.

    When the result carries `related_active_claims`, the corpus already holds
    claims that read like this one. It does not know whether they contradict,
    duplicate or should be replaced: pass `supersedes_claim_id` to settle it.

    Takes no credential. The caller's identity is the one
    MIND_PALACE_MCP_CREDENTIAL resolves to, and that identity needs write access
    to the corpus.
    """
    from api.services.authz import AuthorizationError
    from api.services.ingest_paths import IngestPathRefused, require_permitted
    from api.services.remember import RememberError
    from mindpalace_sdk import MemoryClientError

    try:
        resolved = _checked_file(statement, file, require_permitted)
    except IngestPathRefused as exc:
        return _refusal("ingest_path_refused", str(exc))
    except RememberError as exc:
        return _refusal("invalid_evidence", str(exc))

    try:
        # `_caller()` resolves the credential on entry, so the authorization
        # failure happens there and not inside the write. The whole block is
        # inside the try for that reason.
        with _caller():
            written = _client(corpus).remember(
                statement,
                corpus=corpus,
                key=key,
                file=resolved,
                supersedes_claim_id=supersedes_claim_id,
                topic_key=topic_key,
            )
    except AuthorizationError as exc:
        # Caught like `_execute_memory` catches it. Anything else escapes as a
        # generic ToolError, which an agent reads as a broken server and answers
        # by retrying -- the opposite of what a refusal should do.
        return _refusal("forbidden", str(exc))
    except RuntimeError as exc:
        # `_caller()` resolves the credential on entry and wraps anything that
        # goes wrong as RuntimeError, keeping the real error as __cause__. Unwrap
        # only an authorization failure and let the rest through unchanged:
        # a blanket RuntimeError catch here would turn a bug into a refusal.
        if isinstance(exc.__cause__, AuthorizationError):
            return _refusal("forbidden", str(exc.__cause__))
        raise
    except RememberError as exc:
        return _refusal("invalid_evidence", str(exc))
    except MemoryClientError as exc:
        return _refusal(getattr(exc, "code", "remember_failed"), str(exc))
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(_result_dict(written), indent=2))]
    )


def _checked_file(statement: str, file: str | None, require_permitted) -> str | None:
    """Resolve `file` against the ingest roots and the exact-quote rule.

    Returns the resolved path, or None when there is no file to check. Raises
    rather than returning a reason: the caller builds the taxonomy entry, and the
    reason must not name the allowed roots.
    """
    from api.services.remember import RememberError

    if file is None:
        return None
    resolved = require_permitted(file)
    # `remember(file=...)` on its own treats the file as the statement. When a
    # statement is also supplied, the two are separate things and the claim's
    # evidence has to be found in the document -- otherwise the caller is asking
    # for a quote the file does not contain.
    if statement:
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise RememberError(f"cannot read the file given to memory_remember: {exc}") from exc
        if statement not in content:
            raise RememberError(
                "statement is not an exact substring of the file given to "
                "memory_remember. Copy the sentence out of the file character for "
                "character -- paraphrasing, re-wrapping, smart quotes and em dashes "
                "are all rejected."
            )
    return str(resolved)


def _refusal(code: str, message: str) -> CallToolResult:
    """A refusal the agent can read and act on, not a transport error.

    The text carries the reason and never the statement: this runs on a path an
    attacker can influence, and an error message is the easiest place for memory
    content to leak back into a transcript.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"code": code, "message": message}))],
        isError=True,
    )


def _result_dict(written) -> dict:
    result = {
        "corpus": written.corpus,
        "version_id": written.version_id,
        "event": written.event,
        "path": written.path,
        "key": written.key,
        "unchanged": written.unchanged,
    }
    # Only when there is something to say. An always-present empty list reads as
    # "there were candidates and none matched", which is a different claim.
    if written.related_active_claims:
        result["related_active_claims"] = written.related_active_claims
    # An agent can grade its own call, which is the only way to find out whether
    # `ambiguous` was useful rather than decorative.
    if written.query_id:
        result["query_id"] = written.query_id
    return result


@mcp.tool()
def list_corpora() -> str:
    """List available corpora with document counts."""
    from api.services import corpora as corpora_svc
    from api.services.db import session_scope

    async def _list():
        async with session_scope() as db:
            return await corpora_svc.list_corpora(db)

    import asyncio

    with _caller():
        items = asyncio.run(_list())
    return json.dumps(items, indent=2, default=str)


# Advertise the central response schema while allowing MCP-native error results.
#
# `MemoryWire`, the same shape REST declares, so an agent reading the OpenAPI or
# the tool schema sees one response contract across both surfaces. It differs from
# `MemoryResponse` only in carrying the retrieval diagnostics, which `model_dump`
# omits from the digest -- and `structuredContent` is built from `public_dict`,
# which restores them.
MemoryToolResult = Annotated[CallToolResult, MemoryWire]
_READ_MEMORY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


#: Operations that create state, and so need the read-write role. Mirrors
#: `api.routers.memory.WRITE_OPERATIONS`; both surfaces decide the same way.
WRITE_OPERATIONS = frozenset({"snapshot"})


async def _execute_memory(operation: str, request: MemoryRequest) -> CallToolResult:
    from api.services.authz import AuthorizationError, require_corpus
    from api.services.db import current_principal, principal_scope
    from api.services.memory_public import MemoryError, execute

    try:
        # Resolved on this loop rather than on a private one: these tools are
        # awaited, so `asyncio.run` would refuse.
        principal = await _resolved_principal()
        with principal_scope(principal):
            require_corpus(current_principal(), request.corpus, write=operation in WRITE_OPERATIONS)
            response = await execute(operation, request)
    except (MemoryError, AuthorizationError) as exc:
        detail = {"code": exc.code, "message": exc.message}
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=json.dumps({"detail": detail}))],
            structuredContent={"detail": detail},
        )
    # `public_dict`, not `model_dump`: the digest omits the retrieval diagnostics on
    # purpose, and both blocks here are what an agent reads. A client that ignores
    # `structuredContent` and parses the text -- which is most of them -- would
    # otherwise see rank 1 with nothing saying it was a near-tie.
    payload = response.public_dict()
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
        structuredContent=payload,
    )


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_recall(request: MemoryRequest) -> MemoryToolResult:
    """Answer a question from memory.

    Returns the current authoritative memory for the question, each with its
    claim text, source document and version, and status. This is the tool to reach
    for first.

    It never invents an answer. If the corpus does not cover the question, the
    result is empty and `constraints` says so.

    For "why should I believe that?", use `memory_explain`.
    """
    return await _execute_memory("query", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_query(request: MemoryRequest) -> MemoryToolResult:
    """Answer a question from memory. The v0.8.0 name for `memory_recall`.

    Kept so an agent configured against v0.8.0 keeps working. It calls the same
    service with the same schema and returns the same object, so the two names
    cannot drift; new configurations should prefer `memory_recall`.
    """
    return await _execute_memory("query", request)


@mcp.tool(annotations=_READ_MEMORY, structured_output=True)
async def memory_explain(request: MemoryRequest) -> MemoryToolResult:
    """Answer "why should I trust this memory?" with authoritative provenance.

    Returns the same result as `memory_recall` -- same authoritative memory, same
    evidence, same temporal state, same conflicts -- plus the Memory Receipt,
    which names the claim identity, its source version and path, its evidence with
    offsets, its validity window, its supersession lineage and the authoritative
    digest. The receipt can be exported and verified offline with no server,
    database or model.

    This explains the recorded provenance and temporal basis for a memory. It does
    not expose model reasoning, and it does not establish that the original source
    was factually correct. Integrity and provenance are verified; authenticity is
    only established when the caller supplies a trust anchor it pinned itself.
    """
    explained = request.model_copy(update={"include_receipt": True})
    return await _execute_memory("query", explained)


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


def _load_artifact(value):
    """Load a receipt/pack from a dict, JSON string, or file path."""
    import json
    from pathlib import Path

    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        p = Path(value)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
)
async def memory_revalidate(
    corpus: str | None = None,
    receipt: str | dict | None = None,
    pack: str | dict | None = None,
    claim_ids: list[str] | None = None,
    valid_at: str | None = None,
    include_drift: bool = True,
) -> CallToolResult:
    """Revalidate a Receipt, Memory Pack, or claim ids against the live corpus.

    Returns overall status (CURRENT/STALE), per-claim freshness, and drift summary.
    """
    from api.services.authz import AuthorizationError, require_corpus
    from api.services.db import current_principal, principal_scope, session_scope
    from api.services.revalidate import revalidate_claims, revalidate_pack, revalidate_receipt

    receipt_data = _load_artifact(receipt)
    pack_data = _load_artifact(pack)

    # resolve corpus
    if receipt_data is not None:
        resolved_corpus = (
            (receipt_data.get("response") or {}).get("corpus")
            if isinstance(receipt_data, dict)
            else None
        )
        resolved_corpus = resolved_corpus or corpus
    elif pack_data is not None:
        resolved_corpus = pack_data.get("corpus") if isinstance(pack_data, dict) else None
        resolved_corpus = resolved_corpus or corpus
    else:
        resolved_corpus = corpus

    if not resolved_corpus:
        return _refusal("invalid_request", "corpus is required")

    try:
        principal = await _resolved_principal()
    except Exception as exc:  # noqa: BLE001
        return _refusal("forbidden", str(exc))

    with principal_scope(principal):
        try:
            require_corpus(current_principal(), resolved_corpus, write=False)
        except AuthorizationError as exc:
            return _refusal("forbidden", str(exc))

        try:
            async with session_scope() as db:
                if receipt_data is not None:
                    result = await revalidate_receipt(db, receipt_data, include_drift=include_drift)
                elif pack_data is not None:
                    result = await revalidate_pack(db, pack_data, include_drift=include_drift)
                else:
                    if not claim_ids:
                        return _refusal("invalid_request", "claim_ids is required")
                    result = await revalidate_claims(
                        db,
                        resolved_corpus,
                        list(claim_ids),
                        include_drift=include_drift,
                        valid_at=valid_at,
                    )
        except ValueError as exc:
            return _refusal("invalid_request", str(exc))
        except Exception as exc:  # noqa: BLE001
            return _refusal("revalidate_failed", str(exc))

    verdict = result.get("verdict") or result.get("status") or "STALE"
    if verdict not in ("CURRENT", "STALE"):
        verdict = "STALE" if verdict != "CURRENT" else "CURRENT"
    result["verdict"] = verdict
    result["status"] = verdict
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    )


MCP_USAGE = """\
mindpalace-mcp -- Mind Palace over the Model Context Protocol

Speaks JSON-RPC 2.0 on stdin/stdout. Start it from an MCP client config, not by
typing at it: it reads requests from stdin and writes replies to stdout, so a
terminal invocation reads EOF and exits immediately without printing anything.

Usage:
  mindpalace-mcp                 serve on stdin/stdout (the normal case)
  mindpalace-mcp --help          this text
  mindpalace-mcp --version       print the server version

Environment:
  MIND_PALACE_MCP_CREDENTIAL  required in `required` auth mode. The raw secret
      from `mindpalace credential issue <name> -c <corpus> -r read_write`. It
      must be a credential that can write to the corpus, or `memory_remember`
      and `sync` are refused. A read-only credential can still use the read
      tools. This is an environment variable rather than a tool argument on
      purpose: a caller that could pass its own credential would need no
      authorization at all.
  DATABASE_URL                 PostgreSQL with pgvector. Required.
  MIND_PALACE_INGEST_ROOTS     directories `sync` and `memory_remember(file=)`
      may read. `none` disables both.

Client configuration (stdio):
  { "command": "mindpalace-mcp",
    "env": { "DATABASE_URL": "postgresql://mpadmin:secret@localhost:5432/mindpalace",
             "MIND_PALACE_MCP_CREDENTIAL": "mpk_...",
             "MIND_PALACE_INGEST_ROOTS": "/srv/content" } }

To check it is alive without a client:
  printf '%s\\n' '{"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2024-11-05","capabilities":{},
              "clientInfo":{"name":"probe","version":"0"}}}' | mindpalace-mcp
"""


def main() -> None:
    import asyncio
    import sys

    # `--help` before anything else: the server's normal mode consumes stdin, so
    # a flag has to be handled here or not at all. It used to print nothing, and
    # a reader who tried it concluded the executable was broken.
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        sys.stdout.write(MCP_USAGE)
        return
    if "--version" in sys.argv[1:]:
        from importlib.metadata import PackageNotFoundError, version

        try:
            found = version("mindpalace-os")
        except PackageNotFoundError:
            found = "unknown (running from a source tree, not an install)"
        sys.stdout.write(f"mindpalace-mcp {found}\n")
        return

    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
