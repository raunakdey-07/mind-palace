"""Tests for the retrieval quality gate."""

import json

from api.services.confidence import MetricSamples
from api.services.quality_gate import (
    collect_gate_results,
    compare_to_baseline,
    render_gate_report,
)


def _make_result(name: str, recall3: float):
    """Build a minimal StrategyResult-like object."""

    class R:
        pass

    r = R()
    r.name = name
    r.recall = {1: recall3, 3: recall3, 5: recall3, 10: recall3}
    r.precision = {1: 0.3, 3: 0.3, 5: 0.3, 10: 0.3}
    r.ndcg = {1: recall3, 3: recall3, 5: recall3, 10: recall3}
    r.mrr = recall3
    r.latencies_ms = [10.0, 20.0, 30.0]
    r.avg_latency_ms = 20.0
    r.p50_latency_ms = 20.0
    return r


def _make_samples(name: str, vals: list[float]):
    ms = MetricSamples(strategy=name)
    for v in vals:
        ms.add(recall={3: v}, precision={3: 0.3}, mrr=v, ndcg={3: v})
    return ms


def test_collect_gate_results_shape():
    results = {"vector": _make_result("vector", 0.8)}
    samples = {"vector": _make_samples("vector", [0.8, 0.7])}
    gate = collect_gate_results(results, samples)
    assert "vector" in gate["strategies"]
    assert "samples" in gate
    assert "recall" in gate["samples"]["vector"]
    assert "latency_ms" in gate["strategies"]["vector"]


def test_compare_identical_runs_pass():
    vals = [0.8] * 20
    current = {"hybrid+rrf": _make_samples("hybrid+rrf", vals)}
    baseline = {"samples": {"hybrid+rrf": {"recall": {"3": vals}}}}
    report = compare_to_baseline(current, baseline)
    assert report["decision"].startswith("PASS")
    assert not report["regressions"]


def test_compare_clear_regression_fails():
    base_vals = [0.9] * 40
    curr_vals = [0.2] * 40
    current = {"hybrid+rrf": _make_samples("hybrid+rrf", curr_vals)}
    baseline = {"samples": {"hybrid+rrf": {"recall": {"3": base_vals}}}}
    report = compare_to_baseline(current, baseline)
    assert report["decision"].startswith("FAIL")
    assert "hybrid+rrf" in report["regressions"]


def test_noise_does_not_fail_gate():
    import random

    rng = random.Random(11)
    base_vals = [rng.random() for _ in range(60)]
    # small perturbation within noise
    curr_vals = [min(1.0, max(0.0, v + rng.uniform(-0.04, 0.04))) for v in base_vals]
    current = {"s": _make_samples("s", curr_vals)}
    baseline = {"samples": {"s": {"recall": {"3": base_vals}}}}
    report = compare_to_baseline(current, baseline)
    assert report["decision"].startswith("PASS")


def test_unknown_strategy_in_current_is_ignored():
    current = {"new-strategy": _make_samples("new-strategy", [0.5])}
    baseline = {"samples": {}}
    report = compare_to_baseline(current, baseline)
    assert report["comparisons"] == []
    assert report["decision"].startswith("PASS")


def test_render_report_contains_decision():
    gate = collect_gate_results(
        {"vector": _make_result("vector", 0.8)}, {"vector": _make_samples("vector", [0.8])}
    )
    text = render_gate_report(gate, None)
    assert "R@3" in text and "vector" in text


def test_baseline_json_round_trip(tmp_path):
    results = {"vector": _make_result("vector", 0.8)}
    samples = {"vector": _make_samples("vector", [0.8, 0.6])}
    gate = collect_gate_results(results, samples)
    p = tmp_path / "baseline.json"
    p.write_text(json.dumps(gate))

    loaded = json.loads(p.read_text())
    current = {"vector": _make_samples("vector", [0.8, 0.6])}
    report = compare_to_baseline(current, loaded)
    assert report["decision"].startswith("PASS")
