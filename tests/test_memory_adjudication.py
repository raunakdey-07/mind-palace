import json

import pytest

from api.services.memory_adjudication import (
    AdjudicationRecord,
    blind_review_payload,
    dataset_hash,
    generate_blind_review_set,
    record_hash,
    validate_records,
)
from api.services.memory_decision_dataset import load_development_examples


def record(**overrides):
    values = {
        "adjudication_id": "adj-001",
        "dataset_version": "m007-adjudicated-v0.1",
        "question_id": "question-001",
        "candidate_id": "claim-001",
        "reviewer_id": "reviewer-01",
        "reviewed_at": "2026-09-24T00:00:00Z",
        "relevance": "relevant",
        "subject_compatibility": "compatible",
        "temporal_applicability": "not_temporal",
        "evidence_sufficiency": "sufficient",
        "authority_compatibility": "current",
        "overall_decision": "accept",
        "rationale": "The claim and evidence address the question.",
        "source_reference": "stage-a/example.md",
    }
    values.update(overrides)
    return AdjudicationRecord(**values)


def test_required_dimensions_and_unknown_are_preserved():
    item = record(evidence_sufficiency="uncertain", overall_decision="abstain")
    validate_records([item])
    assert item.evidence_sufficiency == "uncertain"
    assert item.overall_decision == "abstain"


def test_invalid_enum_and_empty_provenance_fail():
    with pytest.raises(ValueError):
        record(relevance="negative").validate()
    with pytest.raises(ValueError):
        record(rationale="").validate()


def test_hash_is_timestamp_independent():
    first = record()
    second = record(reviewed_at="2030-01-01T00:00:00Z")
    assert record_hash(first) == record_hash(second)
    assert dataset_hash([first]) == dataset_hash([second])


def test_blind_review_set_is_deterministic_and_has_no_resolver_fields():
    examples = load_development_examples()
    first = generate_blind_review_set(examples, target_size=120)
    second = generate_blind_review_set(examples, target_size=120)
    assert [blind_review_payload(item) for item in first] == [
        blind_review_payload(item) for item in second
    ]
    assert 100 <= len(first) <= 120
    assert {item.sampling_stratum for item in first} == {
        "authored_expected_claim_review",
        "structural_negative_review",
        "unknown_abstention_review",
    }
    assert len({item.review_id for item in first}) == len(first)
    assert all("selected_id" not in blind_review_payload(item) for item in first)
    assert all("sampling_stratum" not in blind_review_payload(item) for item in first)
    assert all("score" not in blind_review_payload(item) for item in first)


def test_schema_is_machine_readable():
    schema = json.loads(
        (
            __import__("pathlib").Path(__file__).parents[1]
            / "eval"
            / "m007"
            / "adjudication"
            / "schema.json"
        ).read_text()
    )
    assert "adjudication_id" in schema["record_fields"]
    assert "unknown" in schema["enums"]["relevance"] or "uncertain" in schema["enums"]["relevance"]
