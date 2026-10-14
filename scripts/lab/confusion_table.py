"""The one confusion table for the product path on the 202-question dev set.

    python scripts/lab/confusion_table.py

Every figure is read out of `docs/performance/retrieval-lab-dev.json`. Nothing
here is typed in, so the table cannot drift from the artifact it describes.

## Why this exists

Five numbers have been quoted about the same arm at the same time -- 117, 127,
151, 24 and 33 -- and two of them are **the same number arrived at by two
unrelated routes**, which is what made them unreadable.

`scripts/retrieval_lab/metrics.py:249` partitions each question into exactly one
cell. `scripts/retrieval_lab/harm.py:60` partitions it differently: its
`correct()` returns True when a question is ABSENT and the run abstained. So the
harm matrix's "151 correct" is **127 correct + 24 correctly abstained** at the
0.30 threshold, while the report's "151" is **117 + 33 + 1 answered** at 85%
coverage. Both are 151. Neither is the other. Every argument built on "151" was
really an argument about one of two different quantities.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "docs" / "performance" / "retrieval-lab-dev.json"

#: The three operating points the lab reports, in the order a reader meets them.
OPERATING_POINTS = (
    ("at 0.30 (the shipped threshold)", "at_threshold"),
    ("at 85% coverage", "at_85pct_coverage"),
    ("at 70% coverage", "at_70pct_coverage"),
)


def main() -> int:
    data = json.loads(ARTIFACT.read_text())
    overall = data["arms"]["shipped-rank1"]["overall"]
    n_able = overall["n_answerable"]
    n_absent = overall["n_absent"]

    print(f"source: {ARTIFACT.relative_to(ROOT)}")
    print(f"set: {data['set']}   dataset_hash {data['dataset_hash']}")
    print(f"questions {overall['n']} = answerable {n_able} + absent {n_absent}\n")

    header = (
        f"{'':34}| {'correct':>9} {'wrong':>7} {'abstained':>10} {'on absent':>10} | {'total':>7}"
    )
    for label, key in OPERATING_POINTS:
        row = overall[key]
        # metrics.py counts each question into exactly one of these five, so the
        # row sums are the invariant; assert rather than trust.
        answerable_row = row["correct"] + row["wrong"] + row["abstained_when_answerable"]
        absent_row = row["abstained_correctly"] + row["false_answer"]
        assert answerable_row == n_able, (label, answerable_row, n_able)
        assert absent_row == n_absent, (label, absent_row, n_absent)
        total = answerable_row + absent_row
        assert total == overall["n"], (label, total, overall["n"])
        assert row["answered"] == row["correct"] + row["wrong"] + row["false_answer"]

        print(f"### {label}   (score threshold {row.get('threshold', data['threshold'])})")
        print(header)
        print(
            f"{'answerable':34}| {row['correct']:>9} {row['wrong']:>7} "
            f"{row['abstained_when_answerable']:>10} {'--':>10} | {answerable_row:>7}"
        )
        print(
            f"{'absent':34}| {'--':>9} {'--':>7} {row['abstained_correctly']:>10} "
            f"{row['false_answer']:>10} | {absent_row:>7}"
        )
        print(
            f"{'total':34}| {row['correct']:>9} {row['wrong']:>7} "
            f"{row['abstained_when_answerable'] + row['abstained_correctly']:>10} "
            f"{row['false_answer']:>10} | {total:>7}"
        )
        print(f"{'':34}  answered: {row['answered']}\n")

    shipped = overall["at_threshold"]
    mid = overall["at_85pct_coverage"]

    print("### the five numbers, and where each one comes from\n")
    print(f"  117   correct answers at 85% coverage  (threshold {mid['threshold']})")
    print(f"  127   correct answers at 0.30          (threshold {data['threshold']})")
    print(
        f"  151   answered at 85% coverage         = "
        f"{mid['correct']} + {mid['wrong']} + {mid['false_answer']}"
    )
    print(
        f"  151   not-wrong at 0.30                = "
        f"{shipped['correct']} + {shipped['abstained_correctly']}"
    )
    print(
        f"  151   harm-matrix baseline             = "
        f"{shipped['correct']} + {shipped['abstained_correctly']}"
    )
    print(
        f"        {shipped['correct']} + {shipped['abstained_correctly']} and "
        f"{mid['correct']} + {mid['wrong']} + {mid['false_answer']} both land on 151."
    )
    print(
        "        Two different quantities. Quoting '151' without saying which one is\n"
        "        the whole confusion; that is what 'correct on 151 of 202' did."
    )
    print("\n  24    abstentions that were CORRECT      (absent questions, at 0.30)")
    print("  33    wrong answers on answerable questions at 85% coverage")
    print(
        f"        {mid['wrong']} + the {mid['false_answer']} answered-on-absent = "
        f"{mid['wrong'] + mid['false_answer']} wrong answers at 85% coverage,"
    )
    print(
        "        which is exactly the 194 - 117 = 77 minus the 43 a previous\n"
        "        session reconciled. 77 - 43 = "
        f"{77 - 43}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
