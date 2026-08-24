"""Tests for evaluation metrics and benchmark loading."""

import pytest

from api.services.evaluation import (
    EvaluationService,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

# --- Recall@K ---


def test_recall_all_found():
    assert recall_at_k(["a", "b"], ["a", "b", "c"], 3) == 1.0


def test_recall_partial():
    assert recall_at_k(["a", "b"], ["a", "x", "y"], 3) == 0.5


def test_recall_none_found():
    assert recall_at_k(["a"], ["x", "y"], 2) == 0.0


def test_recall_outside_k_ignored():
    # relevant doc at rank 4, k=3 -> not counted
    assert recall_at_k(["a"], ["x", "y", "z", "a"], 3) == 0.0
    assert recall_at_k(["a"], ["x", "y", "z", "a"], 4) == 1.0


def test_recall_empty_expected():
    assert recall_at_k([], ["x"], 1) == 0.0


def test_recall_zero_results():
    assert recall_at_k(["a"], [], 5) == 0.0


def test_recall_duplicate_expected_deduped():
    # duplicate 'a' should not inflate the denominator
    assert recall_at_k(["a", "a"], ["a"], 1) == 1.0


# --- Precision@K ---


def test_precision_perfect():
    assert precision_at_k(["a"], ["a", "b", "c"], 3) == pytest.approx(1 / 3)


def test_precision_all_relevant():
    assert precision_at_k(["a", "b"], ["a", "b"], 2) == 1.0


def test_precision_none_relevant():
    assert precision_at_k(["a"], ["x", "y"], 2) == 0.0


def test_precision_empty_results():
    assert precision_at_k(["a"], [], 5) == 0.0


def test_precision_no_expected_still_measures_noise():
    # no relevant docs exist; every retrieved item is a false positive
    assert precision_at_k([], ["x", "y"], 2) == 0.0


# --- MRR ---


def test_rr_rank_one():
    assert reciprocal_rank(["a"], ["a", "x"]) == 1.0


def test_rr_rank_three():
    assert reciprocal_rank(["a"], ["x", "y", "a"]) == pytest.approx(1 / 3)


def test_rr_not_found():
    assert reciprocal_rank(["a"], ["x", "y"]) == 0.0


def test_rr_empty_retrieved():
    assert reciprocal_rank(["a"], []) == 0.0


# --- nDCG ---


def test_ndcg_perfect_ordering():
    assert ndcg_at_k(["a", "b"], ["a", "b", "c"], 3) == 1.0


def test_ndcg_imperfect_ordering():
    score = ndcg_at_k(["a"], ["x", "a"], 2)
    assert 0.0 < score < 1.0


def test_ndcg_not_found():
    assert ndcg_at_k(["a"], ["x", "y"], 2) == 0.0


# --- Benchmark loading ---


def test_load_benchmarks_valid(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text(
        "- query: q1\n  expected: [docA]\n  category: factual\n- query: q2\n  expected: [docB]\n"
    )
    data = EvaluationService.load_benchmarks(str(f))
    assert len(data) == 2
    assert data[0]["category"] == "factual"
    assert data[1]["category"] == "general"  # default applied


def test_load_benchmarks_missing_query(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("- expected: [docA]\n")
    with pytest.raises(ValueError, match="query"):
        EvaluationService.load_benchmarks(str(f))


def test_load_benchmarks_missing_expected(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("- query: q1\n")
    with pytest.raises(ValueError, match="expected"):
        EvaluationService.load_benchmarks(str(f))


def test_load_benchmarks_not_a_list(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("key: value\n")
    with pytest.raises(ValueError, match="list"):
        EvaluationService.load_benchmarks(str(f))


def test_load_benchmarks_duplicate_query(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("- query: q1\n  expected: [a]\n- query: q1\n  expected: [b]\n")
    with pytest.raises(ValueError, match="Duplicate"):
        EvaluationService.load_benchmarks(str(f))


def test_load_benchmarks_dedupes_expected(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("- query: q1\n  expected: [docA, docA]\n")
    data = EvaluationService.load_benchmarks(str(f))
    assert data[0]["expected"] == ["docA"]


def test_load_benchmarks_malformed_entry_type(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("- just_a_string\n")
    with pytest.raises(ValueError, match="mapping"):
        EvaluationService.load_benchmarks(str(f))
