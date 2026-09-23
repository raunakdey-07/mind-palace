from api.services.memory_evaluation_lab import run_all


def test_semantic_oracle_suites_are_explicitly_synthetic():
    result = run_all()
    assert result["label"] == "SYNTHETIC_SEMANTIC_VALIDATION"
    assert result["scientific_status"] == "NOT_HUMAN_EVALUATION"
    assert result["cases"] == result["passed"]
    assert {suite["suite"] for suite in result["suites"]} == {
        "TEMPORAL_SEMANTIC_ORACLE",
        "LONGITUDINAL_SEMANTIC_ORACLE",
        "CONFLICT_SEMANTIC_ORACLE",
        "SECURITY_SEMANTIC_ORACLE",
        "EVIDENCE_INTEGRITY_ORACLE",
    }
