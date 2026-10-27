"""M017 Phase 6: 4-way agency readiness benchmark.

Executes the same brand-question suite under four retrieval conditions and
asserts the acceptance metrics: brand-rule recall >= 95%, zero prohibited
claims, zero superseded-policy errors, zero unapproved brainstorming leaks,
zero cross-brand leakage, and relevant evidence availability >= 90%.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.agency_readiness.evaluate import run_evaluation


@pytest.mark.asyncio
async def test_agency_readiness_acceptance_metrics(tmp_path: Path):
    result = await run_evaluation(tmp_path)
    metrics = result["metrics"]

    assert metrics["rule_recall"] >= 0.95, metrics
    assert metrics["prohibited_violations"] == 0, metrics
    assert metrics["superseded_errors"] == 0, metrics
    assert metrics["brainstorm_leaks"] == 0, metrics
    assert metrics["cross_brand_leaks"] == 0, metrics
    assert metrics["evidence_availability"] >= 0.90, metrics
