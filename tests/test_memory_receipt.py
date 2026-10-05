"""M014.2: the Memory Receipt, and the three properties it does and does not prove.

The receipt's job is to answer "what exactly did Mind Palace tell my application,
and can I check that later?". These tests pin the answer, including the cases where
verification must refuse, and the case that matters most for honesty: authenticity
is NOT established without a trust anchor that lives outside the artifact.
"""

from __future__ import annotations

import json

import pytest
from test_proof_cli import PACK_FIXTURE

from memory_pack import MemoryPack
from memory_proof import canonical_bytes
from memory_receipt import (
    ReceiptError,
    build_receipt,
    canonical_receipt_json,
    query_digest,
    receipt_digest,
    receipt_for_response,
    verify_receipt,
    verify_response_receipt,
    verify_trust,
)

PACK = MemoryPack.from_dict(PACK_FIXTURE)
KEY = "architecture.postgres"


@pytest.fixture
def receipt() -> dict:
    return build_receipt(PACK, KEY)


# --- construction ------------------------------------------------------------


def test_receipt_records_the_query_and_its_digest(receipt):
    assert receipt["query"] == PACK_FIXTURE["query"]
    assert receipt["query_digest"] == query_digest(receipt["query"])


def test_receipt_binds_to_a_claim_in_the_pack(receipt):
    assert receipt["answer"]["claim_id"] == "c-new"  # the CURRENT one
    assert receipt["answer"]["claim_key"] == KEY
    assert receipt["answer"]["claim"] == "The primary datastore is PostgreSQL."


def test_receipt_carries_evidence_provenance_and_lineage(receipt):
    assert [e["evidence_id"] for e in receipt["evidence"]] == ["e-new"]
    assert receipt["evidence"][0]["text"] == "The primary datastore is PostgreSQL."
    assert receipt["source"]["path"] == "docs/tech/postgres.md"
    assert receipt["lineage"]["supersedes"] == ["c-old"]


def test_receipt_is_bounded(receipt):
    """A receipt travels with an answer; it must stay small.

    It embeds the proof but references the pack by digest rather than embedding
    it, so it grows with the answer, not with the archive.
    """
    body = len(canonical_receipt_json(receipt).encode("utf-8"))
    assert body < 4096, f"receipt grew to {body} bytes"
    assert "current_memories" not in canonical_receipt_json(receipt)
    assert receipt["integrity"]["memory_pack_digest"] == PACK.digest()


def test_receipt_can_omit_the_embedded_proof():
    small = build_receipt(PACK, KEY, include_proof=False)
    assert "proof" not in small
    assert small["integrity"]["proof_digest"]
    result = verify_receipt(small, PACK)
    assert result["verified"], result
    assert result["proof_verified"] is None


# --- determinism -------------------------------------------------------------


def test_receipt_serialisation_is_canonical(receipt):
    reordered = {k: receipt[k] for k in sorted(receipt, reverse=True)}
    assert canonical_receipt_json(reordered) == canonical_receipt_json(receipt)
    assert receipt_digest(reordered) == receipt["receipt_id"]


def test_receipt_canonicalisation_matches_the_proof_contract(receipt):
    """One canonicalisation contract across pack, proof and receipt."""
    assert canonical_bytes(receipt) == canonical_receipt_json(receipt).encode("utf-8")


def test_receipts_are_reproducible():
    assert receipt_digest(build_receipt(PACK, KEY)) == receipt_digest(build_receipt(PACK, KEY))


# --- verification ------------------------------------------------------------


def test_a_matching_receipt_verifies(receipt):
    result = verify_receipt(receipt, PACK)
    assert result["verified"], result
    assert result["proof_verified"] is True


def test_receipt_accepts_raw_artifact_dicts(receipt):
    assert verify_receipt(receipt, PACK.raw)["verified"]


@pytest.mark.parametrize(
    "mutate,bucket",
    [
        (lambda p: p["answer"].update({"claim": "Something else"}), "provenance"),
        (lambda p: p["answer"].update({"claim_key": "architecture.other"}), "provenance"),
        (lambda p: p["answer"].update({"value": "MySQL"}), "provenance"),
        (lambda p: p["evidence"][0].update({"text": "Fabricated"}), "provenance"),
        (lambda p: p["source"].update({"path": "elsewhere.md"}), "provenance"),
        (lambda p: p.update({"query": "a different question"}), "integrity"),
        # Rewriting valid_at is caught as a TEMPORAL failure, not an integrity one:
        # the receipt now recomputes validity from the artifact, so an instant at
        # which the claim did not exist is exactly what it should object to.
        (lambda p: p["state"].update({"valid_at": "2024-01-01T00:00:00+00:00"}), "temporal"),
        (lambda p: p["lineage"].update({"superseded_by": ["c-fake"]}), "supersession"),
        (lambda p: p["integrity"].update({"memory_pack_digest": "0" * 64}), "integrity"),
    ],
)
def test_every_receipt_mutation_is_caught_in_the_right_bucket(receipt, mutate, bucket):
    """A refusal must name the property that failed, not just say INVALID."""
    mutated = json.loads(json.dumps(receipt))
    mutate(mutated)
    mutated["receipt_id"] = receipt_digest(mutated)
    result = verify_receipt(mutated, PACK)
    assert not result["verified"]
    assert result[bucket], f"expected a {bucket} failure, got {result}"


def test_tampering_the_receipt_without_reminting_is_caught(receipt):
    receipt["query"] = "something Mind Palace never said"
    result = verify_receipt(receipt, PACK)
    assert not result["verified"]
    assert any("query digest mismatch" in r for r in result["integrity"])


def test_wrong_pack_is_rejected(receipt):
    other = MemoryPack.from_dict({**PACK_FIXTURE, "corpus": "somewhere-else"})
    result = verify_receipt(receipt, other)
    assert not result["verified"]
    assert any("memory pack digest mismatch" in r for r in result["integrity"])


def test_unknown_receipt_version_is_an_error_not_a_verdict():
    with pytest.raises(ReceiptError):
        verify_receipt({"receipt_version": 99}, PACK)


# --- the trust model ---------------------------------------------------------


def test_authenticity_is_never_claimed_from_a_digest(receipt):
    """The point of this milestone: integrity is not authenticity."""
    result = verify_receipt(receipt, PACK)
    assert result["verified"] is True
    assert result["authenticity"] == "NOT ESTABLISHED"


def test_trust_requires_an_anchor_the_verifier_supplies(receipt):
    # No anchor: authenticity is not established, even though integrity passes.
    unpinned = verify_trust({}, "", PACK.digest())
    assert unpinned["authenticated"] is False
    assert "not established" in unpinned["reason"]

    # Anchor matches an artifact the verifier already trusts.
    anchored = verify_trust({}, PACK.digest(), PACK.digest())
    assert anchored["authenticated"] is True

    # Anchor disagrees: this is the attack a digest alone cannot catch.
    forged = verify_trust({}, "b" * 64, PACK.digest())
    assert forged["authenticated"] is False
    assert "does not match" in forged["reason"]


def test_a_rebuilt_artifact_forge_is_caught_only_by_a_trust_anchor(receipt):
    """An attacker who replaces BOTH artifact and proof defeats integrity.

    This is the honest demonstration of why authenticity needs an external anchor,
    and it is asserted rather than asserted-about: rebuild the artifact, rebuild the
    receipt over it, and show integrity now passes.
    """
    forged_raw = json.loads(json.dumps(PACK_FIXTURE))
    forged_raw["current_memories"][0]["value"] = "MySQL"
    forged = MemoryPack.from_dict(forged_raw)
    forged_receipt = build_receipt(forged, KEY)

    # Integrity and provenance pass: the receipt is internally consistent.
    result = verify_receipt(forged_receipt, forged)
    assert result["verified"], result
    assert result["authenticity"] == "NOT ESTABLISHED"

    # Only a pinned digest from outside the artifact catches it.
    assert verify_trust({}, PACK.digest(), forged.digest())["authenticated"] is False


# --- temporal ----------------------------------------------------------------


def test_receipt_reflects_the_supersession_chain(receipt):
    assert receipt["temporal"]["observed_at"] == "2025-06-01T00:00:00+00:00"
    assert receipt["temporal"]["in_force_at_valid_at"] is True


def test_historical_receipt_names_the_superseded_version():
    """The superseded truth, proven, from the same pack.

    At an earlier instant the archive recorded SQLite, so the receipt must name
    SQLite -- not the CURRENT PostgreSQL claim. Picking the current claim would
    attach a receipt for the wrong historical truth.
    """
    historical = build_receipt(
        PACK,
        KEY,
        query="What datastore was production using?",
        valid_at="2025-02-01T00:00:00+00:00",
    )
    assert verify_receipt(historical, PACK)["verified"]
    assert historical["answer"]["claim"] == "The primary datastore is SQLite."
    assert historical["answer"]["status"] == "SUPERSEDED"
    assert historical["temporal"]["in_force_at_valid_at"] is True
    assert historical["lineage"]["superseded_by"] == ["c-new"]


def test_receipts_are_portable_json(receipt):
    """Round-trips through a plain file with no runtime."""
    restored = json.loads(json.dumps(receipt))
    assert verify_receipt(restored, PACK)["verified"]


# --- the response-level bundle ----------------------------------------------


def _response_model():
    """A MemoryResponse shaped like the real one, built from the fixture pack."""
    from api.models.memory import MemoryResponse

    raw = PACK.raw
    return MemoryResponse.model_validate(
        {
            "query": raw["query"],
            "corpus": raw["corpus"],
            "state": raw["state"],
            "current_memories": raw["current_memories"],
            "historical_memories": raw["historical_memories"],
            "evidence": raw["evidence"],
            "sources": raw["sources"],
        }
    )


def test_response_bundle_attests_to_what_was_returned():
    bundle = receipt_for_response(_response_model())
    assert bundle is not None
    assert bundle["claim_keys"] == [KEY]
    assert bundle["claim_ids"] == ["c-new"]
    assert bundle["receipt_count"] == 1
    assert bundle["query_digest"] == query_digest(bundle["query"])
    assert bundle["answered_query"] == PACK.raw["query"]
    # The response was asked at valid_at 2025-06-01, so the answer IS the current
    # claim. The per-key receipt names the same claim the bundle attests to.
    assert bundle["claim_ids"] == ["c-new"]
    assert bundle["receipts"][0]["answer"]["claim_id"] == "c-new"
    # The bundle digest anchors the WHOLE response, not only the named claim.
    assert bundle["memory_pack_digest"] == _pack_digest_of_response()


def test_response_bundle_verifies_against_the_same_response():
    response = _response_model()
    bundle = receipt_for_response(response)
    result = verify_response_receipt(bundle, response.model_dump(mode="json"))
    assert result["verified"], result
    assert result["receipts_checked"] == 1
    assert result["authenticity"] == "NOT ESTABLISHED"


def test_response_bundle_is_rejected_after_any_tampering():
    """Every field a client might edit must be caught."""
    response = _response_model()
    raw = response.model_dump(mode="json")

    for mutate in (
        lambda d: d["current_memories"][0].update({"claim": "Forged"}),
        lambda d: d["evidence"][0].update({"text": "Forged"}),
        lambda d: d["current_memories"][0].update({"observed_at": "2099-01-01T00:00:00+00:00"}),
        lambda d: d["sources"][0].update({"path": "elsewhere.md"}),
    ):
        tampered = json.loads(json.dumps(raw))
        mutate(tampered)
        bundle = receipt_for_response(response)
        result = verify_response_receipt(bundle, tampered)
        assert not result["verified"], mutate


def _pack_digest_of_response() -> str:
    from memory_pack import MemoryPack

    return MemoryPack.from_dict(_response_model().model_dump(mode="json")).digest()


def test_response_bundle_detects_a_changed_query_binding():
    """A caller cannot swap the question and re-derive a matching digest.

    Binding is verified against the artifact's own query, not against the field
    the caller controls.
    """
    response = _response_model()
    bundle = receipt_for_response(response, query="a question nobody asked")
    # Self-consistent on its face...
    assert bundle["query_digest"] == query_digest(bundle["query"])
    # ...but it does not claim to have answered the artifact's query.
    assert bundle["answered_query"] == response.query
    assert bundle["answered_query_digest"] != bundle["query_digest"]

    # Tamper with the artifact so the answered query differs from the bundle's.
    raw = response.model_dump(mode="json")
    raw["query"] = "a question nobody asked"
    result = verify_response_receipt(bundle, raw)
    assert not result["verified"]
    assert result["query_binding"] is False


def test_no_current_memory_yields_no_receipt():
    """An abstention has no answer to attest to, so no receipt is issued."""
    response = _response_model()
    response.current_memories = []
    assert receipt_for_response(response) is None


def test_response_bundle_is_deterministic():
    response = _response_model()
    assert json.dumps(receipt_for_response(response), sort_keys=True) == json.dumps(
        receipt_for_response(response), sort_keys=True
    )
