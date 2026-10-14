"""Would labelled passages help, or would they over-answer? Dev set only.

    python scripts/lab/passage_option.py

REPORT ONLY. Nothing here is in the product path, and running it changes no
behaviour. It exists to size one owner decision: whether documents with **no
authored claims** should return labelled, non-authoritative passages instead of
abstaining.

## The option

Today a document with no `claims:` block is chunked and stored, and `query`
**abstains** on it. The option is: for those documents, return the best-matching
passage(s) labelled as *not* an authored claim. It would make plain Markdown and
conversation transcripts answerable.

## The two numbers that decide it

The risk is over-answering, and it has two directions:

1. **Absent questions would receive a passage.** The dev set has **26** absent
   questions -- things the corpus deliberately does not hold. A retriever will
   always return *something* for those. How often a passage clears the shipped
   abstain threshold on a question that has no answer is the over-answer rate.

2. **Answerable-but-abstained questions would be rescued.** The product abstains
   on answerable questions whose top claim scores below threshold. If the right
   passage is in the **top 3**, the option recovers them.

If (1) is much larger than (2), the option trades a silence for a wrong answer on
questions the user asked *because* the answer was not there.

## Why a script and not a flag

There is no flag. Adding one would be a product change, and the point is to find
out whether it is worth one.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (str(ROOT), str(ROOT / "scripts")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

# Two operating points, because "abstains" is a property of a threshold, not of
# a system. At 0.30 the shipped arm abstains on ZERO answerable questions
# (retrieval-lab-dev.json: abstained_when_answerable = 0), so there is nothing to
# rescue and a single-point measurement is vacuous. At the 85%-coverage point it
# abstains on 26 of them, which is where a passage option could do anything.
OPERATING_POINTS = (
    ("0.30  (shipped threshold)", 0.30),
    ("0.570753 (85% coverage)", 0.570753),
)
TOP_K = 3


def _measure(corpus, questions, index, threshold: int) -> dict:
    from retrieval_lab import arms as arm_module
    from retrieval_lab import metrics as M

    out = dict.fromkeys(
        (
            "absent_total",
            "absent_passage",
            "absent_gold",
            "abstain_answerable",
            "abstain_any_passage",
            "abstain_had_gold",
            "rescued_top3",
            "rescued_top5",
            "absent_already_answered",
        ),
        0,
    )
    for q in questions:
        run = arm_module.arm_shipped_rank1(index, q, corpus)
        answered = bool(run.scores) and run.scores[0] >= threshold
        gold = M._GOLD_PASSAGES.get(q.qid, [])  # noqa: SLF001
        if not q.answerable:
            out["absent_total"] += 1
            if answered or run.passages:
                out["absent_passage"] += 1
            if answered:
                out["absent_already_answered"] += 1
            if set(run.passages) & set(gold):
                out["absent_gold"] += 1
            continue
        if answered:
            continue
        out["abstain_answerable"] += 1
        if run.passages:
            out["abstain_any_passage"] += 1
        if gold:
            out["abstain_had_gold"] += 1
            if gold[0] in run.passages[:TOP_K]:
                out["rescued_top3"] += 1
            if gold[0] in run.passages[:5]:
                out["rescued_top5"] += 1
    return out


def main() -> int:
    from retrieval_lab import arms as arm_module
    from retrieval_lab import metrics as M
    from retrieval_lab.corpus import load_dev

    corpus, questions = load_dev()
    index = arm_module._Index(corpus)

    M.attach_gold({q.qid: q.key for q in questions})
    M.attach_gold_passages(
        {q.qid: [p.passage_id for p in corpus.passages_for(q.key)] for q in questions if q.key}
    )

    print("source: dev set, 202 questions (176 answerable, 26 absent)")
    print(f"top-k for rescue: {TOP_K}\n")

    for label, threshold in OPERATING_POINTS:
        m = _measure(corpus, questions, index, threshold)
        print(f"=== threshold {label}")
        print("  1. OVER-ANSWER -- absent questions that would receive a passage")
        print(f"     absent questions                    : {m['absent_total']}")
        print(f"     would receive SOME passage          : {m['absent_passage']}")
        print(f"     of those, ALREADY answered today    : {m['absent_already_answered']}")
        print(f"     would receive a passage that IS gold: {m['absent_gold']}   <- must stay 0")
        print("  2. RESCUE -- answerable questions the product abstains on")
        print(f"     abstained on an answerable question : {m['abstain_answerable']}")
        print(f"     of those, the run produced a passage : {m['abstain_any_passage']}")
        print(f"     of those, a gold passage exists     : {m['abstain_had_gold']}")
        print(f"     gold passage in top {TOP_K}                : {m['rescued_top3']}")
        print(f"     gold passage in top 5                : {m['rescued_top5']}")
        print()

    print("VERDICT INPUT, not a verdict: at 0.30 there is nothing to rescue, because")
    print("the shipped arm abstains on ZERO answerable questions. The option's only")
    print("effect there is on absent questions, and the ones that would receive a")
    print("passage are the ones it already falsely answers.")
    print("\nNOT ESTABLISHED: the passage list here comes from `_finish`, which expands")
    print("claim keys -- a document with no claims has no keys, so its passage list is")
    print("empty by construction. This sizes the gold-side benefit and the abstain")
    print("rule's effect on absent questions. It does NOT model the passage retriever")
    print("a real implementation would use, and it says nothing about a corpus made")
    print("entirely of unclaimed documents -- the dev corpus is mostly claimed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
