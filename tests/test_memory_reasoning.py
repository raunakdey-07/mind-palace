from datetime import datetime, timezone

import pytest

from api.services.memory_reasoning import (
    AuditRecord,
    ConflictLifecycle,
    MemoryDecision,
    ReasonCode,
    append_audit,
    build_decision_receipt,
    conflict_lifecycle,
    explain_receipt,
    fingerprint,
    receipt_diff,
    replay_receipt,
    snapshot_diff,
    temporal_state_at,
)


def decision():
    return MemoryDecision(
        query_id="q1",
        candidate_id="c1",
        relevance="relevant",
        subject_state="compatible",
        temporal_state="CURRENT",
        evidence_state="sufficient",
        authority_state="current",
        conflict_state="none",
        uncertainty_state="known",
        decision="SELECT",
        reason_codes=(ReasonCode.RELEVANCE_MATCH,),
    )


def test_temporal_state_does_not_use_recency_as_validity():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert temporal_state_at(observed_at=at, query_time=at) == "CURRENT"
    assert temporal_state_at(observed_at=at, query_time=at.replace(year=2024)) == "HISTORICAL"
    assert (
        temporal_state_at(observed_at=at, query_time=at, valid_from=at.replace(year=2027))
        == "HISTORICAL"
    )
    assert temporal_state_at(observed_at=at, query_time=at, status="SUPERSEDED") == "SUPERSEDED"


def test_snapshot_diff_is_deterministic_and_traceable():
    result = snapshot_diff({"a": 1, "b": 2}, {"b": 3, "c": 4})
    assert [(x.event, x.claim_key, x.before, x.after) for x in result] == [
        ("REMOVED", "a", 1, None),
        ("MODIFIED", "b", 2, 3),
        ("ADDED", "c", None, 4),
    ]


def test_conflict_resolution_requires_basis():
    with pytest.raises(ValueError):
        conflict_lifecycle("x", ["a", "b"], "t1", "t2", state="RESOLVED")
    result = conflict_lifecycle(
        "x", ["a", "b"], "t1", "t2", state="RESOLVED", resolution_basis="authored"
    )
    assert isinstance(result, ConflictLifecycle)


def test_receipt_is_stable_and_replay_verifiable():
    candidates = [{"id": "c1", "claim": "one"}, {"id": "c2", "claim": "two"}]
    receipt = build_decision_receipt(
        query="q",
        candidates=candidates,
        decision=decision(),
        policy_fingerprint="policy-v1",
        engine_version="engine-v1",
        memory_snapshot="snapshot-a",
        evidence_ids=["e1"],
    )
    replay = replay_receipt(receipt, candidates=list(reversed(candidates)), decision=decision())
    assert replay["reproducible"] is True
    assert receipt.query_fingerprint == fingerprint("q")
    assert receipt.selected_candidate_ids == ("c1",)
    assert receipt.rejected_candidate_ids == ("c2",)


def test_receipt_explanation_is_machine_readable_and_stable():
    receipt = build_decision_receipt(
        query="q",
        candidates=[{"id": "c1", "claim": "one"}],
        decision=decision(),
        policy_fingerprint="policy-v1",
        engine_version="engine-v1",
        memory_snapshot="snapshot-a",
        evidence_ids=["e1"],
    )
    first = explain_receipt(receipt)
    second = explain_receipt(receipt)
    assert first == second
    assert first["decision"] == "SELECT"
    assert first["selected_candidate_ids"] == ["c1"]
    assert first["evidence_ids"] == ["e1"]
    assert first["reason_codes"] == ["RELEVANCE_MATCH"]


def test_receipt_diff_classifies_drift():
    first = build_decision_receipt(
        query="q",
        candidates=[{"id": "c1", "claim": "one"}],
        decision=decision(),
        policy_fingerprint="policy-v1",
        engine_version="engine-v1",
        memory_snapshot="snapshot-a",
    )
    second = build_decision_receipt(
        query="q2",
        candidates=[{"id": "c2", "claim": "two"}],
        decision=decision(),
        policy_fingerprint="policy-v2",
        engine_version="engine-v2",
        memory_snapshot="snapshot-b",
    )
    result = receipt_diff(first, second)
    assert result["reproducible"] is False
    assert set(result["drift"]) == {
        "candidate_drift",
        "policy_drift",
        "engine_drift",
        "snapshot_drift",
        "query_drift",
    }


def test_receipt_detects_candidate_or_decision_drift():
    candidates = [{"id": "c1", "claim": "one"}]
    receipt = build_decision_receipt(
        query="q",
        candidates=candidates,
        decision=decision(),
        policy_fingerprint="policy-v1",
        engine_version="engine-v1",
        memory_snapshot="snapshot-a",
    )
    result = replay_receipt(receipt, candidates=[{"id": "c2", "claim": "two"}], decision=decision())
    assert result["reproducible"] is False


def test_audit_keeps_candidate_fingerprint_and_decision():
    records = []
    result = append_audit(
        records,
        query="q",
        candidates=[{"id": "c1", "claim": "x"}],
        decision=decision(),
        authority_state="current",
        provider="deterministic",
        provider_version="1",
        configuration="none",
        timestamp="2026-01-01T00:00:00Z",
    )
    assert isinstance(result, AuditRecord)
    assert result.candidate_set_fingerprint == fingerprint([{"id": "c1", "claim": "x"}])
    assert result.decision.reason_codes == (ReasonCode.RELEVANCE_MATCH,)
