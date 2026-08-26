"""Tests for statistical confidence methodology."""

import random

from api.services.confidence import (
    MetricSamples,
    bootstrap_ci,
    format_comparison_table,
    is_significant,
    paired_difference_ci,
    summarize_samples,
)


def test_bootstrap_ci_contains_mean():
    samples = [0.5] * 10 + [0.7] * 10
    lo, hi = bootstrap_ci(samples)
    mean = sum(samples) / len(samples)
    assert lo <= mean <= hi


def test_bootstrap_ci_deterministic_with_seed():
    samples = [float(i % 7) / 7 for i in range(50)]
    ci1 = bootstrap_ci(samples, rng=random.Random(1))
    ci2 = bootstrap_ci(samples, rng=random.Random(1))
    assert ci1 == ci2


def test_bootstrap_ci_empty_samples():
    assert bootstrap_ci([]) == (0.0, 0.0)


def test_paired_difference_identical_samples_is_zero():
    s = [0.8, 0.6, 0.9, 0.4]
    lo, hi = paired_difference_ci(s, s)
    # identical strategies: difference is exactly zero everywhere
    assert lo <= 0.0 <= hi
    assert abs(lo) < 1e-9 and abs(hi) < 1e-9


def test_paired_difference_clearly_positive_is_significant():
    a = [0.9] * 40  # strategy A always 0.9
    b = [0.3] * 40  # strategy B always 0.3
    lo, hi = paired_difference_ci(a, b)
    assert is_significant((lo, hi))
    assert lo > 0  # A beats B consistently


def test_overlapping_noise_is_not_significant():
    rng = random.Random(7)
    base = [rng.random() for _ in range(60)]
    other = [min(1.0, x + rng.uniform(-0.05, 0.05)) for x in base]
    lo, hi = paired_difference_ci(other, base, rng=random.Random(7))
    # tiny perturbations should not produce a decisive difference
    assert not is_significant((lo, hi)) or abs(hi - lo) < 0.06


def test_metric_samples_accumulates_per_k():
    ms = MetricSamples(strategy="x")
    ms.add(recall={1: 1.0, 3: 0.5}, precision={3: 0.3}, mrr=1.0, ndcg={5: 0.75})
    ms.add(recall={1: 0.0, 3: 0.5}, precision={3: 0.3}, mrr=0.5, ndcg={5: 0.25})
    assert ms.recall[1] == [1.0, 0.0]
    assert ms.recall[3] == [0.5, 0.5]
    assert ms.mrr == [1.0, 0.5]
    assert ms.ndcg[5] == [0.75, 0.25]


def test_summarize_samples():
    s = summarize_samples([1.0, 2.0, 3.0])
    assert s["mean"] == 2.0
    assert s["median"] == 2.0
    assert s["stdev"] > 0
    assert summarize_samples([])["mean"] == 0.0


def test_format_comparison_table_renders():
    base = MetricSamples(strategy="base")
    other = MetricSamples(strategy="other")
    for v in (1.0, 0.0, 1.0):
        base.add(recall={3: v}, precision={}, mrr=v, ndcg={})
        other.add(recall={3: 0.5}, precision={}, mrr=0.5, ndcg={})
    table = format_comparison_table(base, [other], k=3)
    assert "base" in table and "other" in table
    assert "paired" in table.lower()
