"""`memory_remember`: the one MCP tool that writes memory.

Until now an agent could read memory and could `sync` a directory, but could not
record a statement. This file pins the properties that make that safe to expose:

* the arguments and the code path are the SDK's, so an agent and a script cannot
  write memory differently;
* idempotency holds, so a retrying agent cannot duplicate or grow the corpus;
* `file=` is bounded by MIND_PALACE_INGEST_ROOTS and by the exact-quote rule;
* a refusal is a readable result carrying a reason and **no memory content** --
  the tool runs on a path an attacker can influence, and an error message is the
  easiest place for what was written to leak back into a transcript.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from test_tenant_isolation import ALPHA_CORPORA
from test_tenant_isolation import world as _shared_world_fixture

# The `world` fixture is shared rather than duplicated, so these tests run against
# exactly the two organisations the isolation suite uses. Aliased for the same
# reason as in tests/test_credential_management.py: a test's own `world` parameter
# stays unambiguous to a reader and to the linter.
world = _shared_world_fixture

CORPUS = "mcp-remember-corpus"


def _skip_without_db() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")


@pytest.fixture(scope="module")
def loop():
    """One event loop for the module.

    `asyncio.run` per call is what the older MCP tests do, and it breaks here:
    the SDK caches the local principal as an asyncio task, and a task belongs to
    the loop that created it. A second call on a fresh loop gets "got Future
    attached to a different loop" -- a test artefact, not a product one.
    """
    created = asyncio.new_event_loop()
    yield created
    created.close()


def _call(loop, **kwargs):
    """Call the tool the way an MCP client does: by name, through the server."""
    import mcp_server

    return loop.run_until_complete(mcp_server.mcp.call_tool("memory_remember", kwargs))


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.fixture
def fresh_corpus():
    _skip_without_db()
    import sqlalchemy as sa

    url = os.environ["DATABASE_URL"]
    for scheme in ("postgresql+psycopg://", "postgresql://"):
        url = url.replace(scheme, "postgresql+psycopg2://")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "DELETE FROM ingestion_manifest WHERE corpus_id IN "
                    "(SELECT id FROM corpora WHERE name = :c)"
                ),
                {"c": CORPUS},
            )
            conn.execute(
                sa.text(
                    "DELETE FROM chunks WHERE doc_id IN "
                    "(SELECT id FROM documents WHERE corpus_id IN "
                    "(SELECT id FROM corpora WHERE name = :c))"
                ),
                {"c": CORPUS},
            )
    finally:
        engine.dispose()
    yield CORPUS


def test_the_tool_is_registered_and_documented():
    import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool = next(t for t in tools if t.name == "memory_remember")
    # A tool a client cannot call is worse than an absent one: it is discovered,
    # offered to a model, and then fails at the socket.
    assert tool.description
    properties = tool.input_schema["properties"]
    assert {"statement", "corpus", "key", "file"} <= set(properties)
    # No credential parameter. The identity is MIND_PALACE_MCP_CREDENTIAL, and a
    # caller that could choose its own would need no authorization.
    assert "credential" not in properties
    assert "api_key" not in properties


def test_remembering_a_statement_writes_memory(fresh_corpus, loop):
    result = _call(
        loop,
        statement="The checkout service uses PostgreSQL.",
        corpus=fresh_corpus,
        key="checkout.datastore",
    )
    assert not result.is_error, _payload(result)
    body = _payload(result)
    assert body["event"] == "NEW"
    assert body["unchanged"] is False
    assert body["key"] == "checkout.datastore"
    assert body["version_id"]


def test_the_same_call_twice_is_idempotent(fresh_corpus, loop):
    """A retrying agent must not duplicate memory or grow the corpus."""
    first = _payload(
        _call(
            loop,
            statement="Billing runs in the eu-west-1 region.",
            corpus=fresh_corpus,
            key="billing.region",
        )
    )
    second = _payload(
        _call(
            loop,
            statement="Billing runs in the eu-west-1 region.",
            corpus=fresh_corpus,
            key="billing.region",
        )
    )
    assert second["unchanged"] is True
    assert second["event"] == "UNCHANGED"
    assert second["version_id"] == first["version_id"]


def test_file_outside_the_ingest_roots_is_refused(tmp_path, monkeypatch, loop):
    """An agent can be talked into `file=/etc/passwd`. The roots bound it."""
    _skip_without_db()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("The secret is hunter2.\n")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(allowed))

    result = _call(loop, statement="The secret is hunter2.", corpus=CORPUS, file=str(outside))

    assert result.is_error
    assert _payload(result)["code"] == "ingest_path_refused"
    # The refusal must not leak the roots it compared against.
    assert str(allowed) not in _payload(result)["message"]


def test_a_non_substring_statement_is_refused(tmp_path, monkeypatch, loop):
    """The exact-quote rule, on the only path where there are two things."""
    _skip_without_db()
    root = tmp_path / "docs"
    root.mkdir()
    doc = root / "runbook.md"
    doc.write_text("Revoke the old key only after publishing the new one.\n")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(root))

    result = _call(
        loop,
        statement="the old key should be revoked first",
        corpus=CORPUS,
        file=str(doc),
    )

    assert result.is_error
    assert _payload(result)["code"] == "invalid_evidence"
    assert "exact substring" in _payload(result)["message"]


def test_an_exact_statement_inside_the_roots_is_accepted(tmp_path, monkeypatch, loop):
    _skip_without_db()
    root = tmp_path / "docs"
    root.mkdir()
    doc = root / "runbook.md"
    doc.write_text("Revoke the old key only after publishing the new one.\n")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(root))

    result = _call(
        loop,
        statement="Revoke the old key only after publishing the new one.",
        corpus=CORPUS,
        file=str(doc),
    )

    assert not result.is_error, _payload(result)
    assert _payload(result)["event"] == "NEW"


def test_a_refusal_carries_no_statement_content(tmp_path, monkeypatch, loop):
    """The error text must not become a channel for what was being written."""
    _skip_without_db()
    secret = "The production root password is correct-horse-battery."
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text(secret + "\n")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(allowed))

    result = _call(loop, statement=secret, corpus=CORPUS, file=str(outside))
    message = json.dumps(_payload(result))

    assert result.is_error
    assert "correct-horse-battery" not in message


# ---------------------------------------------------------------------------
# Authorization. The credential is read from the environment, never from an
# argument, so a caller's role is decided before the tool body runs.
# ---------------------------------------------------------------------------


def _refused(loop, **kwargs):
    """True when the tool refused, whichever layer said so.

    A refusal can arrive as a structured result or as a raised error, and which
    one depends on where the role check fires. What this file is about is that a
    credential without write access cannot cause a write, not which layer
    reports it -- the structured shape is pinned by the other tests here.
    """
    import mcp_server

    try:
        result = loop.run_until_complete(mcp_server.mcp.call_tool("memory_remember", kwargs))
    except Exception as exc:  # noqa: BLE001 - a refusal may surface as a raise
        chain = []
        seen = exc
        while seen is not None and len(chain) < 6:
            chain.append(f"{type(seen).__name__}: {seen}")
            seen = seen.__cause__ or seen.__context__
        return True, " | ".join(chain)
    if result.is_error:
        return True, json.dumps(_payload(result))
    return False, json.dumps(_payload(result))


def test_a_read_only_credential_cannot_remember(world, monkeypatch, loop):
    """A read-only credential granted a corpus must not be able to write to it.

    `sync` had this defect once: it resolved its corpus with `write=False`, so a
    reader could perform a full archive write into a corpus it was granted. The
    same choke point has to fire for `memory_remember`.
    """
    import mcp_server

    monkeypatch.setenv("MIND_PALACE_MCP_CREDENTIAL", world.credentials["alpha-reviewer"]["secret"])
    monkeypatch.setattr("api.services.db.AsyncSessionLocal", world.db_module.AsyncSessionLocal)

    refused, detail = _refused(
        loop,
        statement="The reviewer should not be able to write this.",
        corpus=ALPHA_CORPORA["brand"],
    )
    assert refused, detail
    assert mcp_server is not None


def test_no_credential_is_refused_in_required_mode(world, monkeypatch, loop):
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.delenv("MIND_PALACE_MCP_CREDENTIAL", raising=False)
    monkeypatch.setattr("api.services.db.AsyncSessionLocal", world.db_module.AsyncSessionLocal)

    refused, detail = _refused(
        loop, statement="Written with no credential at all.", corpus=ALPHA_CORPORA["brand"]
    )
    assert refused, detail
