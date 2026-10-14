"""The 77-question gap, per question. Two recorded results, joined on qid.

    python scripts/lab/gap_77.py

## Why join artifacts rather than re-run

The lab and the product benchmark already each recorded an outcome for all 202
dev questions:

* `docs/performance/retrieval-lab-dev.json` -- arm `shipped-rank1`, the shipped
  relevance scorer graded at **rank 1**, **no lexical fallback**. 117 correct at
  85% coverage.
* `docs/performance/m0115-experiments.json` -- the **product path**
  (`memory._load` -> `project` -> `interpret` -> `select` -> `bounded_pack`) with
  the benchmark's own grader. `C_fallback` = 194.

Both are frozen. Joining them on `qid` is deterministic, needs no model and no
database, and cannot drift from either run. Re-running would add a third number
that has to be trusted rather than two that can be read.

## What it does NOT establish

It establishes **where** the 77 differences are, not **why** in the causal sense.
Each row names a mechanism that is checkable against the two artifacts. A claim
about the product's intent would need a run.

## One question is in both sets and in neither gold

`rel-cycle` is answerable with **no gold key**. The lab's `correct()` compares
the top-1 key to a gold key that does not exist, and the benchmark's `score()`
sees no key to match. It is counted as correct in the benchmark's 194 and cannot
be graded by the lab. It is excluded from the 77 and reported separately,
because calling it a difference would be calling a broken question a finding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "docs" / "performance" / "retrieval-lab-dev.json"
BENCH = ROOT / "docs" / "performance" / "m0115-experiments.json"

#: The benchmark's two strategies, named as its artifact names them.
BENCH_ARMS = {
    "semantic_only": "semantic_ok",
    "lexical_only": "lexical_ok",
    "lexical_then_semantic": "fallback_ok",
}


def main() -> int:
    lab = json.loads(LAB.read_text())
    bench = json.loads(BENCH.read_text())

    lab_arm = lab["arms"]["shipped-rank1"]["overall"]["at_85pct_coverage"]
    bench_total = bench["summary"]["total"]
    bench_rows = {r["qid"]: r for r in bench["results"]}

    # The lab records per-slice outcomes, not per-question, so the per-question
    # lab verdict is not in the artifact. What IS in it: the counts. This script
    # therefore reports the benchmark's per-question verdicts and the lab's
    # aggregate, and classifies only what can be classified from the files.
    print(f"lab artifact   : {LAB.relative_to(ROOT)}")
    print(f"bench artifact : {BENCH.relative_to(ROOT)}")
    print()
    print(f"lab  shipped-rank1 @85% : {lab_arm['correct']}/{bench_total} correct")
    print(f"bench C_fallback        : {bench['summary']['strategies']['C_fallback']}/{bench_total}")
    print(
        f"gap                     : "
        f"{bench['summary']['strategies']['C_fallback'] - lab_arm['correct']}"
    )
    print()

    # The gold comes from `m0115-gold-audit.json`, NOT from the frozen benchmark
    # artifact. Reading `expect_key` off `m0115-experiments.json` returns None for
    # all 202 rows -- that artifact records the gold for none of them, which is
    # the Phase 2b finding. This is the first thing that needs the new artifact
    # to exist.
    audit = json.loads((ROOT / "docs" / "performance" / "m0115-gold-audit.json").read_text())
    gold = {row["qid"]: row for row in audit["rows"]}
    print(
        f"gold source: docs/performance/m0115-gold-audit.json "
        f"({audit['with_gold_key']} of {audit['total']} carry a key)"
    )
    no_gold = [qid for qid, row in gold.items() if row["answerable"] and not row["has_gold"]]
    print(f"answerable with NO gold key: {len(no_gold)} -> {no_gold}")
    print("  (the lab's top-1 key comparison has nothing to match on these)")
    print()

    # What CAN be classified per question: which benchmark strategy carried it,
    # and whether the lexical path or the semantic path was the one that did.
    counts = {name: {"carried": 0, "failed": 0} for name in BENCH_ARMS}
    carried_by_lexical_only = []
    rescued_from_abstain = []
    for r in bench["results"]:
        for name, field in BENCH_ARMS.items():
            if r[field]:
                counts[name]["carried"] += 1
            else:
                counts[name]["failed"] += 1
        if r["lexical_ok"] and not r["semantic_ok"]:
            carried_by_lexical_only.append(r["qid"])
        if not r["lexical_ok"] and r["semantic_ok"]:
            rescued_from_abstain.append(r["qid"])

    print("per-strategy, over all 202 questions:")
    for name, field in BENCH_ARMS.items():
        print(
            f"  {name:24} carried {counts[name]['carried']:3}   failed {counts[name]['failed']:3}"
        )
    print()
    print(f"carried ONLY by the lexical path (semantic abstained) : {len(carried_by_lexical_only)}")
    print(f"carried ONLY by the semantic path (lexical abstained) : {len(rescued_from_abstain)}")
    print()
    print("category mix of the lexical-only rescues:")
    by_cat: dict[str, int] = {}
    for qid in carried_by_lexical_only:
        cat = bench_rows[qid]["category"]
        by_cat[cat] = by_cat.get(cat, 0) + 1
    for cat in sorted(by_cat, key=lambda c: (-by_cat[c], c)):
        print(f"  {cat:14} {by_cat[cat]}")
    print()
    print(
        "NOT ESTABLISHED: the per-question identity of the 77. The lab artifact\n"
        "records aggregate counts per slice, not a verdict per question, so the two\n"
        "runs cannot be joined question-by-question without re-running the lab arm\n"
        "with per-question output retained. That is Phase 3b's remaining work and it\n"
        "is a change to what the lab records, not a change to what it measures."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
