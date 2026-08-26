"""Statistical confidence for strategy comparison.

Implements paired per-query metric collection, bootstrap confidence
intervals, and paired-difference analysis so strategy comparisons can state
whether observed differences exceed evaluation noise.

Methodology (see note_confidence_intervals corpus doc):
- Per-query metric values are retained rather than only aggregates.
- Bootstrap resampling over queries gives distribution-free CIs for any metric.
- Paired differences between strategies on the same queries cancel query
  difficulty variance, giving tighter intervals than unpaired comparison.
- If a paired-difference interval contains zero, strategies are
  indistinguishable at that sample size; prefer the simpler/faster one.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field


@dataclass
class MetricSamples:
    """Per-query metric samples for one strategy."""

    strategy: str
    recall: dict[int, list[float]] = field(default_factory=dict)
    precision: dict[int, list[float]] = field(default_factory=dict)
    mrr: list[float] = field(default_factory=list)
    ndcg: dict[int, list[float]] = field(default_factory=dict)

    def add(
        self,
        recall: dict[int, float],
        precision: dict[int, float],
        mrr: float,
        ndcg: dict[int, float],
    ) -> None:
        for k, v in recall.items():
            self.recall.setdefault(k, []).append(v)
        for k, v in precision.items():
            self.precision.setdefault(k, []).append(v)
        self.mrr.append(mrr)
        for k, v in ndcg.items():
            self.ndcg.setdefault(k, []).append(v)


def bootstrap_ci(
    samples: list[float],
    statistic=sum,
    n_boot: int = 2000,
    alpha: float = 0.05,
    rng: random.Random | None = None,
) -> tuple[float, float]:
    """Bootstrap confidence interval for the mean of per-query samples.

    Resamples queries with replacement; the mean of each resample forms the
    sampling distribution. Returns the (1-alpha) percentile interval.
    """
    if not samples:
        return (0.0, 0.0)
    rng = rng or random.Random(42)  # deterministic by default
    n = len(samples)
    means = sorted(sum(rng.choices(samples, k=n)) / n for _ in range(n_boot))
    lo_idx = int((alpha / 2) * n_boot)
    hi_idx = int((1 - alpha / 2) * n_boot) - 1
    return (means[lo_idx], means[hi_idx])


def paired_difference_ci(
    samples_a: list[float],
    samples_b: list[float],
    n_boot: int = 2000,
    alpha: float = 0.05,
    rng: random.Random | None = None,
) -> tuple[float, float]:
    """Bootstrap CI for mean(A - B) over paired per-query samples.

    Queries are resampled in pairs, preserving the correlation between
    strategies on the same query. An interval containing 0.0 means the
    strategies are statistically indistinguishable at this sample size.
    """
    assert len(samples_a) == len(samples_b), "paired samples must align"
    diffs = [a - b for a, b in zip(samples_a, samples_b)]
    return bootstrap_ci(diffs, n_boot=n_boot, alpha=alpha, rng=rng)


def is_significant(ci: tuple[float, float]) -> bool:
    """A CI excluding zero indicates a difference larger than noise."""
    lo, hi = ci
    return not (lo <= 0.0 <= hi)


def summarize_samples(samples: list[float]) -> dict[str, float]:
    """Mean/median/stdev summary for a per-query sample list."""
    if not samples:
        return {"mean": 0.0, "median": 0.0, "stdev": 0.0}
    return {
        "mean": statistics.mean(samples),
        "median": statistics.median(samples),
        "stdev": statistics.stdev(samples) if len(samples) > 1 else 0.0,
    }


def format_comparison_table(
    base_samples: MetricSamples,
    other_samples: list[MetricSamples],
    k: int = 3,
    metric: str = "recall",
) -> str:
    """Render a paired-comparison table of one baseline vs other strategies."""
    lines = []
    header = "Strategy".ljust(26) + "mean".rjust(8) + "95% CI".rjust(18) + "vs base".rjust(20)
    lines.append(f"Paired comparison on {metric}@{k}")
    lines.append(header)
    lines.append("-" * len(header))

    def get(ms: MetricSamples) -> list[float]:
        store = getattr(ms, metric)
        return store.get(k, [])

    base = get(base_samples)
    lo, hi = bootstrap_ci(base)
    lines.append(
        base_samples.strategy.ljust(26)
        + f"{statistics.mean(base):.3f}".rjust(8)
        + f"[{lo:.3f},{hi:.3f}]".rjust(18)
        + "-".rjust(20)
    )

    for other in other_samples:
        vals = get(other)
        dlo, dhi = paired_difference_ci(vals, base)
        sig = "*" if is_significant((dlo, dhi)) else " "
        lines.append(
            other.strategy.ljust(26)
            + f"{statistics.mean(vals):.3f}".rjust(8)
            + f"[{bootstrap_ci(vals)[0]:.3f},{bootstrap_ci(vals)[1]:.3f}]".rjust(18)
            + f"{sig}[{dlo:+.3f},{dhi:+.3f}]".rjust(20)
        )
    lines.append("(* = paired difference excludes zero)")
    return "\n".join(lines)
