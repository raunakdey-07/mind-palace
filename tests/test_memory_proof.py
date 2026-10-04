"""M014: the Verifiable Memory demonstration, end to end.

The milestone scenario: record version A, supersede it with B, query current and
historical state, mint proofs for both, export, verify offline, tamper, watch
verification fail, restore, and watch it pass again.

The tamper step is the point of the whole feature, so several tests assert on the
SPECIFIC invariant that fails rather than only on "it is False".
"""

from __future__ import annotations

import json

import pytest

from memory_pack import MemoryPack
from memory_proof import (
    ProofError,
    build_proof,
    canonical_bytes,
    proof_digest,
    verify,
    verify_files,
)


@pytest.fixture
def pack() -> MemoryPack:
    """A pack with one superseded claim, one current claim, and one conflict."""
    return MemoryPack.from_dict(
        {
            "schema_version": 1,
            "query": "What datastore did production use?",
            "corpus": "demo",
            "state": {"as_of": None, "valid_at": "2025-06-01T00:00:00+00:00"},
            "current_memories": [
                {
                    "id": "c-new",
                    "key": "architecture.postgres",
                    "value": "PostgreSQL",
                    "claim": "The primary datastore is PostgreSQL.",
                    "status": "CURRENT",
                    "version_id": "v-new",
                    "path": "docs/tech/postgres.md",
                    "observed_at": "2025-06-01T00:00:00+00:00",
                    "valid_from": "2025-06-01T00:00:00+00:00",
                    "valid_until": None,
                    "supersedes_id": "c-old",
                    "evidence_ids": ["e-new"],
                }
            ],
            "historical_memories": [
                {
                    "id": "c-old",
                    "key": "architecture.postgres",
                    "value": "SQLite",
                    "claim": "The primary datastore is SQLite.",
                    "status": "SUPERSEDED",
                    "version_id": "v-old",
                    "path": "adrs/adr-011.md",
                    "observed_at": "2025-01-01T00:00:00+00:00",
                    "valid_from": "2025-01-01T00:00:00+00:00",
                    "valid_until": "2025-06-01T00:00:00+00:00",
                    "supersedes_id": None,
                    "evidence_ids": ["e-old"],
                }
            ],
            "evidence": [
                {
                    "id": "e-new",
                    "claim_id": "c-new",
                    "version_id": "v-new",
                    "document_id": "d-new",
                    "path": "docs/tech/postgres.md",
                    "source_hash": "h-new",
                    "chunk_id": "k-new",
                    "heading": "Datastore",
                    "text": "The primary datastore is PostgreSQL.",
                    "start_offset": 0,
                    "end_offset": 35,
                    "observed_at": "2025-06-01T00:00:00+00:00",
                },
                {
                    "id": "e-old",
                    "claim_id": "c-old",
                    "version_id": "v-old",
                    "document_id": "d-old",
                    "path": "adrs/adr-011.md",
                    "source_hash": "h-old",
                    "chunk_id": "k-old",
                    "heading": "Edge store",
                    "text": "The primary datastore is SQLite.",
                    "start_offset": 0,
                    "end_offset": 30,
                    "observed_at": "2025-01-01T00:00:00+00:00",
                },
            ],
            "sources": [
                {
                    "document_id": "d-new",
                    "version_id": "v-new",
                    "path": "docs/tech/postgres.md",
                    "source_hash": "h-new",
                    "observed_at": "2025-06-01T00:00:00+00:00",
                },
                {
                    "document_id": "d-old",
                    "version_id": "v-old",
                    "path": "adrs/adr-011.md",
                    "source_hash": "h-old",
                    "observed_at": "2025-01-01T00:00:00+00:00",
                },
            ],
        }
    )


# --- the demonstration ------------------------------------------------------


def test_step_1_to_11_the_milestone_scenario(pack, tmp_path):
    """Record A, supersede with B, prove, export, verify, tamper, restore."""
    # Steps 1-2: version A exists and is superseded by B in the same pack.
    old = next(c for c in pack.all_claims() if c.id == "c-old")
    new = next(c for c in pack.all_claims() if c.id == "c-new")
    assert old.status == "SUPERSEDED" and new.supersedes_id == "c-old"

    # Step 3-4: current and historical states are distinguishable.
    assert [c.id for c in pack.current] == ["c-new"]
    assert [c.id for c in pack.historical] == ["c-old"]

    # Step 5: a proof for each.
    proof_new = build_proof(pack, "architecture.postgres")
    assert {e["claim"]["id"] for e in proof_new["entries"]} == {"c-new", "c-old"}
    lineage = {e["claim"]["id"]: e["lineage"] for e in proof_new["entries"]}
    assert lineage["c-old"]["superseded_by"] == ["c-new"]
    assert lineage["c-new"]["supersedes"] == ["c-old"]

    # Step 6: export.
    pack_path = tmp_path / "pack.json"
    proof_path = tmp_path / "proof.json"
    pack_path.write_text(pack.canonical_json(), encoding="utf-8")
    proof_path.write_text(json.dumps(proof_new, indent=2, sort_keys=True), encoding="utf-8")

    # Step 7: verify offline, from files only.
    verdict = verify_files(str(proof_path), str(pack_path))
    assert verdict.verified, verdict.render()

    # Step 8-9: tamper one authoritative byte; verification must reject.
    raw = json.loads(pack_path.read_text(encoding="utf-8"))
    raw["current_memories"][0]["claim"] = "The primary datastore is MySQL."
    tampered = tmp_path / "tampered.json"
    tampered.write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    rejected = verify_files(str(proof_path), str(tampered))
    assert not rejected.verified
    assert any("authoritative digest mismatch" in r for r in rejected.reasons)

    # Step 10-11: restore, and it passes again.
    assert verify_files(str(proof_path), str(pack_path)).verified


# --- integrity invariants ---------------------------------------------------


def test_proof_digest_is_canonical_and_key_order_independent(pack):
    proof = build_proof(pack, "architecture.postgres")
    reordered = {k: proof[k] for k in sorted(proof, reverse=True)}
    assert proof_digest(reordered) == proof["proof_digest"]


def test_tampering_the_proof_is_detected(pack):
    proof = build_proof(pack, "architecture.postgres")
    proof["state"]["valid_at"] = "1999-01-01T00:00:00+00:00"
    verdict = verify(proof, pack)
    assert not verdict.verified
    assert any("proof digest mismatch" in r for r in verdict.reasons)


def test_tampered_evidence_text_is_detected(pack):
    proof = build_proof(pack, "architecture.postgres")
    proof["entries"][0]["evidence"][0]["text"] = "something else entirely"
    proof["proof_digest"] = proof_digest(proof)  # re-mint, so only evidence differs
    verdict = verify(proof, pack)
    assert not verdict.verified
    assert any("evidence" in r for r in verdict.reasons)


def test_tampered_temporal_state_is_detected(pack):
    """Falsifying an entry's own validity interval must be caught.

    The pack-level valid_at legitimately spans a supersession chain, so the
    per-entry force flag is what carries the claim-level temporal assertion.
    """
    proof = build_proof(pack, "architecture.postgres")
    entry = next(e for e in proof["entries"] if e["claim"]["id"] == "c-new")
    entry["temporal"]["in_force_at_pack_valid_at"] = False
    proof["proof_digest"] = proof_digest(proof)
    verdict = verify(proof, pack)
    assert not verdict.verified
    assert any("records in_force=" in r for r in verdict.reasons)


def test_tampered_document_identity_is_detected(pack):
    proof = build_proof(pack, "architecture.postgres")
    proof["entries"][0]["document"]["path"] = "somewhere/else.md"
    proof["proof_digest"] = proof_digest(proof)
    verdict = verify(proof, pack)
    assert not verdict.verified
    assert any("but the artifact records" in r for r in verdict.reasons)


def test_evidence_must_belong_to_the_claim(pack):
    proof = build_proof(pack, "architecture.postgres")
    # Move the current claim's evidence onto the superseded entry.
    current = next(e for e in proof["entries"] if e["claim"]["id"] == "c-new")
    stale = next(e for e in proof["entries"] if e["claim"]["id"] == "c-old")
    stale["evidence"] = current["evidence"]
    proof["proof_digest"] = proof_digest(proof)
    verdict = verify(proof, pack)
    assert not verdict.verified
    assert any("does not belong to claim" in r for r in verdict.reasons)


def test_a_superseded_claim_is_never_presented_as_current(pack):
    """Hiding a supersession must fail, even if the proof re-mints its digest."""
    proof = build_proof(pack, "architecture.postgres")
    entry = next(e for e in proof["entries"] if e["claim"]["id"] == "c-old")
    entry["lineage"]["superseded_by"] = []  # hide the supersession
    entry["claim"]["status"] = "CURRENT"  # and claim it is current
    proof["proof_digest"] = proof_digest(proof)
    verdict = verify(proof, pack)
    assert not verdict.verified
    assert any("but the artifact records" in r for r in verdict.reasons)


def test_build_proof_refuses_an_unknown_key(pack):
    with pytest.raises(ProofError):
        build_proof(pack, "architecture.does_not_exist")


def test_unsupported_proof_version_is_an_error_not_a_verdict():
    with pytest.raises(ProofError):
        verify({"proof_version": 99}, {})


def test_canonicalisation_matches_the_pack(pack):
    """One canonicalisation, so a proof and its pack cannot disagree."""
    assert canonical_bytes(pack.raw) == pack.canonical_json().encode("utf-8")
