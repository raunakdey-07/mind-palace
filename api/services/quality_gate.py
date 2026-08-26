# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Retrieval quality gate: machine-readable benchmark output for CI.

Runs the benchmark, emits JSON results with per-strategy metrics and
latency, and — when a baseline file is provided — computes paired
differences against it to detect statistically meaningful regressions.

Usage:
    # produce a baseline (commit this file)
    python -m cli.main eval gate --save baseline.json

    # in CI: compare against the committed baseline
    python -m cli.main eval gate --baseline baseline.json

Exit code is non-zero only when a statistically significant regression is
detected (paired-difference CI excludes zero on the negative side). Noise
never fails the build.
"""

from __future__ import annotations

import statistics

from api.services.confidence import is_significant, paired_difference_ci


def collect_gate_results(results, samples) -> dict:
    """Build the machine-readable quality-gate payload.

    Includes per-query metric samples so a later run can compute paired
    differences against this baseline query-by-query.
    """
    strategies = {}
    per_query = {}
    for name, sr in results.items():
        lat = sorted(sr.latencies_ms)
        p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
        strategies[name] = {
            "recall": {str(k): round(v, 4) for k, v in sr.recall.items()},
            "precision": {str(k): round(v, 4) for k, v in sr.precision.items()},
            "ndcg": {str(k): round(v, 4) for k, v in sr.ndcg.items()},
            "mrr": round(sr.mrr, 4),
            "latency_ms": {
                "avg": round(sr.avg_latency_ms, 1),
                "p50": round(sr.p50_latency_ms, 1),
                "p95": round(p95, 1),
            },
        }
        ms = samples.get(name)
        if ms:
            per_query[name] = {
                "recall": {str(k): vals for k, vals in ms.recall.items()},
                "mrr": ms.mrr,
                "ndcg": {str(k): vals for k, vals in ms.ndcg.items()},
            }
    return {"strategies": strategies, "samples": per_query}


def _metric_series(samples, strategy: str, metric: str, k: int) -> list[float]:
    store = getattr(samples[strategy], metric)
    return store.get(k, [])


def compare_to_baseline(
    samples,
    baseline: dict,
    k: int = 3,
    metric: str = "recall",
    alpha: float = 0.05,
) -> dict:
    """Paired comparison of current run against a saved baseline.

    The baseline stores per-query samples keyed by strategy so the paired
    difference is computed query-by-query.
    """
    report: dict = {"regressions": [], "comparisons": []}

    base_samples = baseline.get("samples", {})
    for name, current in samples.items():
        if name not in base_samples:
            continue
        base_vals = base_samples[name].get(metric, {}).get(str(k), [])
        curr_vals = _metric_series_from(current, metric, k)
        if not base_vals or not curr_vals:
            continue

        n = min(len(base_vals), len(curr_vals))
        lo, hi = paired_difference_ci(curr_vals[:n], base_vals[:n], alpha=alpha)
        entry = {
            "strategy": name,
            "metric": f"{metric}@{k}",
            "baseline_mean": round(statistics.mean(base_vals[:n]), 4),
            "current_mean": round(statistics.mean(curr_vals[:n]), 4),
            "paired_diff_ci": [round(lo, 4), round(hi, 4)],
            "significant_regression": is_significant((lo, hi)) and hi < 0,
        }
        report["comparisons"].append(entry)
        if entry["significant_regression"]:
            report["regressions"].append(name)

    report["decision"] = (
        f"FAIL — significant regression in: {', '.join(report['regressions'])}"
        if report["regressions"]
        else "PASS — no statistically significant regression"
    )
    return report


def _metric_series_from(sample_obj, metric: str, k: int) -> list[float]:
    store = getattr(sample_obj, metric)
    return store.get(k, [])


def render_gate_report(gate: dict, comparison: dict | None) -> str:
    lines = ["[GATE] Quality gate results", ""]
    for name, s in gate["strategies"].items():
        r3 = s["recall"].get("3", 0.0)
        mrr = s["mrr"]
        lat = s["latency_ms"]["avg"]
        lines.append(f"  {name.ljust(24)} R@3={r3:.3f}  MRR={mrr:.3f}  avg={lat:.0f}ms")

    if comparison:
        lines.append("")
        for c in comparison["comparisons"]:
            lo, hi = c["paired_diff_ci"]
            flag = "REGRESSION" if c["significant_regression"] else "ok"
            lines.append(
                f"  {c['strategy'].ljust(24)} {c['metric']}: "
                f"{c['baseline_mean']:.3f} -> {c['current_mean']:.3f} "
                f"CI[{lo:+.3f},{hi:+.3f}] {flag}"
            )
        lines.append("")
        lines.append(f"DECISION: {comparison['decision']}")
    return "\n".join(lines)
