"""The exact-quote rejection must tell the author what to do, and leak nothing.

Two properties, and the second one is the reason the first is hard.

**Actionable.** `api/services/memory.py` rejects an authored claim whose
`evidence` is not an exact substring of an archived chunk, or whose `claim`
text is not present in the chunk its evidence matched. Before this session the
messages were:

    claim[0] evidence must be an exact substring of an archived chunk
    claim[0] text must appear in the supporting chunk; semantic inference is not supported

Both are true and neither tells an author what to do. Measured on 2026-10-14
while building the ingestion inventory: **two of three drafted documents were
rejected**, and the second rejection was the one nobody could act on, because
the rule has two halves and the message named only one.

**Leak-free.** `ClaimValidationError` exists so a rejection is identifiable
without putting a customer's prose in a log -- the same reasoning as
`unexpected_error` in `api/main.py`. So the message may carry structure (a claim
index, a count, an instruction) and must not carry content: not the offending
quote, not the chunk text, not the claim text, not the claim key.

An error message is a log line. "Helpful" and "leaks the document into the log"
are the same edit if nobody is careful, so this file checks both.
"""

from __future__ import annotations

import pytest

from api.services import memory

CHUNKS = [
    {"text": "The checkout service uses PostgreSQL. We moved it in March.", "order_index": 0},
    {"text": "Checkout retries a failed payment twice before giving up.", "order_index": 1},
]

CLAIMS = [
    {
        "key": "checkout.datastore",
        "value": "PostgreSQL",
        "claim": "The checkout service uses PostgreSQL.",
        "evidence": "The checkout service uses PostgreSQL.",
    }
]


def _prepare(claims, chunks=CHUNKS):
    metadata = {
        "title": "Checkout",
        "document_type": "decision",
        "claims": claims,
    }
    return memory._prepare(metadata, list(chunks))


def _reason(claims, chunks=CHUNKS) -> str:
    with pytest.raises(ValueError) as caught:
        _prepare(claims, chunks)
    return str(caught.value)


# ----------------------------------------------------------------- actionable


def test_a_paraphrased_quote_says_how_to_fix_it():
    reason = _reason([{**CLAIMS[0], "evidence": "Checkout runs on PostgreSQL."}])
    assert "exact substring" in reason
    # The instruction, not just the prohibition.
    assert "character for character" in reason
    # The common near-misses named, because those are what people actually try.
    for word in ("Paraphras", "re-wrap", "smart quote"):
        assert word.lower() in reason.lower(), word
    # What was searched, so "not found" is distinguishable from "searched nothing".
    assert f"searched {len(CHUNKS)} chunk(s)" in reason


def test_a_claim_that_paraphrases_the_document_says_so():
    """The rule has two halves and the second half needs its own message."""
    reason = _reason([{**CLAIMS[0], "claim": "Checkout is backed by PostgreSQL."}])
    assert "semantic inference is not supported" in reason
    assert "the supporting chunk that its evidence matched" in reason
    assert "the document's own wording" in reason


def test_the_two_rejections_are_distinguishable():
    """An author hitting both halves must be able to tell which one they hit."""
    quote_bad = _reason([{**CLAIMS[0], "evidence": "nope"}])
    claim_bad = _reason([{**CLAIMS[0], "claim": "nope"}])
    assert quote_bad != claim_bad
    assert "exact substring" in quote_bad and "exact substring" not in claim_bad


def test_the_index_is_still_reported():
    """Which claim in the document is at fault, when a document authors several."""
    second = {**CLAIMS[0], "key": "checkout.retries", "evidence": "not in the document"}
    reason = _reason([CLAIMS[0], second])
    assert "claim[1]" in reason


# ------------------------------------------------------------------ leak-free


def test_the_message_carries_no_document_content():
    """The whole point of `ClaimValidationError`.

    Everything below is text an author wrote and expects not to end up in a
    log. None of it may appear in the message.
    """
    secret_quote = "The internal runbook says rotate keys quarterly"
    secret_claim = "Key rotation happens quarterly"
    secret_key = "ops.internal.runbook.secret"
    chunks = [{"text": secret_quote, "order_index": 0}]

    reason = _reason(
        [{**CLAIMS[0], "key": secret_key, "claim": secret_claim, "evidence": "rotate keys"}],
        chunks,
    )
    assert secret_quote not in reason
    assert secret_claim not in reason
    assert secret_key not in reason


def test_the_error_type_still_carries_the_identifiers():
    """It is identifiable without being legible: path and fingerprint survive."""
    with pytest.raises(memory.ClaimValidationError) as caught:
        memory.validate_source(
            "docs/x.md", "body text", {"claims": [{**CLAIMS[0], "evidence": "not in it"}]}, CHUNKS
        )
    error = caught.value
    assert error.path == "docs/x.md"
    assert len(error.version_id) == 64
    assert "docs/x.md" in str(error)
