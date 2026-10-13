"""Both-ways comparison of every arm against the shipped default.

    python scripts/retrieval_lab/harm.py --set dev --out harm-dev.json

## The question

Last round reported how many questions each arm *fixes*. That is half a number,
and the half that flatters. An arm that fixes 12 questions and breaks 9 has not
improved anything; it has changed which 21 questions fail.

**Retrieval harm** is the count of questions the shipped default gets RIGHT and
the arm gets WRONG. It is the retrieval equivalent of the harm rate the
downstream protocol defines, and it is the only version of this comparison that
can be aggregated honestly.

Four cells, per question, per arm:

| cell | shipped | arm | meaning |
|---|---|---|---|
| `fixed` | wrong | right | the arm repaired it |
| `broken` | right | wrong | **the arm damaged it** |
| `unchanged_correct` | right | right | both got it |
| `unchanged_wrong` | wrong | wrong | both missed it |

Correctness is top-1 claim match, and for an ABSENT question, abstaining.

## What this cannot tell you

It is measured on **dev** and on the **burned** held-out v1. Both are tuning or
diagnosis sets. No number here is evidence of generalisation, and the counts on
held-out v1 in particular are on a set whose failures the author already knows.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from retrieval_lab import arms as arm_module  # noqa: E402
from retrieval_lab import metrics as M  # noqa: E402
from retrieval_lab.corpus import load_dev, load_heldout_v1  # noqa: E402

LOADERS = {"dev": load_dev, "heldout-v1": load_heldout_v1}

#: The abstain rule a run is judged at. Same 0.30 the lab protocol froze, and
#: the same reason it is not tuned per arm: choosing each arm's best threshold
#: on the set being scored answers a different question.
THRESHOLD = 0.30


def correct(run, question) -> bool:
    """Top-1 correct, or abstained on an ABSENT question."""
    answered = bool(run.scores) and run.scores[0] >= THRESHOLD
    if question.answerable:
        return bool(answered and run.keys[:1] == [question.key])
    return not answered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default="dev", choices=sorted(LOADERS))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    corpus, questions = LOADERS[args.set]()
    index = arm_module._Index(corpus)
    bm25 = arm_module._BM25(index.claim_token_lists)
    M.attach_gold({q.qid: q.key for q in questions})

    baseline = {
        q.qid: correct(
            (
                arm_module.arm_shipped_rank1(index, q, corpus)
                if q.answerable
                else _abstain_only(index, q, corpus)
            ),
            q,
        )
        for q in questions
    }

    results: dict[str, dict] = {}
    detail: dict[str, list[dict]] = {}
    for name, run in (
        ("bm25", lambda q: arm_module.arm_bm25(index, q, corpus, bm25)),
        ("semantic", lambda q: arm_module.arm_semantic(index, q, corpus)),
        ("rrf", lambda q: arm_module.arm_rrf(index, q, corpus, bm25)),
        ("fusion-rerank", lambda q: arm_module.arm_fusion_rerank(index, q, corpus, bm25)),
        ("passage", lambda q: arm_module.arm_passage(index, q, corpus)),
    ):
        cells: Counter = Counter()
        by_slice: dict[str, Counter] = defaultdict(Counter)
        rows = []
        for question in questions:
            result = run(question)
            arm_ok = correct(result, question)
            base_ok = baseline[question.qid]
            if arm_ok and not base_ok:
                cell = "fixed"
            elif base_ok and not arm_ok:
                cell = "broken"
            elif arm_ok and base_ok:
                cell = "unchanged_correct"
            else:
                cell = "unchanged_wrong"
            cells[cell] += 1
            by_slice[question.slice][cell] += 1
            rows.append({"qid": question.qid, "slice": question.slice, "cell": cell})
        results[name] = {
            **dict(cells),
            "net": cells["fixed"] - cells["broken"],
            "per_slice": {s: dict(c) for s, c in sorted(by_slice.items())},
        }
        detail[name] = rows

    artifact = {
        "set": args.set,
        "threshold": THRESHOLD,
        "baseline": "arm `shipped-rank1`, the shipped scorer at rank 1",
        "n": len(questions),
        "baseline_correct": sum(baseline.values()),
        "arms": results,
        "detail": detail,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"set {args.set}: {len(questions)} questions, baseline correct {sum(baseline.values())}")
    for name, r in results.items():
        print(
            f"  arm {name}: fixed={r.get('fixed', 0):3} BROKEN={r.get('broken', 0):3} "
            f"net={r['net']:+4}  (both right {r.get('unchanged_correct', 0)}, "
            f"both wrong {r.get('unchanged_wrong', 0)})"
        )
    print(f"wrote {out}")
    return 0


def _abstain_only(index, question, corpus):
    """An ABSENT question scored by the shipped arm, for the baseline cell."""
    return arm_module.arm_shipped_rank1(index, question, corpus)


if __name__ == "__main__":
    raise SystemExit(main())
