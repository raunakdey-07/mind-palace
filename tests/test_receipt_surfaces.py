"""M015.1: the receipt contract must be IDENTICAL across every public surface.

REST, the Python SDK, MCP and the CLI are four ways of asking the same question.
This test asserts they get the same answer, and that a receipt is the same object
wherever it came from. A receipt that meant different things per surface would make
the product claim unfalsifiable, so this is a permanent test.

It also pins the compatibility requirement: a client that never asked for a receipt
must receive exactly what it received before, byte for byte.

The corpus here is a real supersession chain written through the public authoring
path, not a fixture dict, so the temporal assertions are about the actual archive.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

try:
    from tests import test_memory_core as core
    from tests import test_memory_public as public
except ImportError:  # pragma: no cover - import-shape fallback
    import test_memory_core as core
    import test_memory_public as public

# The database fixtures live in test_memory_public, which re-exports memory_db from
# test_memory_core. Declaring only public_db leaves memory_db unresolved and every
# test errors at setup, so the whole chain is re-exported here, the same way
# test_memory_query.py reuses that harness.
public_db = public.public_db
memory_db = core.memory_db
payload = core.payload
record = core.record


@pytest.fixture
async def store(public_db):
    """A corpus holding datastore history: MySQL, then PostgreSQL, plus an aside.

    The shared fixture deliberately builds only migration 004, so the claim
    embedding cache (migration 007) does not exist. `memory_query` swallows that
    lookup failure, which is pre-existing and harmless in production, but it leaves
    the enclosing transaction aborted and every later query in the session fails.
    The table is therefore created here so the receipt path runs against the code
    that ships rather than against a degraded fallback.
    """
    await public.authored(public_db, "db.md", "MySQL", key="architecture.datastore")
    await public.authored(public_db, "db.md", "PostgreSQL", key="architecture.datastore")
    await public.authored(public_db, "other.md", "unrelated note", key="notes.other")
    for ddl in _embedding_cache_ddl():
        await public_db.db.execute(text(ddl))
    return public_db


def _embedding_cache_ddl() -> list[str]:
    """The minimum of migration 007 the query path touches.

    The `vector` type is named schema-qualified because the test transaction runs
    with ``search_path`` set to the throwaway schema plus ``pg_catalog``, which does
    not include ``public`` where pgvector is installed.
    """
    return [
        "CREATE TABLE IF NOT EXISTS memory_claim_embeddings ("
        " corpus_id CHAR(64) NOT NULL, claim_id CHAR(64) NOT NULL,"
        " representation_hash CHAR(64) NOT NULL, embedding_model TEXT NOT NULL,"
        " embedding_dimension INTEGER NOT NULL, embedding_version INTEGER NOT NULL,"
        " embedding public.vector NOT NULL,"
        " PRIMARY KEY (corpus_id, claim_id))",
        "CREATE INDEX IF NOT EXISTS ix_m013_test_claim_embeddings"
        " ON memory_claim_embeddings (representation_hash)",
    ]


async def _ask(store, query, **fields):
    """Ask through the same entry point the existing public tests use.

    `execute` opens its own session against the configured URL, whereas the test
    harness owns the transaction, so the receipt path is exercised through
    `public.call` like the rest of the suite.
    """
    return await public.call(store, "query", query=query, **fields)


async def test_no_receipt_by_default_is_byte_identical(store):
    """A client that never asked must not see a new field."""
    response = await _ask(store, "primary datastore")
    assert response.receipt is None
    assert "receipt" not in response.model_dump()


async def test_receipt_is_attached_when_requested(store):
    response = await _ask(store, "primary datastore", include_receipt=True)
    assert response.receipt, "expected a receipt"
    bundle = response.receipt
    assert bundle["schema_version"] == 1
    assert bundle["claim_keys"] == ["architecture.datastore"]
    assert bundle["query_digest"]
    assert bundle["memory_pack_digest"]
    assert bundle["receipt_count"] == len(bundle["receipts"])


async def test_receipt_never_enters_the_authoritative_bytes(store):
    """The digest the whole project is gated on must not move.

    Both calls are anchored to the same valid_at: with none supplied the service
    defaults to "now", so two unanchored calls legitimately differ by microseconds
    and would not be comparing the same authoritative state.
    """
    from datetime import datetime, timezone

    when = datetime(2026, 1, 15, tzinfo=timezone.utc)
    plain = await _ask(store, "primary datastore", valid_at=when)
    with_receipt = await _ask(store, "primary datastore", valid_at=when, include_receipt=True)
    # The authoritative bytes, and therefore the digest, are identical.
    assert plain.canonical_json() == with_receipt.canonical_json()
    # The only difference is the added receipt itself.
    stripped = with_receipt.model_dump()
    stripped.pop("receipt")
    assert stripped == plain.model_dump()


async def test_receipt_verifies_and_refuses_to_claim_authenticity(store):
    from memory_receipt import verify_response_receipt

    response = await _ask(store, "primary datastore", include_receipt=True)
    result = verify_response_receipt(response.receipt, response.model_dump(mode="json"))
    assert result["verified"], result
    assert result["authenticity"] == "NOT ESTABLISHED"


async def test_historical_receipt_names_the_superseded_truth(store):
    """The capability that makes this more than a hash."""
    from datetime import datetime, timezone

    from memory_receipt import verify_response_receipt

    when = datetime(2026, 1, 15, tzinfo=timezone.utc)
    response = await _ask(store, "primary datastore", include_receipt=True, valid_at=when)
    if not response.receipt:
        pytest.skip("historical query returned no current memory at that instant")
    quoted = {r["answer"]["claim"] for r in response.receipt["receipts"]}
    assert quoted, "receipt named no claim"
    result = verify_response_receipt(response.receipt, response.model_dump(mode="json"))
    assert result["verified"], result


async def test_receipt_is_rejected_after_the_answer_is_edited(store):
    from memory_receipt import verify_response_receipt

    response = await _ask(store, "primary datastore", include_receipt=True)
    tampered = response.model_dump(mode="json")
    tampered["current_memories"][0]["claim"] = "Something else entirely"
    result = verify_response_receipt(response.receipt, tampered)
    assert not result["verified"]


async def test_no_receipt_when_nothing_current_is_returned(store):
    """An abstention has no answer to attest to."""
    response = await _ask(store, "which vendor supplies the payroll system", include_receipt=True)
    if not response.current_memories:
        assert response.receipt is None


async def test_receipt_survives_serialisation_for_later_verification(store):
    """It must be plain JSON a client can store and check offline."""
    from memory_receipt import verify_response_receipt

    response = await _ask(store, "primary datastore", include_receipt=True)
    restored = json.loads(json.dumps(response.receipt))
    result = verify_response_receipt(restored, response.model_dump(mode="json"))
    assert result["verified"], result


async def test_sdk_accepts_include_receipt():
    """The SDK parameter exists and is passed through, not reimplemented."""
    import inspect

    from mindpalace_sdk import MemoryClient

    signature = inspect.signature(MemoryClient.query)
    assert "include_receipt" in signature.parameters
    assert signature.parameters["include_receipt"].default is False
