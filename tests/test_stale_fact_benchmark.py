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


def test_benchmark_runs_and_mind_palace_is_superior():
    from scripts.benchmark_stale_facts import run_benchmark

    result = run_benchmark()
    mp = result["mind_palace"]
    base = result["plain_rag"]
    assert mp["authoritative_top1_accuracy"] >= 0.9
    assert mp["stale_cache_miss_rate"] == 0.0
    assert mp["cached_revalidation_accuracy"] == 1.0
    assert mp["cached_revalidation_f1"] == 1.0
    assert mp["authoritative_top1_accuracy"] > base["authoritative_top1_accuracy"]
