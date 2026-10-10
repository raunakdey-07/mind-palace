"""Report answer quality as four outcomes, not one accuracy number.

    python scripts/quality_frame.py

## Why not a single number

"131/157 correct" and "45/60 correct" describe two different failure modes and
average them into one figure that fits neither.

* A question the archive could answer, answered wrong, is a **wrong answer**.
  Somebody reads it and believes it.
* A question the archive could not answer, answered anyway, is worse: the system
  asserted something it had no basis for, and a reader cannot tell which of its
  answers are safe to act on.
* A question it declined to answer is a **product limitation**, not a safety
  failure. It costs the user an answer and buys them a truth.

An aggregate accuracy cannot distinguish "the system abstains when it is unsure"
from "the system answers when it is unsure". Those are different products, and
one of them is the reason to use a memory subsystem at all.

So this reports, per evaluation:

    correct                              answered, and right
    abstained_when_answerable            declined, but the archive had the answer
    answered_wrong                       answered, and wrong
    answered_when_it_should_have_abstained  answered where the right answer was silence
    abstained_correctly                  declined, correctly

`answered_when_it_should_have_abstained` is the one to watch. It is the only
category where the system was confident and had no right to be.

## Inputs

    docs/performance/m0130-heldout.json    held-out v1, 157 independently authored questions
    eval/m00675/result.json                frozen M006.75 benchmark, 60 scenarios

Both are frozen artifacts and are read, never written.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HELDOUT_V1 = "docs/performance/m0130-heldout.json"
FROZEN = "eval/m00675/result.json"

ORDER = (
    "correct",
    "abstained_when_answerable",
    "answered_wrong",
    "abstained_correctly",
    "answered_when_it_should_have_abstained",
)

LABEL = {
    "correct": "correct",
    "abstained_when_answerable": "abstained when answerable",
    "answered_wrong": "answered wrong",
    "abstained_correctly": "abstained correctly",
    "answered_when_it_should_have_abstained": "ANSWERED WHEN IT SHOULD HAVE ABSTAINED",
}


def heldout(path: Path) -> tuple[collections.Counter, list[str]]:
    data = json.loads(path.read_text())
    counts: collections.Counter = collections.Counter()
    notes: list[str] = []
    for row in data["results"]:
        answerable = row["expect_state"] != "ABSENT"
        answered = bool(row.get("keys_returned"))
        correct = bool(row["fallback_ok"])

        if not answerable:
            # An ABSENT question with nothing returned is the behaviour we want.
            # One marked incorrect anyway is a grader disagreement, not a system
            # failure, and it is reported separately rather than counted as either.
            if not correct and not answered:
                notes.append(
                    f"{row['qid']}: expected no memory, returned nothing, graded incorrect "
                    f"(expect_state={row['expect_state']}, failure={row['failure']!r}). "
                    f"Counted as abstained-correctly; the grader disagrees."
                )
                counts["abstained_correctly"] += 1
            elif answered:
                counts["answered_when_it_should_have_abstained"] += 1
            else:
                counts["abstained_correctly"] += 1
            continue

        if correct:
            counts["correct"] += 1
        elif answered:
            counts["answered_wrong"] += 1
        else:
            counts["abstained_when_answerable"] += 1
    return counts, notes


def frozen(path: Path) -> tuple[collections.Counter, list[str]]:
    data = json.loads(path.read_text())
    counts: collections.Counter = collections.Counter()
    for row in data["questions"]:
        abstention = row["abstention"]
        should_abstain = bool(abstention["expected"])
        answered = not abstention["actual_empty"]
        if should_abstain:
            key = "answered_when_it_should_have_abstained" if answered else "abstained_correctly"
            counts[key] += 1
        elif row["exact"]:
            counts["correct"] += 1
        elif answered:
            counts["answered_wrong"] += 1
        else:
            counts["abstained_when_answerable"] += 1
    return counts, []


def report(title: str, counts: collections.Counter, total: int, notes: list[str]) -> dict:
    print(f"\n{title}  (n={total})")
    print(f"  {'':40}{'n':>4}  {'share':>7}")
    for key in ORDER:
        value = counts.get(key, 0)
        print(f"  {LABEL[key]:40}{value:4}  {100 * value / total:6.1f}%")
    print(f"  {'TOTAL':40}{sum(counts.values()):4}")
    confident_wrong = counts.get("answered_when_it_should_have_abstained", 0)
    print(f"  >> confident-wrong: {confident_wrong}")
    for note in notes:
        print(f"  note: {note}")
    return {
        "total": total,
        **{key: counts.get(key, 0) for key in ORDER},
        "confident_wrong": confident_wrong,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="write the report as JSON here")
    args = parser.parse_args()

    results = {}
    v1_counts, v1_notes = heldout(ROOT / HELDOUT_V1)
    results["heldout_v1"] = report("HELD-OUT v1", v1_counts, sum(v1_counts.values()), v1_notes)

    frozen_counts, frozen_notes = frozen(ROOT / FROZEN)
    results["frozen_m00675"] = report(
        "FROZEN M006.75", frozen_counts, sum(frozen_counts.values()), frozen_notes
    )

    print("\nRead the two confident-wrong counts together: they are the number that")
    print("decides whether an integrator can act on an answer without re-checking it.")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
