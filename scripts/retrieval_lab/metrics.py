"""Metrics for ranking quality, abstention quality, and the curve between them.

## Why these and not accuracy

A retriever has two jobs that pull in opposite directions: **find the answer**,
and **stay silent when there is none**. Reporting only the first turns a system
that answers every question perfectly on the ones it was shown into something an
integrator must not deploy. So every arm here is reported as a curve, not a
point, and the two numbers that matter most are the two ends of it:

* **confident-wrong rate** -- answered, and wrong. The failure that damages
  trust, because it is the one a user cannot detect.
* **coverage** -- the share of answerable questions it was willing to answer.
  Every reduction in confident-wrong is paid for here, and a report that gives
  the first without the second is an advertisement.

## Deterministic, and stated in advance

Grading is exact key match. There is no LLM judge anywhere in this lab, no
model, and no temperature; a number here can be recomputed from the artifact on
any machine. That is a deliberate limit: it cannot tell you whether a returned
claim is a *good* answer, only whether it is the right one. The end-to-end
benchmark measures the other thing, and the two disagree often enough to be
worth measuring separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Run:
    """One arm's answer to one question.

    `keys` is the ranked list of authored keys, best first. `scores` parallels
    it and is what the abstention rule reads. `passages` is the ranked passage
    list, kept separately because a retriever can rank the right passage under
    the wrong key and the reverse, and conflating them hides both.
    """

    qid: str
    slice: str
    answerable: bool
    keys: list[str]
    scores: list[float]
    passages: list[str] = field(default_factory=list)
    #: Which rung produced the top result: "claim" or "passage". Arm F needs
    #: this to keep L1 authority intact, and the report needs it to say how often
    #: an answer came from body text that carries no claim at all.
    basis: str = "claim"
    #: True when the top result came from a passage with no authored claim.
    unbacked_passage: bool = False


def _rank_of(ranked: list[str], gold: str | None) -> int | None:
    """1-based rank of `gold`, or None if it is absent. Duplicates do not count."""
    if gold is None:
        return None
    for index, value in enumerate(ranked, start=1):
        if value == gold:
            return index
    return None


def _mrr(runs: list[Run], ks: tuple[int, ...]) -> dict[str, float]:
    """Reciprocal rank, and its truncated variants.

    Truncated MRR@k is reported alongside recall@k because they answer different
    questions: recall@k asks "is it in the list", MRR asks "is it first". A
    system can have excellent recall@5 and a useless MRR@5, and that system is
    worse than its recall alone suggests.
    """
    total = 0.0
    by_k = {f"mrr@{k}": 0.0 for k in ks}
    n = 0
    for run in runs:
        if not run.answerable:
            continue
        n += 1
        rank = _rank_of(run.keys, _gold_key(run))
        if rank is not None:
            total += 1.0 / rank
        for k in ks:
            if rank is not None and rank <= k:
                by_k[f"mrr@{k}"] += 1.0 / rank
    if not n:
        return {"mrr": 0.0, **by_k}
    return {"mrr": total / n, **{key: value / n for key, value in by_k.items()}}


#: `Run` does not carry the gold; it is attached by the scorer so a run file is
#: self-describing and a metric bug cannot silently mislabel a hit as a miss.
_GOLD: dict[str, str | None] = {}


def attach_gold(gold_by_qid: dict[str, str | None]) -> None:
    _GOLD.clear()
    _GOLD.update(gold_by_qid)


def _gold_key(run: Run) -> str | None:
    return _GOLD.get(run.qid)


def recall_at(runs: list[Run], ks: tuple[int, ...] = (1, 3, 5)) -> dict[str, float]:
    """Claim-level and passage-level recall@k.

    Passage-level gold is every passage containing the answer's claim text. A
    passage hit with the wrong key is still partial evidence the system found
    the sentence, and separating the two levels is what tells a vocabulary
    problem apart from a representation problem.
    """
    out: dict[str, float] = {}
    answerable = [r for r in runs if r.answerable]
    n = len(answerable)
    if not n:
        return {f"recall@{k}": 0.0 for k in ks} | {f"passage_recall@{k}": None for k in ks}
    for k in ks:
        hits = sum(1 for r in answerable if (_rank_of(r.keys, _gold_key(r)) or 10**9) <= k)
        out[f"recall@{k}"] = hits / n
        # Averaged over the answerable questions that HAVE passage gold, not over
        # all answerable questions. Dividing by the larger number would let a
        # question with no gold passage dilute the score of ones that do, which
        # is a measurement artefact wearing the costume of a lower score.
        with_passage_gold = [r for r in answerable if _GOLD_PASSAGES.get(r.qid)]
        if with_passage_gold:
            phits = sum(
                1 for r in with_passage_gold if set(r.passages[:k]) & set(_GOLD_PASSAGES[r.qid])
            )
            out[f"passage_recall@{k}"] = round(phits / len(with_passage_gold), 4)
        else:
            out[f"passage_recall@{k}"] = None
    return out


_GOLD_PASSAGES: dict[str, list[str]] = {}


def attach_gold_passages(passages_by_qid: dict[str, list[str]]) -> None:
    _GOLD_PASSAGES.clear()
    _GOLD_PASSAGES.update(passages_by_qid)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Used instead of normal-approximation because the
    confident-wrong cells here are single-digit counts, where the normal
    interval produces a lower bound below zero and reads as more certain than
    it is."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def answerable_separation_auroc(runs: list[Run]) -> float:
    """How well the score separates "there is an answer" from "there is not".

    AUROC, not accuracy, because the decision threshold is exactly what is being
    contested: a system can have a poor AUROC and still work at its chosen
    operating point, and it can have a perfect AUROC and still ship the wrong
    threshold. Both numbers are needed; AUROC alone is not a decision.

    Ranking statistic, so ties count as half. Computed by the rank-sum identity
    rather than by pairwise comparison: O(n log n) instead of O(n^2), which
    matters because this is recomputed per arm per slice.
    """
    positives = [r.scores[0] for r in runs if r.answerable and r.scores]
    negatives = [r.scores[0] for r in runs if not r.answerable and r.scores]
    if not positives or not negatives:
        return float("nan")
    ordered = sorted([(s, 1) for s in positives] + [(s, 0) for s in negatives], key=lambda t: t[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[index][0]:
            end += 1
        mean_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            if ordered[position][1] == 1:
                rank_sum += mean_rank
        index = end + 1
    n_pos, n_neg = len(positives), len(negatives)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _threshold_for_coverage(runs: list[Run], coverage: float) -> float:
    """The score threshold that answers `coverage` of the answerable questions."""
    scores = sorted((r.scores[0] for r in runs if r.answerable and r.scores), reverse=True)
    if not scores:
        return float("inf")
    index = max(0, min(len(scores) - 1, int(round(coverage * len(scores))) - 1))
    return scores[index]


def risk_coverage(runs: list[Run], points: int = 11) -> list[dict]:
    """Accuracy against coverage, answered in decreasing score order.

    This is the shape of the trade-off rather than a single point on it. A
    system whose curve is flat and high is reliable; one whose curve collapses
    after the first few percent is not, however good its peak looks.
    """
    answerable = [r for r in runs if r.answerable]
    absent = [r for r in runs if not r.answerable]
    if not answerable:
        return []
    curve: list[dict] = []
    for step in range(points):
        coverage = (step + 1) / points
        threshold = _threshold_for_coverage(runs, coverage)
        answered_able = [r for r in answerable if r.scores and r.scores[0] >= threshold]
        right = sum(1 for r in answered_able if _rank_of(r.keys, _gold_key(r)) == 1)
        answered_absent = [r for r in absent if r.scores and r.scores[0] >= threshold]
        wrong_on_absent = len(answered_absent)
        curve.append(
            {
                "target_coverage": round(coverage, 3),
                "threshold": round(threshold, 4),
                "answered": len(answered_able) + wrong_on_absent,
                "answerable_answered": len(answered_able),
                "coverage": round(len(answered_able) / len(answerable), 4),
                "accuracy": round(right / len(answered_able), 4) if answered_able else None,
                "wrong_on_absent": wrong_on_absent,
                "false_answer_rate": (round(wrong_on_absent / len(absent), 4) if absent else None),
            }
        )
    return curve


def wrong_answer_rate(runs: list[Run], threshold: float) -> dict:
    """The four-way breakdown at one threshold, which is the only honest way to
    report an abstain/answer rule.

    Two distinct wrongs, and merging them is the most common way a retrieval
    report flatters itself:

    * **wrong** -- answered an answerable question with something else.
    * **false answer** -- answered a question whose answer is not in the corpus.

    The second is the one that produces confident nonsense, because the system
    had no way to know it was wrong.
    """
    counts = {
        "correct": 0,
        "abstained_when_answerable": 0,
        "wrong": 0,
        "abstained_correctly": 0,
        "false_answer": 0,
    }
    for run in runs:
        answered = bool(run.scores) and run.scores[0] >= threshold
        if run.answerable:
            if not answered:
                counts["abstained_when_answerable"] += 1
            elif _rank_of(run.keys, _gold_key(run)) == 1:
                counts["correct"] += 1
            else:
                counts["wrong"] += 1
        else:
            if answered:
                counts["false_answer"] += 1
            else:
                counts["abstained_correctly"] += 1
    total = len(runs)
    answered_total = counts["correct"] + counts["wrong"] + counts["false_answer"]
    wrong_total = counts["wrong"] + counts["false_answer"]
    low, high = wilson(wrong_total, answered_total) if answered_total else (0.0, 0.0)
    return {
        **counts,
        "n": total,
        "answered": answered_total,
        "wrong_answer_rate": round(wrong_total / answered_total, 4) if answered_total else None,
        "confident_wrong_wilson95": [round(low, 4), round(high, 4)],
    }


def confident_wrong_rate(runs: list[Run], threshold: float) -> dict:
    """Alias kept separate because it is the number the report leads with."""
    result = wrong_answer_rate(runs, threshold)
    return {
        "threshold": threshold,
        "confident_wrong_rate": result["wrong_answer_rate"],
        "wilson95": result["confident_wrong_wilson95"],
        "answered": result["answered"],
    }


def metrics_for_run(runs: list[Run], threshold: float) -> dict:
    """Everything for one arm over one set of runs, in one dict."""
    answerable = [r for r in runs if r.answerable]
    return {
        "n": len(runs),
        "n_answerable": len(answerable),
        "at_70pct_coverage": at_coverage(runs, 0.70),
        "at_85pct_coverage": at_coverage(runs, 0.85),
        "n_absent": len(runs) - len(answerable),
        **recall_at(runs, (1, 3, 5)),
        **_mrr(runs, (1, 3, 5)),
        "answerable_vs_absent_auroc": round(answerable_separation_auroc(runs), 4),
        "at_threshold": wrong_answer_rate(runs, threshold),
        "risk_coverage": risk_coverage(runs),
        "unbacked_passage_answers": sum(1 for r in runs if r.unbacked_passage),
    }


#: Coverages the report compares arms at.
#:
#: A single fixed threshold is only meaningful among arms whose scores share a
#: scale. Arms A, B, C and F all score in roughly [0, 1]; arm D's reciprocal
#: rank fusion tops out near 2/61, so at 0.30 it abstains on everything and its
#: "confident-wrong" column reads as empty rather than as good.
#:
#: Comparing at equal *coverage* sidesteps that entirely, and it is the fairer
#: question anyway: "which arm is most right when it is willing to answer as
#: much as the others are" is the decision an integrator is actually making.
def at_coverage(runs: list[Run], coverage: float) -> dict:
    """The four-way breakdown at the threshold that yields `coverage`."""
    threshold = _threshold_for_coverage(runs, coverage)
    answerable = [r for r in runs if r.answerable]
    achieved = (
        sum(1 for r in answerable if r.scores and r.scores[0] >= threshold) / len(answerable)
        if answerable
        else 0.0
    )
    return {
        "requested_coverage": coverage,
        "threshold": round(threshold, 6),
        "achieved_coverage": round(achieved, 4),
        **wrong_answer_rate(runs, threshold),
    }
