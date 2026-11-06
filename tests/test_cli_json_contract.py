# SPDX-License-Identifier: Apache-2.0
"""The machine-readable CLI contract, asserted rather than described.

Three commands print JSON, and a script depends on the exact shape of all three.
The v0.9.0 changelog claims `explain --json` emits the same `{response, receipt}`
bundle `mindpalace receipt` writes; before this file nothing held that claim. Two
tests in `test_api.py` were found during the v0.9.0 release audit passing for the
wrong reason -- they asserted on a substring that a different error path also
contained -- so an unasserted contract here is not a theoretical risk.

What is pinned:

* `explain --json` is the two-key bundle, always, and carries the receipt whenever
  the response carries one.
* That receipt verifies against the response it ships with, so the bundle is not
  merely well-shaped but internally consistent.
* `recall --json` and `history --json` return the bare public response, *not* the
  bundle. They are different documents and a script should not have to probe for
  which one it got.
* No internal or debug field leaks into any of them.
"""

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") or os.getenv("MEMORY_TEST_DATABASE_URL")),
    reason="needs a real PostgreSQL to read real memory",
)

#: The public response contract, as it appears on the wire. Pinned as a literal set so
#: a new field fails here rather than being silently added to every script that
#: consumes this JSON.
#:
#: Note what is absent: `receipt`. `canonical_json()` excludes it on purpose, because
#: the receipt's digest is computed over exactly this document and embedding it would
#: be circular. That exclusion is the reason `explain --json` has to ship the two
#: documents as a bundle rather than merging them.
PUBLIC_RESPONSE_FIELDS = {
    "budget_unit",
    "changes",
    "conflicts",
    "constraints",
    "corpus",
    "current_memories",
    "evidence",
    "historical_memories",
    "query",
    "schema_version",
    "snapshot",
    "sources",
    "state",
    "truncated",
    "uncertain_memories",
}

#: Nothing here is for a consumer. If one of these appears, a debug field leaked.
FORBIDDEN_IN_PUBLIC_JSON = {
    "debug",
    "embedding",
    "embeddings",
    "fresh",
    "internal",
    "model",
    "trace",
    "vector",
    "vectors",
}


@pytest.fixture(scope="module")
def seeded_corpus():
    """A corpus with one current claim, so every command below has something to say."""
    import asyncio

    from api.services import remember as remember_service
    from api.services.corpora import get_or_create_corpus
    from api.services.db import async_engine, session_scope

    name = f"m016-json-{os.getpid()}"

    async def build():
        async with session_scope() as db:
            row = await get_or_create_corpus(db, name)
        await remember_service.remember(
            "Backups run nightly at 02:00 UTC and are retained for 35 days.",
            corpus=name,
            corpus_id=row["id"],
            key="ops.backups",
        )
        await async_engine.dispose()

    asyncio.run(build())
    return name


def run(argv: list[str]) -> dict:
    """Invoke the CLI and return the parsed JSON, failing loudly on anything else."""
    from cli.main import app

    result = CliRunner().invoke(app, argv)
    assert (
        result.exit_code == 0
    ), f"{argv} exited {result.exit_code}\n{result.output}\n{result.stderr}"
    return json.loads(result.stdout)


def test_explain_json_is_the_response_and_receipt_bundle(seeded_corpus):
    """Two keys, always. And the receipt comes along.

    `canonical_json()` omits the receipt on purpose -- its digest is computed over
    that JSON, so embedding it would be circular. A script asking `explain --json`
    for the basis of an answer still wants the digest, so the CLI emits the bundle
    `mindpalace receipt` writes rather than dropping it and going quietly less
    useful than the human-readable form.
    """
    payload = run(["explain", "When do we run backups?", "--corpus", seeded_corpus, "--json"])

    assert sorted(payload) == ["receipt", "response"], sorted(payload)

    response = payload["response"]
    assert set(response) == PUBLIC_RESPONSE_FIELDS, sorted(set(response) ^ PUBLIC_RESPONSE_FIELDS)
    # Asserted because it is the reason the bundle exists at all: the receipt's digest
    # is taken over exactly this document, so the receipt cannot live inside it.
    assert "receipt" not in response

    receipt = payload["receipt"]
    assert receipt["receipt_count"] >= 1, receipt
    assert receipt["receipts"], "a receipt bundle with no receipts attests to nothing"


def test_the_explain_bundle_verifies_against_its_own_response(seeded_corpus):
    """Well-shaped is not the same as correct. Verify it the way a recipient would.

    Offline, with no database and no model, because that is the entire point of
    exporting a receipt: someone who has never heard of Mind Palace must be able to
    check it.
    """
    from memory_receipt import verify_response_receipt

    payload = run(["explain", "When do we run backups?", "--corpus", seeded_corpus, "--json"])
    checked = verify_response_receipt(payload["receipt"], payload["response"])

    assert checked["verified"] is True, checked
    assert checked["receipts_checked"] >= 1, checked
    assert checked["failures"] == [], checked

    # And a rewritten answer must be rejected by the same reader, so the bundle is
    # not merely self-consistent but actually load-bearing.
    forged = json.loads(json.dumps(payload))
    forged["response"]["current_memories"][0]["claim"] = "Backups never run."
    assert verify_response_receipt(forged["receipt"], forged["response"])["verified"] is False


def test_recall_and_history_json_are_the_bare_response(seeded_corpus):
    """Not the bundle. They are different documents and a script should not probe.

    Only `explain --json` wraps. `recall --json` and `history --json` return the
    public response directly, so a consumer reads `state` and `current_memories` at
    the top level rather than one level down behind a `response` key.
    """
    for argv in (
        ["recall", "When do we run backups?", "--corpus", seeded_corpus, "--json"],
        ["history", "ops.backups", "--corpus", seeded_corpus, "--json"],
    ):
        payload = run(argv)
        assert (
            set(payload) == PUBLIC_RESPONSE_FIELDS
        ), f"{argv[0]} --json changed shape: {sorted(set(payload) ^ PUBLIC_RESPONSE_FIELDS)}"


def test_no_internal_field_leaks_into_any_public_json(seeded_corpus):
    """A debug field reaching a script is a compatibility promise nobody agreed to.

    Cheaper to catch here than to unpickle in someone's pipeline later.
    """
    documents = [
        run(["recall", "When do we run backups?", "--corpus", seeded_corpus, "--json"]),
        run(["explain", "When do we run backups?", "--corpus", seeded_corpus, "--json"])[
            "response"
        ],
        run(["history", "ops.backups", "--corpus", seeded_corpus, "--json"]),
    ]
    for document in documents:
        leaked = FORBIDDEN_IN_PUBLIC_JSON & set(document)
        assert not leaked, f"internal fields in public JSON: {sorted(leaked)}"


def test_json_output_is_deterministic(seeded_corpus):
    """Same corpus, same question, same bytes -- with `valid_at` pinned.

    `state.valid_at` is "now", so two calls milliseconds apart legitimately differ.
    Everything else must not, because a script that diffs two responses should see
    only the answer change.
    """
    first = run(["recall", "When do we run backups?", "--corpus", seeded_corpus, "--json"])
    second = run(["recall", "When do we run backups?", "--corpus", seeded_corpus, "--json"])

    volatile = {"state"}
    a = {k: v for k, v in first.items() if k not in volatile}
    b = {k: v for k, v in second.items() if k not in volatile}
    assert a == b, "a repeated recall changed something other than the clock"

    # And with the clock pinned, it is byte-identical.
    first["state"]["valid_at"] = second["state"]["valid_at"] = "2026-01-01T00:00:00+00:00"
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
