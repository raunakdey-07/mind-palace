"""Research-only typed primitives for M007/M008 reasoning experiments.

These records compose existing immutable memory identifiers. They do not
mutate claims, resolve authority, or alter the released query path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ReasonCode(StrEnum):
    RELEVANCE_MATCH = "RELEVANCE_MATCH"
    SUBJECT_MATCH = "SUBJECT_MATCH"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    VALID_AT_QUERY_TIME = "VALID_AT_QUERY_TIME"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    EVIDENCE_PRESENT = "EVIDENCE_PRESENT"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    NOT_SUPERSEDED = "NOT_SUPERSEDED"
    SUPERSEDED = "SUPERSEDED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    AUTHORITY_ALLOWED = "AUTHORITY_ALLOWED"
    AUTHORITY_BLOCKED = "AUTHORITY_BLOCKED"
    ABSTAINED = "ABSTAINED"
    AMBIGUOUS_QUERY = "AMBIGUOUS_QUERY"
    NO_SUPPORTING_EVIDENCE = "NO_SUPPORTING_EVIDENCE"


class DecisionKind(StrEnum):
    SELECT = "SELECT"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class TemporalState(StrEnum):
    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
    AS_OF = "AS_OF"
    VALID_AT = "VALID_AT"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"
    RESTORED = "RESTORED"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MemoryDecision:
    query_id: str
    candidate_id: str
    relevance: str
    subject_state: str
    temporal_state: str
    evidence_state: str
    authority_state: str
    conflict_state: str
    uncertainty_state: str
    decision: str
    reason_codes: tuple[ReasonCode, ...]
    supporting_evidence: tuple[str, ...] = ()
    temporal_scope: dict[str, str | None] = field(default_factory=dict)
    conflict_group: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionReceipt:
    """Canonical, replayable research receipt for one bounded decision."""

    query: str
    query_fingerprint: str
    candidate_set_fingerprint: str
    decision: MemoryDecision
    policy_fingerprint: str
    engine_version: str
    memory_snapshot: str
    selected_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    uncertain_candidate_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    reason_codes: tuple[ReasonCode, ...]

    def canonical_payload(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        return fingerprint(self.canonical_payload())


def build_decision_receipt(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    decision: MemoryDecision,
    policy_fingerprint: str,
    engine_version: str,
    memory_snapshot: str,
    evidence_ids: list[str] | tuple[str, ...] = (),
) -> DecisionReceipt:
    candidate_ids = tuple(sorted(str(item["id"]) for item in candidates))
    selected = (
        (decision.candidate_id,) if decision.decision == "SELECT" and decision.candidate_id else ()
    )
    rejected = tuple(item for item in candidate_ids if item not in selected)
    uncertain = tuple(candidate_ids) if decision.uncertainty_state == "uncertain" else ()
    return DecisionReceipt(
        query=query,
        query_fingerprint=fingerprint(query),
        candidate_set_fingerprint=fingerprint(
            sorted(candidates, key=lambda item: str(item.get("id", "")))
        ),
        decision=decision,
        policy_fingerprint=policy_fingerprint,
        engine_version=engine_version,
        memory_snapshot=memory_snapshot,
        selected_candidate_ids=selected,
        rejected_candidate_ids=rejected,
        uncertain_candidate_ids=uncertain,
        evidence_ids=tuple(sorted(evidence_ids)),
        reason_codes=decision.reason_codes,
    )


def explain_receipt(receipt: DecisionReceipt) -> dict[str, Any]:
    """Render a deterministic, machine-readable explanation of a receipt."""
    return {
        "receipt_fingerprint": receipt.fingerprint(),
        "query": receipt.query,
        "query_fingerprint": receipt.query_fingerprint,
        "candidate_set_fingerprint": receipt.candidate_set_fingerprint,
        "selected_candidate_ids": list(receipt.selected_candidate_ids),
        "rejected_candidate_ids": list(receipt.rejected_candidate_ids),
        "uncertain_candidate_ids": list(receipt.uncertain_candidate_ids),
        "evidence_ids": list(receipt.evidence_ids),
        "decision": receipt.decision.decision,
        "reason_codes": [code.value for code in receipt.reason_codes],
        "temporal_state": receipt.decision.temporal_state,
        "authority_state": receipt.decision.authority_state,
        "evidence_state": receipt.decision.evidence_state,
        "conflict_state": receipt.decision.conflict_state,
        "uncertainty_state": receipt.decision.uncertainty_state,
        "policy_fingerprint": receipt.policy_fingerprint,
        "engine_version": receipt.engine_version,
        "memory_snapshot": receipt.memory_snapshot,
    }


def receipt_diff(expected: DecisionReceipt, actual: DecisionReceipt) -> dict[str, Any]:
    """Return stable, machine-readable reasons two receipts diverge."""
    checks = {
        "candidate_drift": expected.candidate_set_fingerprint != actual.candidate_set_fingerprint,
        "evidence_drift": expected.evidence_ids != actual.evidence_ids,
        "policy_drift": expected.policy_fingerprint != actual.policy_fingerprint,
        "engine_drift": expected.engine_version != actual.engine_version,
        "snapshot_drift": expected.memory_snapshot != actual.memory_snapshot,
        "decision_drift": expected.decision != actual.decision,
        "query_drift": expected.query_fingerprint != actual.query_fingerprint,
    }
    return {
        "reproducible": not any(checks.values()),
        "drift": [name for name, changed in checks.items() if changed],
    }


def replay_receipt(
    receipt: DecisionReceipt, *, candidates: list[dict[str, Any]], decision: MemoryDecision
) -> dict[str, Any]:
    """Compare replay inputs without using timestamps or provider state."""
    expected = build_decision_receipt(
        query=receipt.query,
        candidates=candidates,
        decision=decision,
        policy_fingerprint=receipt.policy_fingerprint,
        engine_version=receipt.engine_version,
        memory_snapshot=receipt.memory_snapshot,
        evidence_ids=receipt.evidence_ids,
    )
    return {
        "reproducible": expected.fingerprint() == receipt.fingerprint(),
        "expected_fingerprint": expected.fingerprint(),
        "actual_fingerprint": receipt.fingerprint(),
    }


@dataclass(frozen=True)
class DecisionProvenance:
    query_id: str
    candidate_set_fingerprint: str
    decision: MemoryDecision
    claim_id: str | None
    document_id: str | None
    version_id: str | None
    source_reference: str
    reason_codes: tuple[ReasonCode, ...]
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def temporal_state_at(
    *,
    observed_at: datetime,
    query_time: datetime,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    status: str | None = None,
) -> TemporalState:
    """Classify a claim using explicit temporal fields, never document recency."""
    if status and status.upper() == "SUPERSEDED":
        return TemporalState.SUPERSEDED
    if valid_until is not None and query_time >= valid_until:
        return TemporalState.HISTORICAL
    if valid_from is not None and query_time < valid_from:
        return TemporalState.HISTORICAL
    if observed_at > query_time:
        return TemporalState.HISTORICAL
    return TemporalState.CURRENT


@dataclass(frozen=True)
class ChangeEvent:
    subject: str
    claim_key: str
    before: Any
    after: Any
    effective_time: str | None
    observed_time: str
    evidence: tuple[str, ...]
    source_version: str
    reason: str | None = None


@dataclass(frozen=True)
class SnapshotChange:
    event: str
    claim_key: str
    before: Any
    after: Any
    evidence: tuple[str, ...] = ()


def snapshot_diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[SnapshotChange, ...]:
    """Produce a deterministic claim-key diff for already materialized snapshots."""
    changes = []
    for key in sorted(set(before) | set(after)):
        if key not in before:
            changes.append(SnapshotChange("ADDED", key, None, after[key]))
        elif key not in after:
            changes.append(SnapshotChange("REMOVED", key, before[key], None))
        elif before[key] != after[key]:
            changes.append(SnapshotChange("MODIFIED", key, before[key], after[key]))
    return tuple(changes)


@dataclass(frozen=True)
class ConflictLifecycle:
    conflict_id: str
    claim_ids: tuple[str, ...]
    state: str
    first_observed: str
    last_observed: str
    resolution_basis: str | None = None
    authority_state: str = "UNKNOWN"


def conflict_lifecycle(
    conflict_id: str,
    claim_ids: list[str],
    first_observed: str,
    last_observed: str,
    *,
    state: str = "OPEN",
    resolution_basis: str | None = None,
) -> ConflictLifecycle:
    allowed = {"OPEN", "ACKNOWLEDGED", "RESOLVED", "REOPENED", "SUPERSEDED", "REJECTED", "UNKNOWN"}
    if state not in allowed:
        raise ValueError(f"Unsupported conflict state: {state}")
    if state in {"RESOLVED", "REJECTED", "SUPERSEDED"} and not resolution_basis:
        raise ValueError("A resolved/rejected/superseded conflict requires a basis")
    return ConflictLifecycle(
        conflict_id, tuple(claim_ids), state, first_observed, last_observed, resolution_basis
    )


@dataclass(frozen=True)
class AuditRecord:
    query: str
    candidate_set_fingerprint: str
    decision: MemoryDecision
    authority_state: str
    provider: str
    provider_version: str
    configuration: str
    timestamp: str


def append_audit(
    records: list[AuditRecord],
    *,
    query: str,
    candidates: list[dict[str, Any]],
    decision: MemoryDecision,
    authority_state: str,
    provider: str,
    provider_version: str,
    configuration: str,
    timestamp: str,
) -> AuditRecord:
    record = AuditRecord(
        query,
        fingerprint(candidates),
        decision,
        authority_state,
        provider,
        provider_version,
        configuration,
        timestamp,
    )
    records.append(record)
    return record
