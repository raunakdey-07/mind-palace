from api.services.memory_decision_benchmark import (
    build_review_queue,
    compare_runs,
    run_provider,
)
from api.services.memory_decision_spike import DeterministicDecisionProvider


def test_benchmark_preserves_same_candidate_rows_and_unknown_labels():
    result = run_provider(DeterministicDecisionProvider(), provider_name="baseline")
    assert result["candidate_rows"] == 412
    assert result["question_rows"] == 40
    assert any(row["label_status"] == "unknown" for row in result["rows"])
    assert all("candidate_count" in row["trace"]["candidate_features"] for row in result["rows"])


def test_disagreement_classification_and_queue_are_explicit():
    baseline = {
        "rows": [
            {"question_id": "q1", "trace": {"decision": "abstain"}},
            {"question_id": "q2", "trace": {"decision": "select"}},
        ]
    }
    alternate = {
        "rows": [
            {"question_id": "q1", "trace": {"decision": "select"}},
            {"question_id": "q2", "trace": {"decision": "abstain"}},
        ]
    }
    comparison = compare_runs(baseline, alternate)
    assert comparison["counts"]["BASELINE_ABSTAIN_JEV_SELECT"] == 1
    assert comparison["counts"]["BASELINE_SELECT_JEV_ABSTAIN"] == 1
    assert build_review_queue(comparison)[0]["type"] == "BASELINE_ABSTAIN_JEV_SELECT"
