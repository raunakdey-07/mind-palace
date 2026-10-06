"""M015.1: REST, SDK and MCP must return the SAME answer and the SAME receipt.

One product contract, three developer surfaces. If they can disagree, the product
claim is unfalsifiable, so this is a permanent test rather than a convenience.

Each surface is driven the way a developer drives it -- REST over HTTP, the SDK
through its own client, MCP through the server's tool -- using the repository's
existing `interfaces` fixture, which keeps every surface inside the test-owned
transaction via a savepoint.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

try:
    from tests import test_memory_core as core
    from tests import test_memory_public as public
except ImportError:  # pragma: no cover - import-shape fallback
    import test_memory_core as core
    import test_memory_public as public

memory_db = core.memory_db
public_db = public.public_db
payload = core.payload
record = core.record
interfaces = public.interfaces

WHEN = datetime(2026, 1, 15, tzinfo=timezone.utc)
#: The fixture authors "Decision is <value>." under key "architecture.datastore",
#: so a question about the datastore retrieves. Note that "decision" itself does
#: NOT: it is a stop word in the relevance policy, so a question built from it
#: abstains. These tests are about receipts, not recall, so the question is chosen
#: to retrieve -- otherwise every assertion below would pass vacuously against an
#: empty answer.
QUESTION = "what is the current datastore"


@pytest.fixture
async def store(public_db):
    """A corpus with a real supersession chain, plus the embedding cache table.

    The shared fixture builds only migration 004, so without the cache table the
    query path degrades and leaves the transaction aborted.
    """
    await public.authored(public_db, "db.md", "MySQL", key="architecture.datastore")
    await public.authored(public_db, "db.md", "PostgreSQL", key="architecture.datastore")
    await public.authored(public_db, "notes.md", "unrelated note", key="notes.other")
    await public_db.db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS memory_claim_embeddings ("
            " corpus_id CHAR(64) NOT NULL, claim_id CHAR(64) NOT NULL,"
            " representation_hash CHAR(64) NOT NULL, embedding_model TEXT NOT NULL,"
            " embedding_dimension INTEGER NOT NULL, embedding_version INTEGER NOT NULL,"
            " embedding public.vector NOT NULL, PRIMARY KEY (corpus_id, claim_id))"
        )
    )
    return public_db


async def datastore_claim(store):
    """The authoritative `architecture.datastore` claim and when it was written."""
    current = [
        c
        for c in (await public.call(store, "current")).current_memories
        if c.key == "architecture.datastore"
    ]
    assert len(current) == 1, "fixture must leave exactly one current datastore claim"
    return current[0]


def _request(store, **fields):
    from api.models.memory import MemoryRequest

    return MemoryRequest(corpus=store.name, query=QUESTION, valid_at=WHEN, **fields)


async def test_rest_sdk_and_mcp_agree_without_a_receipt(store, interfaces):
    from api.models.memory import MemoryResponse

    request = _request(store)
    fields = request.model_dump(mode="json", exclude_unset=True, exclude={"corpus"})

    direct = await public.call(store, "query", **fields)
    assert direct.current_memories, "the question must retrieve, or nothing below is proven"

    rest = await interfaces.rest.post("/api/memory/query", json=request.model_dump(mode="json"))
    assert rest.status_code == 200, rest.text
    rest_response = MemoryResponse.model_validate(rest.json())

    sdk_result = await interfaces.sdk("query", request)

    tool = await interfaces.mcp.call_tool(
        "memory_query", {"request": request.model_dump(mode="json")}
    )
    assert not tool.is_error, tool
    mcp_response = MemoryResponse.model_validate(tool.structured_content)

    assert direct.model_dump() == rest_response.model_dump()
    assert direct.model_dump() == sdk_result.model_dump()
    assert direct.model_dump() == mcp_response.model_dump()
    assert direct.receipt is None
    # Backwards compatibility, asserted on the wire: a client that never asked for a
    # receipt must not find a new key in the payload.
    assert "receipt" not in rest.json()


async def test_rest_sdk_and_mcp_agree_on_a_receipt(store, interfaces):
    from api.models.memory import MemoryResponse

    request = _request(store, include_receipt=True)
    fields = request.model_dump(mode="json", exclude_unset=True, exclude={"corpus"})

    direct = await public.call(store, "query", **fields)
    assert direct.receipt, "service issued no receipt"

    rest = await interfaces.rest.post("/api/memory/query", json=request.model_dump(mode="json"))
    assert rest.status_code == 200, rest.text
    rest_response = MemoryResponse.model_validate(rest.json())

    sdk_result = await interfaces.sdk("query", request)

    tool = await interfaces.mcp.call_tool(
        "memory_query", {"request": request.model_dump(mode="json")}
    )
    assert not tool.is_error, tool
    mcp_response = MemoryResponse.model_validate(tool.structured_content)

    # The receipt is the SAME object everywhere, not merely a similar one.
    assert direct.receipt == rest_response.receipt == sdk_result.receipt == mcp_response.receipt
    assert rest_response.canonical_json() == direct.canonical_json()
    assert mcp_response.canonical_json() == direct.canonical_json()
    # And the answer itself is untouched by asking for proof.
    assert {c.id for c in sdk_result.current_memories} == {c.id for c in direct.current_memories}


async def test_mcp_explain_returns_provenance_not_reasoning(store, interfaces):
    from api.models.memory import MemoryResponse

    request = _request(store)
    tool = await interfaces.mcp.call_tool(
        "memory_explain", {"request": request.model_dump(mode="json")}
    )
    assert not tool.is_error, tool
    mcp = MemoryResponse.model_validate(tool.structured_content)

    assert mcp.receipt, "explain must carry a receipt even when not requested"
    receipt = mcp.receipt["receipts"][0]
    assert receipt["answer"]["claim_id"]
    assert receipt["answer"]["claim_key"] == "architecture.datastore"
    assert receipt["source"]["path"] == "db.md"
    assert receipt["evidence"]
    assert receipt["lineage"]["supersedes"], "the current claim supersedes the first"
    # The queried instant is on the receipt's state; validity on its temporal block.
    assert receipt["state"]["valid_at"]
    assert "valid_from" in receipt["temporal"]
    assert receipt["temporal"]["in_force_at_valid_at"] is True
    # The receipt is bound to the question that produced it.
    assert receipt["query"] == QUESTION
    # Provenance, never model reasoning or internal scoring traces.
    blob = str(mcp.model_dump()).casefold()
    for forbidden in ("chain_of_thought", "internal prompt", "system prompt"):
        assert forbidden not in blob


async def test_mcp_explain_reconstructs_the_state_that_was_recorded(store, interfaces):
    """The temporal capability: the same question, answered as it was answered then.

    `as_of` reconstructs the archive, so a question asked between the two writes is
    answered with the claim that was authoritative at that instant -- MySQL -- and
    the receipt it carries says so, naming that historical claim rather than
    today's. Asking with no cutoff gets the successor, whose receipt records the
    supersession. Both verify against the artifact they describe.
    """
    from api.models.memory import MemoryRequest, MemoryResponse
    from memory_receipt import verify_response_receipt

    history = await public.call(store, "history")
    superseded = [
        c
        for c in history.historical_memories
        if c.key == "architecture.datastore" and c.status == "SUPERSEDED"
    ]
    assert superseded, "fixture must record a supersession chain"
    # The instant the FIRST version was recorded: "what did we know, then?".
    then = superseded[0].observed_at

    def explain(**fields):
        return MemoryRequest(corpus=store.name, query=QUESTION, **fields)

    historical = await interfaces.mcp.call_tool(
        "memory_explain", {"request": explain(as_of=then).model_dump(mode="json")}
    )
    assert not historical.is_error, historical
    before = MemoryResponse.model_validate(historical.structured_content)

    present = await interfaces.mcp.call_tool(
        "memory_explain", {"request": explain().model_dump(mode="json")}
    )
    assert not present.is_error, present
    now = MemoryResponse.model_validate(present.structured_content)

    def answered(response):
        assert len(response.current_memories) == 1
        return response.current_memories[0]

    old_claim, new_claim = answered(before), answered(now)
    assert "MySQL" in old_claim.claim, old_claim.claim
    assert "PostgreSQL" in new_claim.claim, new_claim.claim
    assert old_claim.id != new_claim.id

    # The historical receipt describes the historical claim, and says which instant
    # it is a statement about. That is the whole point of the temporal surface.
    before_receipt = before.receipt["receipts"][0]
    after_receipt = now.receipt["receipts"][0]
    assert before_receipt["answer"]["claim_id"] == old_claim.id
    assert before_receipt["state"]["as_of"] == then.isoformat()
    assert after_receipt["answer"]["claim_id"] == new_claim.id
    assert after_receipt["state"]["as_of"] is None
    assert old_claim.id in after_receipt["lineage"]["supersedes"]

    # Each receipt verifies against the authoritative state it was cut from.
    for response in (before, now):
        result = verify_response_receipt(response.receipt, response.model_dump(mode="json"))
        assert result["verified"], json.dumps(result, default=str)


async def test_mcp_reports_service_errors_without_leaking_internals(store, interfaces):
    from api.models.memory import MemoryRequest

    request = MemoryRequest(corpus="no-such-corpus-xyz", query=QUESTION, valid_at=WHEN)
    tool = await interfaces.mcp.call_tool(
        "memory_explain", {"request": request.model_dump(mode="json")}
    )
    assert tool.is_error
    detail = tool.structured_content["detail"]
    assert set(detail) == {"code", "message"}
    assert "Traceback" not in tool.content[0].text
    assert "SELECT" not in tool.content[0].text
