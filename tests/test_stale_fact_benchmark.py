"""Head-to-head stale-fact and ADR benchmark tests."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")


def test_dataset_shape():
    from scripts.benchmark_stale_facts import DOCS, QUESTIONS

    assert 12 <= len(DOCS) <= 20
    assert 32 <= len(QUESTIONS) <= 40
    cats = {q[3] for q in QUESTIONS}
    assert {"A", "B", "C"} <= cats


def test_benchmark_runs_and_metrics_valid():
    from scripts.benchmark_stale_facts import run_benchmark

    result = run_benchmark()
    assert "mind_palace" in result
    assert "plain_rag" in result
    assert "rag_recency" in result
    assert "rag_metadata" in result
    for name in ("mind_palace", "plain_rag", "rag_recency", "rag_metadata"):
        m = result[name]
        for key in (
            "authoritative_top1_accuracy",
            "stale_contamination_rate",
            "compound_completeness",
            "abstention_accuracy",
            "cached_revalidation_accuracy",
        ):
            assert key in m
            assert 0.0 <= m[key] <= 1.0
    mp = result["mind_palace"]
    assert mp["stale_cache_miss_rate"] == 0.0
