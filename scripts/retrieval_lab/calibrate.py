"""Fit the abstain/answer rule on dev, and report the curve rather than a number.

    python scripts/retrieval_lab/calibrate.py --out calibration.json

## The question

Phase 3 asks whether a small embedder can be made *reliable*. Reliability is not
recall; it is the joint condition that when the system answers it is right, and
when it is unsure it says so. So this fits the rule that trades those off, using
only features the product can actually compute at query time, and then reports
the whole trade-off curve.

**Fitted on dev. Never on held-out.** Held-out v1 is burned and v4 does not
exist, so there is nothing here to validate on, and that is stated rather than
glossed: these coefficients are a *hypothesis about the shape of the rule*, not
a measured operating point.

## Why a logistic regression and not a hand-written gate

Because the shipped gate is four hand-tuned numbers (`minimum`, `strong`,
`relative`, `max_topics`) that nobody fitted to anything. If a fitted rule on the
*same features* cannot beat it, then the features are the limit and a better gate
is not the answer. If it can, the gate is the limit. Those are very different
conclusions and this is the cheapest way to tell them apart.

No sklearn: a dependency added to fit six coefficients on 202 rows would be a
worse trade than writing thirty lines of gradient descent, and it would have to
be installed by every developer to reproduce a number in a document.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from retrieval_lab import arms as arm_module  # noqa: E402
from retrieval_lab import metrics as M  # noqa: E402
from retrieval_lab.corpus import load_dev  # noqa: E402

FEATURES = (
    "cosine_top1",  # arm C's best score
    "cosine_margin",  # top1 - top2: how lopsided the field is
    "bm25_top1",
    "bm25_margin",
    "rrf_top1",  # rank-derived, so uncalibrated in magnitude
    "cross_encoder_top1",  # arm E's score, from the shipped reranker
    "cross_encoder_margin",
    "claim_backed",  # 1 if the top passage carries an authored claim
    "agreement",  # 1 if arms B, C and E name the same top key
)

#: Features that are not on a 0..1 scale are mapped through the empirical CDF
#: fitted on dev. Rank-based fusion and BM25 have no natural ceiling, and a
#: logistic fit over a raw BM25 score would be dominated by its scale.
_TO_RANK = {"bm25_top1", "bm25_margin", "rrf_top1", "cross_encoder_top1", "cross_encoder_margin"}


def build_rows(corpus, questions):
    index = arm_module._Index(corpus)
    bm25 = arm_module._BM25(index.claim_token_lists)
    M.attach_gold({q.qid: q.key for q in questions})

    raw: list[list[float]] = []
    labels: list[int] = []
    detail: list[dict] = []
    ranked: list[list[str]] = []

    for question in questions:
        b = arm_module.arm_b(index, question, corpus, bm25)
        c = arm_module.arm_c(index, question, corpus)
        d = arm_module.arm_d(index, question, corpus, bm25)
        e = arm_module.arm_e(index, question, corpus, bm25)
        f = arm_module.arm_f(index, question, corpus)

        def top2(run) -> tuple[float, float]:
            scores = run.scores or [0.0, 0.0]
            first = scores[0]
            second = scores[1] if len(scores) > 1 else 0.0
            return first, max(0.0, first - second)

        c1, cm = top2(c)
        b1, bm = top2(b)
        e1, em = top2(e)
        agreement = 1.0 if (b.keys[:1] == c.keys[:1] == e.keys[:1] and b.keys) else 0.0
        raw.append(
            [
                c1,
                cm,
                b1,
                bm,
                d.scores[0] if d.scores else 0.0,
                e1,
                em,
                0.0 if f.unbacked_passage else 1.0,
                agreement,
            ]
        )
        labels.append(1 if question.answerable else 0)
        detail.append(
            {"qid": question.qid, "slice": question.slice, "answerable": question.answerable}
        )
        # The ranking comes from arm E. The *decision* to answer comes from the
        # fitted probability, and the two are independent: the first says what
        # the answer would be, the second whether to trust it. Scoring a run
        # whose `keys` were left empty measures nothing, because with no keys
        # nothing is ever "correct" and the whole curve reads as zero.
        ranked.append(e.keys[:5])

    matrix = np.asarray(raw, dtype=np.float64)
    # Empirical CDF per feature, fitted on dev and applied to dev. Rank transform
    # only; no distribution is assumed and no test set is involved.
    ranks = np.zeros_like(matrix)
    for column, name in enumerate(FEATURES):
        order = np.argsort(matrix[:, column], kind="stable")
        values = matrix[order, column]
        positions = np.arange(len(values), dtype=np.float64)
        # Average ranks for ties, so a column of identical values maps to a
        # constant and contributes nothing rather than contributing noise.
        unique, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
        means = np.zeros(len(unique))
        np.add.at(means, inverse, positions)
        means /= counts
        mapped = np.empty(len(values))
        mapped[order] = means[inverse] / max(1, len(values) - 1)
        ranks[:, column] = mapped if name in _TO_RANK else matrix[:, column]
    return ranks, np.asarray(labels, dtype=np.float64), detail, ranked


def fit(X: np.ndarray, y: np.ndarray, iterations: int = 4000, lr: float = 0.35) -> np.ndarray:
    """Logistic regression by gradient descent with L2, standardised features.

    Plain, and slow, and that is fine: 202 rows by 9 features converges in a few
    seconds and the answer is reproducible without a solver's version history.
    """
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    Z = (X - mean) / std
    Z = np.hstack([np.ones((len(Z), 1)), Z])
    weights = np.zeros(Z.shape[1])
    penalty = np.concatenate([[0.0], np.ones(Z.shape[1] - 1)])
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-Z @ weights))
        gradient = Z.T @ (p - y) / len(y) + 0.05 * penalty * weights
        weights -= lr * gradient
    return weights


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    corpus, questions = load_dev()
    X, y, detail, ranked = build_rows(corpus, questions)
    weights = fit(X, y)
    probability = 1.0 / (1.0 + np.exp(-(np.hstack([np.ones((len(X), 1)), X]) @ weights)))

    runs: list[M.Run] = []
    for index, row in enumerate(detail):
        runs.append(
            M.Run(
                qid=row["qid"],
                slice=row["slice"],
                answerable=row["answerable"],
                keys=ranked[index],
                scores=[float(probability[index])],
            )
        )
    M.attach_gold({q.qid: q.key for q in questions})

    curve = M.risk_coverage(runs, 21)
    best = {
        "conf_wrong_2pct_max_coverage": max(
            (
                p["coverage"]
                for p in curve
                if p["accuracy"] is not None and 1 - p["accuracy"] <= 0.02
            ),
            default=0.0,
        ),
        "conf_wrong_5pct_max_coverage": max(
            (
                p["coverage"]
                for p in curve
                if p["accuracy"] is not None and 1 - p["accuracy"] <= 0.05
            ),
            default=0.0,
        ),
    }
    artifact = {
        "features": list(FEATURES),
        "coefficients": {
            "intercept": round(float(weights[0]), 4),
            **{name: round(float(value), 4) for name, value in zip(FEATURES, weights[1:])},
        },
        "fit_on": "dev (202 questions). Never on held-out.",
        "n": len(questions),
        "answerable": int(y.sum()),
        "ceiling": best,
        "risk_coverage": curve,
        "comparison": {
            "shipped_gate_at_70pct_coverage": {
                k: v
                for k, v in json.loads(
                    (ROOT / "docs/performance/retrieval-lab-dev.json").read_text()
                )["arms"]["A"]["overall"]["at_70pct_coverage"].items()
                if k
                in (
                    "correct",
                    "wrong",
                    "false_answer",
                    "abstained_when_answerable",
                    "wrong_answer_rate",
                )
            },
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print("coefficients:")
    for name, value in artifact["coefficients"].items():
        print(f"  {name:24} {value:+.4f}")
    print(f"max coverage at <=2% confident-wrong: {best['conf_wrong_2pct_max_coverage']*100:.1f}%")
    print(f"max coverage at <=5% confident-wrong: {best['conf_wrong_5pct_max_coverage']*100:.1f}%")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
