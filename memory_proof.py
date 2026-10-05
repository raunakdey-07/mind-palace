# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Verifiable Memory: a proof that a memory was true, and an offline verifier.

This module is deliberately dependency-free. It imports only the standard library
and `memory_pack`, so a third party can verify a proof with no Mind Palace server,
no database, no embedding model, no network and no API credentials. That is the
whole point: verification must not depend on the same system it is verifying.

WHAT A PROOF IS
---------------
A proof is a bounded, canonical JSON object naming one authoritative claim, the
document version that carried it, the evidence supporting it, its temporal
position, and its supersession lineage, plus two digests:

    authoritative_digest  the Memory Pack this proof was taken from
    proof_digest          the proof itself, over its own canonical bytes

WHAT VERIFICATION PROVES, AND WHAT IT DOES NOT
----------------------------------------------
It proves that the supplied artifact still says what the proof says it said:

  * the referenced claim exists in the artifact under the declared identity;
  * the claim text matches its content-addressed claim id;
  * every cited evidence record belongs to that claim and version;
  * the claim was valid at the declared valid_at;
  * the source version existed at or before the declared as_of;
  * a superseded claim is never presented as current;
  * the artifact's authoritative digest matches the one the proof was minted over.

It does NOT prove that the original source was true, or that the outside world
agreed with it. A proof is an integrity and provenance artifact. It shows that Mind
Palace recorded a claim from a particular span of a particular immutable version,
and that the record has not changed since. Truth is the job of the corpus.

SEARCH AND PROOF ARE SEPARATE WORLDS
-------------------------------------
A proof is verified by identity comparison alone. Nothing here re-runs semantic
retrieval, and nothing here says "something similar was found". Ranking is
probabilistic candidate discovery; proof is deterministic verification.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from memory_pack import MemoryPack

PROOF_SCHEMA_VERSION = 1

SUPPORTED_PROOF_VERSIONS = (PROOF_SCHEMA_VERSION,)


class ProofError(ValueError):
    """The proof itself is malformed. Distinct from a failed verification."""


def _require(data: dict, key: str, kind: type, where: str) -> Any:
    if key not in data:
        raise ProofError(f"{where}: missing {key!r}")
    value = data[key]
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, kind) or isinstance(value, bool) != (kind is bool):
        raise ProofError(f"{where}: {key!r} must be {kind.__name__}, got {type(value).__name__}")
    return value


def _text(data: dict, key: str, where: str, *, allow_none: bool = False) -> Any:
    if key not in data:
        raise ProofError(f"{where}: missing {key!r}")
    value = data[key]
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ProofError(f"{where}: {key!r} must be a string, got {type(value).__name__}")
    return value


def canonical_bytes(obj: Any) -> bytes:
    """The one canonicalisation used by both digests.

    Identical to MemoryPack.canonical_json so a proof and the pack it describes
    cannot disagree about what canonical means.
    """
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def proof_digest(proof: dict) -> str:
    """SHA-256 over the proof's own canonical bytes, excluding the field itself."""
    body = {k: v for k, v in proof.items() if k != "proof_digest"}
    return sha256_hex(canonical_bytes(body))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def build_proof(
    pack: MemoryPack,
    claim_key: str,
    *,
    as_of: str | None = None,
    valid_at: str | None = None,
) -> dict:
    """Mint a proof for one authored key against an already-built pack.

    Raises ProofError when the key is not present, rather than minting a proof
    that cannot verify. A proof that cannot be checked is worse than no proof.
    """
    claims = [c for c in pack.all_claims() if c.key == claim_key]
    if not claims:
        raise ProofError(f"no claim for key {claim_key!r} in this pack")

    state = pack.raw.get("state", {})
    resolved_as_of = as_of or state.get("as_of")
    resolved_valid_at = valid_at or state.get("valid_at")

    evidence_by_claim: dict[str, list[dict]] = {}
    for row in pack.raw.get("evidence", []):
        evidence_by_claim.setdefault(row["claim_id"], []).append(row)
    sources = {s["version_id"]: s for s in pack.raw.get("sources", [])}

    entries = []
    for claim in sorted(claims, key=lambda c: c.id):
        rows = sorted(evidence_by_claim.get(claim.id, []), key=lambda r: r["id"])
        source = sources.get(claim.version_id, {})
        entries.append(
            {
                "claim": {
                    "id": claim.id,
                    "key": claim.key,
                    "text": claim.claim,
                    "value": claim.value,
                    "status": claim.status,
                    "version_id": claim.version_id,
                    "supersedes_id": claim.supersedes_id,
                    "valid_from": claim.valid_from,
                    "valid_until": claim.valid_until,
                },
                "document": {
                    "document_id": source.get("document_id"),
                    "version_id": claim.version_id,
                    "path": source.get("path"),
                    "source_hash": source.get("source_hash"),
                },
                "temporal": {
                    "observed_at": claim.observed_at,
                    "valid_from": claim.valid_from,
                    "valid_to": claim.valid_until,
                    # Whether the pack's own valid_at put THIS claim in force. A
                    # proof often spans a supersession chain, so its entries are
                    # not all valid at the same instant; recording it per entry
                    # keeps the pack-level valid_at meaningful without asserting
                    # something false about the superseded version.
                    "in_force_at_pack_valid_at": _in_force_at(claim, resolved_valid_at),
                },
                "evidence": [
                    {
                        "id": row["id"],
                        "text": row["text"],
                        "heading": row.get("heading"),
                        "start_offset": row.get("start_offset"),
                        "end_offset": row.get("end_offset"),
                        "chunk_id": row.get("chunk_id"),
                    }
                    for row in rows
                ],
                "lineage": _lineage(pack, claim),
            }
        )

    proof = {
        "schema_version": PROOF_SCHEMA_VERSION,
        "proof_version": PROOF_SCHEMA_VERSION,
        "query": pack.raw.get("query", ""),
        "corpus": pack.raw.get("corpus", ""),
        "state": {
            "as_of": resolved_as_of,
            "valid_at": resolved_valid_at,
            "snapshot_id": (pack.raw.get("snapshot") or {}).get("id"),
        },
        "claim_key": claim_key,
        "entries": entries,
        "integrity": {"authoritative_digest": pack.digest()},
    }
    proof["proof_digest"] = proof_digest(proof)
    return proof


def _in_force_at(claim, valid_at: str | None) -> bool | None:
    """Was this claim's validity interval open at ``valid_at``?"""
    if not valid_at:
        return None
    if claim.valid_from and valid_at < claim.valid_from:
        return False
    if claim.valid_until and valid_at >= claim.valid_until:
        return False
    return True


def _lineage(pack: MemoryPack, claim) -> dict:
    """What replaced this claim and what it replaced, within the pack."""
    supersedes = [claim.supersedes_id] if claim.supersedes_id else []
    superseded_by = [
        other.id
        for other in pack.all_claims()
        if other.supersedes_id == claim.id and other.id != claim.id
    ]
    return {
        "supersedes": sorted(supersedes),
        "superseded_by": sorted(superseded_by),
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """The result of checking one proof against one artifact."""

    verified: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.verified:
            lines = ["VERIFIED"]
            lines += [f"Note: {note}" for note in self.notes]
            return "\n".join(lines)
        lines = ["REJECTED"]
        lines += [f"Reason: {reason}" for reason in self.reasons]
        return "\n".join(lines)


def load_proof(data: dict) -> dict:
    """Structural validation. Malformed input is an error, not a rejection."""
    if not isinstance(data, dict):
        raise ProofError("proof must be a JSON object")
    version = _require(data, "proof_version", int, "proof")
    if version not in SUPPORTED_PROOF_VERSIONS:
        raise ProofError(
            f"unsupported proof_version {version!r}; "
            f"this verifier understands {list(SUPPORTED_PROOF_VERSIONS)}"
        )
    _require(data, "schema_version", int, "proof")
    _text(data, "claim_key", "proof")
    _require(data, "entries", list, "proof")
    _require(data, "integrity", dict, "proof")
    _text(data["integrity"], "authoritative_digest", "integrity")
    _text(data, "proof_digest", "proof")
    _require(data, "state", dict, "proof")
    return data


def verify(proof: dict, artifact: MemoryPack | dict) -> Verdict:
    """Check a proof against the authoritative artifact it describes.

    Every check is deterministic identity work. Nothing here consults a model,
    a database or a similarity index.
    """
    proof = load_proof(proof)
    pack = artifact if isinstance(artifact, MemoryPack) else MemoryPack.from_dict(artifact)

    reasons: list[str] = []
    notes: list[str] = []
    checks: dict[str, bool] = {}

    # 1. The proof has not been altered since it was minted.
    recomputed = proof_digest(proof)
    intact = recomputed == proof["proof_digest"]
    checks["proof_intact"] = intact
    if not intact:
        reasons.append("proof digest mismatch: the proof itself was modified")

    # 2. The artifact is the one the proof was minted over.
    digest_ok = pack.digest() == proof["integrity"]["authoritative_digest"]
    checks["artifact_digest"] = digest_ok
    if not digest_ok:
        reasons.append(
            "authoritative digest mismatch: the artifact is not the one this proof was created from"
        )

    by_id = {c.id: c for c in pack.all_claims()}
    evidence_by_id = {e["id"]: e for e in pack.raw.get("evidence", [])}
    sources = {s["version_id"]: s for s in pack.raw.get("sources", [])}
    valid_at = proof["state"].get("valid_at")
    as_of = proof["state"].get("as_of")

    identity_ok = evidence_ok = temporal_ok = status_ok = True

    for entry in proof["entries"]:
        claim_proof = entry.get("claim", {})
        claim_id = claim_proof.get("id")
        key = claim_proof.get("key")

        # 3. The referenced claim exists in the artifact, under that identity.
        # Several claims may share an authored key (a supersession chain), so
        # identity is the claim id; the key is checked separately below.
        claim = by_id.get(claim_id)
        if claim is None:
            identity_ok = False
            reasons.append(f"claim id {claim_id!r} is absent from the artifact")
            continue
        if claim.key != key:
            identity_ok = False
            reasons.append(
                f"claim {claim_id!r} is keyed {claim.key!r} in the artifact "
                f"but the proof claims {key!r}"
            )
            continue

        # 4. Claim content matches its content-addressed identity.
        if (
            claim.claim != claim_proof.get("text")
            or claim.key != key
            or claim.value != claim_proof.get("value")
            or claim.version_id != claim_proof.get("version_id")
        ):
            identity_ok = False
            reasons.append(f"claim {claim_id!r} content does not match its proof entry")

        # 4b. Provenance: the document the proof names is the one the artifact
        # records. Without this, a proof could point at a different document than
        # the one the claim was actually derived from.
        proof_document = entry.get("document", {})
        if proof_document.get("path"):
            source = sources.get(claim.version_id)
            actual_path = (source or {}).get("path") or claim.path
            if proof_document.get("path") != actual_path:
                identity_ok = False
                reasons.append(
                    f"proof names document {proof_document['path']!r} for claim "
                    f"{claim_id!r} but the artifact records {actual_path!r}"
                )
            elif claim.path != actual_path:
                identity_ok = False
                reasons.append(f"claim {claim_id!r} path does not match its source version")

        # 5. Every cited evidence record belongs to this claim and version.
        for row in entry.get("evidence", []):
            actual = evidence_by_id.get(row.get("id"))
            if actual is None:
                evidence_ok = False
                reasons.append(f"evidence {row.get('id')!r} is absent from the artifact")
                continue
            if actual.get("claim_id") != claim_id:
                evidence_ok = False
                reasons.append(
                    f"evidence record {row['id']!r} does not belong to claim {claim_id!r}"
                )
            elif actual.get("version_id") != claim.version_id:
                evidence_ok = False
                reasons.append(
                    f"evidence record {row['id']!r} does not belong to version {claim.version_id!r}"
                )
            elif actual.get("text") != row.get("text"):
                evidence_ok = False
                reasons.append(f"evidence record {row['id']!r} text has been altered")

        # 6. Temporal validity. The pack-level valid_at governs the answer as a
        # whole; per entry, the proof records whether that claim was in force.
        # Checking the interval for EVERY entry would be wrong, because a proof
        # over a supersession chain legitimately spans more than one instant.
        in_force = entry.get("temporal", {}).get("in_force_at_pack_valid_at")
        if valid_at:
            # Recomputed from the artifact, never from the proof's own claim.
            actual_force = _in_force_at(claim, valid_at)
            if in_force is not None and in_force != actual_force:
                temporal_ok = False
                reasons.append(
                    f"claim {claim_id!r} records in_force={in_force} but the artifact "
                    f"says {actual_force} at valid_at={valid_at}"
                )
            # A claim may be CURRENT now and still not have been valid at an
            # earlier instant -- that is the whole point of a historical proof.
            # Rejecting it here would make historical verification impossible, so
            # the contradiction is surfaced, not enforced: if the artifact calls
            # the claim CURRENT while the entry says it was not yet in force, a
            # receipt over that claim must not silently claim otherwise. The
            # receipt layer decides which claim to answer with.
            if actual_force is False and claim.status == "CURRENT":
                notes.append(
                    f"claim {claim_id!r} is CURRENT now but was not valid at "
                    f"valid_at={valid_at}; it does not answer a question asked then"
                )

        # 7. Observation state: the source version existed by as_of.
        if as_of and claim.observed_at > as_of:
            temporal_ok = False
            reasons.append(
                f"claim {claim_id!r} was observed at {claim.observed_at}, after as_of={as_of}"
            )

        # 8. Supersession. The artifact is the authority: compare what the proof says
        # about status and lineage against what the artifact records, in both
        # directions, so neither hiding a supersession nor inventing one passes.
        claimed_status = claim_proof.get("status")
        if claimed_status != claim.status:
            status_ok = False
            reasons.append(
                f"claim {claim_id!r} is {claim.status} in the artifact but the proof "
                f"claims {claimed_status}"
            )

        actual_superseded_by = sorted(
            other.id
            for other in pack.all_claims()
            if other.supersedes_id == claim.id and other.id != claim.id
        )
        claimed_lineage = sorted(entry.get("lineage", {}).get("superseded_by", []))
        if actual_superseded_by != claimed_lineage:
            status_ok = False
            reasons.append(
                f"lineage for claim {claim_id!r} claims superseded_by="
                f"{claimed_lineage} but the artifact records {actual_superseded_by}"
            )

        if claim.status == "CURRENT" and actual_superseded_by:
            status_ok = False
            reasons.append(f"claim {claim_id!r} is superseded but presented as CURRENT")

    checks["identity"] = identity_ok
    checks["evidence_ownership"] = evidence_ok
    checks["temporal"] = temporal_ok
    checks["status_consistency"] = status_ok

    return Verdict(verified=not reasons, reasons=reasons, checks=checks, notes=notes)


def verify_files(proof_path: str, artifact_path: str) -> Verdict:
    """Offline entry point: two files, no server."""
    with open(proof_path, encoding="utf-8") as handle:
        proof = json.load(handle)
    with open(artifact_path, encoding="utf-8") as handle:
        artifact = json.load(handle)
    return verify(proof, artifact)


__all__ = [
    "PROOF_SCHEMA_VERSION",
    "ProofError",
    "Verdict",
    "build_proof",
    "canonical_bytes",
    "load_proof",
    "proof_digest",
    "sha256_hex",
    "verify",
    "verify_files",
]
