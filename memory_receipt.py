# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Memory Receipt: a portable record of what was remembered, and on what basis.

Standard library only, like `memory_pack` and `memory_proof`, so a receipt can be
minted, exported and checked with no Mind Palace runtime present.

WHAT A RECEIPT IS
-----------------
A Memory Pack says what the archive currently holds. A receipt says what Mind
Palace *returned to one application, for one query, at one historical state*, and
carries the proof needed to check that later. It answers:

    What exactly did Mind Palace tell my application?
    From which authoritative version?
    At what historical state?
    With what evidence?
    And can I verify that after the fact, elsewhere?

It is deliberately small. It references the authoritative pack by digest rather
than embedding it, so a response carrying a receipt stays bounded.

QUERY BINDING
--------------
A proof that "claim X exists" is much weaker than a proof that "for query Q, this
is the authoritative memory Mind Palace returned". A receipt records both the
exact query string and a digest over it, so the binding is explicit and checkable.

This is still not a claim about retrieval *quality*. Verification establishes that
the recorded result corresponds to the recorded query and authoritative state. It
does not establish that the result was the best possible answer, and it says
nothing about any prose an LLM later writes from it.

TRUST MODEL -- THREE DISTINCT PROPERTIES
----------------------------------------
    INTEGRITY    Can we detect that the covered artifact changed?
    PROVENANCE   Do the claim/evidence/version relations match the artifact?
    AUTHENTICITY Did this artifact really come from the expected instance?

Integrity and provenance are established here, deterministically, from the
artifact alone. Authenticity is **NOT established**: anyone able to replace both
the artifact and the proof can recompute the digests. Authenticity needs a trust
anchor that lives outside the artifact -- a pinned digest the developer kept
somewhere else, or a signature from a key the verifier already trusts. See
`verify_trust` for the pinned-digest case. Until a trust anchor is supplied, the
honest verdict is that authenticity is unestablished, and the CLI says so.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from memory_pack import MemoryPack
from memory_proof import build_proof, canonical_bytes, sha256_hex, verify

RECEIPT_SCHEMA_VERSION = 1
SUPPORTED_RECEIPT_VERSIONS = (RECEIPT_SCHEMA_VERSION,)


class ReceiptError(ValueError):
    """The receipt is malformed. Distinct from a failed verification."""


def _require(data: dict, key: str, kind: type, where: str) -> Any:
    if key not in data:
        raise ReceiptError(f"{where}: missing {key!r}")
    value = data[key]
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, kind) or isinstance(value, bool) != (kind is bool):
        raise ReceiptError(f"{where}: {key!r} must be {kind.__name__}, got {type(value).__name__}")
    return value


def canonical_receipt_json(receipt: dict) -> str:
    """The single canonical serialisation for a receipt.

    Same contract as MemoryPack.canonical_json: sorted keys, no incidental
    whitespace, so an identical receipt always produces identical bytes.
    """
    return json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def query_digest(query: str) -> str:
    """Deterministic identity of the query text a receipt is bound to."""
    return sha256_hex(canonical_bytes({"query": query}))


def receipt_digest(receipt: dict) -> str:
    """SHA-256 over the receipt's own canonical bytes, excluding this field.

    The field is excluded whether or not it is present, so calling this before or
    after assignment yields the same value. That matters: `build_receipt` computes
    the id BEFORE inserting it, and a verifier recomputes it AFTER reading it from a
    file. If the two paths hashed different shapes, every honest receipt would be
    rejected.
    """
    body = {k: v for k, v in receipt.items() if k != "receipt_id"}
    return sha256_hex(canonical_bytes(body))


def _moment(value: str | None) -> datetime | None:
    """Parse an ISO-8601 instant, normalising the offset spelling.

    Fixtures and clients write the same instant two ways -- ``...T00:00:00Z`` and
    ``...T00:00:00+00:00`` -- and comparing those as STRINGS is wrong, because
    ``Z`` sorts after ``+``. That silently excluded a claim whose validity had
    actually begun, so a receipt for "PostgreSQL" quoted the superseded SQLite.
    Instant comparisons are done on parsed datetimes here, never on text.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _contains(valid_at: str | None, start: str | None, end: str | None) -> bool:
    """Half-open containment: ``valid_until`` is exclusive, as the archive treats it."""
    at = _moment(valid_at)
    if at is None:
        return False
    begins = _moment(start)
    if begins is not None and at < begins:
        return False
    ends = _moment(end)
    return not (ends is not None and at >= ends)


def _claim_in_force(entries: list[dict], valid_at: str | None):
    """The entry that was authoritative at ``valid_at``.

    Preference order:
      1. an entry whose validity interval contains ``valid_at``;
      2. otherwise the CURRENT entry, so an ordinary current-state receipt is
         unchanged.

    Within (1) the CURRENT entry wins the tie. Validity windows are half-open, but
    at the exact instant a claim takes over, both neighbours can satisfy
    containment. Taking the first match in evidence order then named the claim that
    had just been superseded, so a receipt for "PostgreSQL" quoted SQLite. The
    response's own claim set is the authority on what was returned.
    """
    current = next((e for e in entries if e["claim"].get("status") == "CURRENT"), None)
    if valid_at:
        in_force = [
            entry
            for entry in entries
            if _contains(
                valid_at,
                entry.get("temporal", {}).get("valid_from"),
                entry.get("temporal", {}).get("valid_to"),
            )
        ]
        if in_force:
            if current is not None and any(current is e for e in in_force):
                return current
            return in_force[0]
    return current if current is not None else entries[0]


def build_receipt(
    pack: MemoryPack,
    claim_key: str,
    *,
    query: str | None = None,
    as_of: str | None = None,
    valid_at: str | None = None,
    include_proof: bool = True,
) -> dict:
    """Build a receipt for one authored key against an already-built pack.

    The pack is the authority. Everything in the receipt is read from it, so a
    receipt cannot assert lineage the archive does not record.
    """
    proof = build_proof(pack, claim_key, as_of=as_of, valid_at=valid_at)
    resolved_query = query if query is not None else pack.raw.get("query", "")

    entries = proof["entries"]
    # The receipt must name the answer Mind Palace would have given AT THIS
    # INSTANT. Picking the CURRENT claim unconditionally is wrong for a historical
    # valid_at: it would attach a receipt naming PostgreSQL to a question asked in
    # February, when the archive recorded SQLite.
    primary = _claim_in_force(entries, valid_at or proof["state"].get("valid_at"))
    claim = primary["claim"]
    document = primary.get("document", {})
    temporal = primary.get("temporal", {})

    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_version": RECEIPT_SCHEMA_VERSION,
        "query": resolved_query,
        "query_digest": query_digest(resolved_query),
        "answer": {
            "claim_id": claim["id"],
            "claim_key": claim["key"],
            "claim": claim["text"],
            "value": claim.get("value"),
            "status": claim.get("status"),
        },
        "state": {
            "as_of": proof["state"].get("as_of"),
            "valid_at": proof["state"].get("valid_at"),
            "snapshot_id": proof["state"].get("snapshot_id"),
        },
        "source": {
            "document_id": document.get("document_id"),
            "document_version_id": document.get("version_id"),
            "path": document.get("path"),
        },
        "evidence": [
            {
                "evidence_id": row["id"],
                "heading": row.get("heading"),
                "text": row.get("text"),
                "start_offset": row.get("start_offset"),
                "end_offset": row.get("end_offset"),
            }
            for row in primary.get("evidence", [])
        ],
        "lineage": primary.get("lineage", {}),
        "temporal": {
            "observed_at": temporal.get("observed_at"),
            "valid_from": temporal.get("valid_from"),
            "valid_to": temporal.get("valid_to"),
            "in_force_at_valid_at": temporal.get("in_force_at_pack_valid_at"),
        },
        "integrity": {
            "memory_pack_digest": proof["integrity"]["authoritative_digest"],
            "proof_digest": proof["proof_digest"],
        },
        "claim_ids": sorted(e["claim"]["id"] for e in entries),
    }
    if include_proof:
        receipt["proof"] = proof
    # Computed last, and over a body that does not yet contain receipt_id, so the
    # verifier's recomputation (which reads receipt_id from the file and excludes
    # it) produces the identical value.
    receipt["receipt_id"] = receipt_digest(receipt)
    return receipt


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def load_receipt(data: dict) -> dict:
    """Structural validation. Malformed input raises; a failed check does not."""
    if not isinstance(data, dict):
        raise ReceiptError("receipt must be a JSON object")
    version = _require(data, "receipt_version", int, "receipt")
    if version not in SUPPORTED_RECEIPT_VERSIONS:
        raise ReceiptError(
            f"unsupported receipt_version {version!r}; "
            f"this verifier understands {list(SUPPORTED_RECEIPT_VERSIONS)}"
        )
    _require(data, "schema_version", int, "receipt")
    _require(data, "answer", dict, "receipt")
    _require(data, "state", dict, "receipt")
    _require(data, "integrity", dict, "receipt")
    _require(data, "evidence", list, "receipt")
    _require(data, "receipt_id", str, "receipt")
    return data


def verify_receipt(receipt: dict, artifact: MemoryPack | dict) -> dict:
    """Check a receipt against the authoritative artifact it describes.

    Returns a structured result with the three trust properties kept separate,
    because conflating them is the failure this milestone exists to prevent.
    """
    receipt = load_receipt(receipt)
    pack = artifact if isinstance(artifact, MemoryPack) else MemoryPack.from_dict(artifact)

    integrity: list[str] = []
    provenance: list[str] = []
    temporal: list[str] = []
    supersession: list[str] = []

    # -- receipt self-consistency
    recomputed_id = receipt_digest(receipt)
    if recomputed_id != receipt["receipt_id"]:
        integrity.append("receipt digest mismatch: the receipt itself was modified")
    if query_digest(receipt["query"]) != receipt.get("query_digest"):
        integrity.append(
            "query digest mismatch: the recorded query does not match the query_digest"
        )

    # -- the artifact is the one the receipt was minted over
    if pack.digest() != receipt["integrity"]["memory_pack_digest"]:
        integrity.append(
            "memory pack digest mismatch: the artifact is not the one this receipt was created from"
        )

    # -- the receipt's own proof, when present, must also verify
    proof_checks = None
    if receipt.get("proof"):
        proof_checks = verify(receipt["proof"], pack)
        if not proof_checks.verified:
            integrity.append("embedded proof rejected against the artifact")

    # -- claim identity and query/result binding
    claim = receipt["answer"]
    actual = next((c for c in pack.all_claims() if c.id == claim["claim_id"]), None)
    if actual is None:
        provenance.append(f"claim {claim['claim_id']!r} is absent from the artifact")
    else:
        if actual.key != claim["claim_key"]:
            provenance.append(
                f"claim {claim['claim_id']!r} is keyed {actual.key!r} in the artifact"
            )
        if actual.claim != claim["claim"]:
            provenance.append(f"claim {claim['claim_id']!r} text does not match the receipt")
        if actual.value != claim.get("value"):
            provenance.append(f"claim {claim['claim_id']!r} value does not match the receipt")

    # -- document provenance, cross-checked against the artifact
    if actual is not None:
        source = receipt.get("source", {})
        recorded = next(
            (s for s in pack.raw.get("sources", []) if s["version_id"] == actual.version_id),
            None,
        )
        actual_path = (recorded or {}).get("path") or actual.path
        if source.get("path") and source["path"] != actual_path:
            provenance.append(
                f"receipt names document {source['path']!r} but the artifact records "
                f"{actual_path!r}"
            )
        if source.get("document_version_id") and actual.version_id != source["document_version_id"]:
            provenance.append(
                f"receipt names version {source['document_version_id']!r} but the "
                f"claim belongs to {actual.version_id!r}"
            )
        if actual.path != actual_path:
            provenance.append(f"claim {actual.id!r} path does not match its source version")

    # -- evidence ownership
    evidence_by_id = {e["id"]: e for e in pack.raw.get("evidence", [])}
    for row in receipt["evidence"]:
        found = evidence_by_id.get(row.get("evidence_id"))
        if found is None:
            provenance.append(f"evidence {row.get('evidence_id')!r} is absent from the artifact")
            continue
        if found.get("claim_id") != claim["claim_id"]:
            provenance.append(
                f"evidence {row['evidence_id']!r} does not belong to claim {claim['claim_id']!r}"
            )
        elif found.get("text") != row.get("text"):
            provenance.append(f"evidence {row['evidence_id']!r} text has been altered")

    # -- temporal state, recomputed from the artifact
    valid_at = receipt["state"].get("valid_at")
    if actual is not None and valid_at:
        # Parsed, not compared as text: the same instant is written with a Z in
        # some fixtures and +00:00 in others, and string comparison silently gets
        # that wrong.
        if not _contains(valid_at, actual.valid_from, actual.valid_until):
            temporal.append(
                f"claim was not valid at valid_at={valid_at} "
                f"(valid_from={actual.valid_from}, valid_to={actual.valid_until})"
            )
    if receipt["temporal"].get("in_force_at_valid_at") is False and actual is not None:
        if actual.status == "CURRENT" and _contains(
            valid_at, actual.valid_from, actual.valid_until
        ):
            temporal.append(
                "receipt records the claim as not in force, but the artifact records it as CURRENT"
            )

    # -- supersession, derived from the artifact rather than trusted
    if actual is not None:
        actual_superseded_by = sorted(
            c.id for c in pack.all_claims() if c.supersedes_id == actual.id and c.id != actual.id
        )
        claimed = sorted(receipt["lineage"].get("superseded_by", []))
        if actual_superseded_by != claimed:
            supersession.append(
                f"lineage claims superseded_by={claimed} but the artifact records "
                f"{actual_superseded_by}"
            )
        if actual.status != claim.get("status"):
            supersession.append(
                f"receipt says status={claim.get('status')} but the artifact records "
                f"{actual.status}"
            )

    return {
        "verified": not (integrity or provenance or temporal or supersession),
        "integrity": integrity,
        "provenance": provenance,
        "temporal": temporal,
        "supersession": supersession,
        # Never inferred from a digest. See the module docstring.
        "authenticity": "NOT ESTABLISHED",
        "proof_verified": None if proof_checks is None else proof_checks.verified,
    }


def verify_trust(result: dict, expected_digest: str, actual_digest: str) -> dict:
    """The authenticity step, if the verifier holds a trust anchor.

    Authenticity is a comparison against something that did NOT come from the
    artifact: a digest the developer pinned out of band. Without one there is
    nothing to compare, and the honest answer is that authenticity is not
    established.
    """
    if not expected_digest:
        return {
            "authenticated": False,
            "reason": "no trust anchor supplied; authenticity is not established",
        }
    if expected_digest == actual_digest:
        return {
            "authenticated": True,
            "reason": "artifact digest matches the pinned trust anchor",
        }
    return {
        "authenticated": False,
        "reason": "artifact digest does not match the pinned trust anchor",
    }


def _iso(value):
    """An aware datetime as its ISO-8601 string; None and strings pass through.

    The response model carries datetimes and the portable contracts carry strings.
    Converting once at this boundary is what lets REST, SDK and MCP produce
    byte-identical receipts from the same answer.
    """
    if value is None or isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def receipt_for_response(response, *, query: str | None = None) -> dict | None:
    """Build receipts describing an authoritative response, or None.

    ONE canonical receipt, shared by REST, SDK, MCP and CLI. Surfaces differ only
    in how they SERIALISE it, never in what they assert, so an answer cannot mean
    two different things depending on which surface asked.

    A receipt is emitted per authored key the response actually returned as
    CURRENT. A response with no current memory -- an abstention, or a purely
    historical one -- yields no receipt, because there is no answer to attest to.
    The field is then absent rather than present-and-empty, so a client can tell
    "nothing was returned" from "a receipt was withheld".

    The enclosing pack digest covers the WHOLE authoritative response, so it is an
    integrity anchor for everything returned, not only the claims named here.
    """
    current = list(getattr(response, "current_memories", []) or [])
    if not current:
        return None

    keys = sorted({c.key for c in current})
    state = getattr(response, "state", None)
    as_of = _iso(getattr(state, "as_of", None))
    valid_at = _iso(getattr(state, "valid_at", None))
    resolved_query = query if query is not None else getattr(response, "query", "")
    pack = MemoryPack.from_dict(response.model_dump(mode="json"))
    digest = pack.digest()

    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "corpus": getattr(response, "corpus", ""),
        "query": resolved_query,
        "query_digest": query_digest(resolved_query),
        # Binding is checked against the ARTIFACT's query, not against this field:
        # a self-consistent digest would only prove the writer computed it correctly.
        "answered_query": pack.raw.get("query", ""),
        "answered_query_digest": query_digest(pack.raw.get("query", "")),
        "claim_ids": sorted(c.id for c in current),
        "claim_keys": keys,
        "state": {"as_of": as_of, "valid_at": valid_at},
        "memory_pack_digest": digest,
        "receipt_count": len(keys),
        "receipts": [
            build_receipt(pack, key, query=resolved_query, as_of=as_of, valid_at=valid_at)
            for key in keys
        ],
    }


def verify_response_receipt(receipt: dict, artifact: MemoryPack | dict) -> dict:
    """Verify every receipt in a response bundle against the artifact.

    The artifact is re-read WITHOUT its receipt. `MemoryPack.from_dict` keeps
    unknown keys, so a payload that still carries the receipt would re-digest to
    something the bundle never claimed, and every honest response would fail its
    own check. The authoritative layer already excludes it from
    `canonical_json`; this is the same rule on the verification side.
    """
    if not isinstance(receipt, dict) or "receipts" not in receipt:
        raise ReceiptError("not a response receipt bundle")
    if isinstance(artifact, MemoryPack):
        pack = artifact
    else:
        pack = MemoryPack.from_dict({k: v for k, v in artifact.items() if k != "receipt"})
    failed: list[dict] = []
    for entry in receipt["receipts"]:
        result = verify_receipt(entry, pack)
        if not result["verified"]:
            failed.append({"receipt_id": entry.get("receipt_id"), **result})
    digest_ok = receipt.get("memory_pack_digest") == pack.digest()
    # Compared against the artifact's own query, so a caller cannot substitute a
    # different question and re-derive a matching digest.
    query_ok = receipt.get("answered_query_digest") == query_digest(pack.raw.get("query", ""))
    return {
        "verified": not failed and digest_ok and query_ok,
        "memory_pack_digest": digest_ok,
        "query_binding": query_ok,
        "receipts_checked": len(receipt["receipts"]),
        "failures": failed,
        "authenticity": "NOT ESTABLISHED",
    }


__all__ = [
    "RECEIPT_SCHEMA_VERSION",
    "ReceiptError",
    "build_receipt",
    "canonical_receipt_json",
    "load_receipt",
    "query_digest",
    "receipt_digest",
    "receipt_for_response",
    "verify_receipt",
    "verify_response_receipt",
    "verify_trust",
]
