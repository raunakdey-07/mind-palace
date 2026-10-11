"""Freeze the held-out v2 question set.

    python scripts/freeze_heldout_v2.py

Writes the questions, a spec of how they were authored, and a SHA-256 over the
canonical question rows. The hash is what makes the set a *held-out* set: once it
is published, a change to retrieval that improves this number is a development
result and has to be reported as one.

## Why v3 and not v2

`docs/performance/heldout-v2-freeze.json` already exists: 32 questions, frozen
2026-10-04 at commit 01980f5, and already scored. Issuing a second set under the
same name would make "held-out v2" mean two different things, and the older one
has been looked at. This set is therefore **v3**.

## What these questions are

Authored from the ten questions a maintainer of this repository actually asked
during the first-hour audit -- see `docs/first-hour-audit.md` -- and from the
kinds of question the product's own limitations predict it will be asked. They
are written to be *paraphrased*, not quoted, because quoting is a different and
much easier task: the dogfood corpus was one or two verbatim sentences per
document and a question that reuses the sentence is close to a lookup.

No retrieval code was read while writing them. The only inputs were the ten
dogfood questions, the public documentation, and the two failed evaluation
reports' *category names* -- never their per-question outcomes.

Scoring happens once, next round, against one commit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/performance/heldout-v3-freeze.json"

SPEC = {
    "name": "held-out v3",
    "authored": "2026-10-11",
    "corpus": "the benchmark corpus (dataset_hash 9cdecda73cbdc8ae)",
    "valid_at": "2026-01-15T12:00:00+00:00",
    "model": "all-MiniLM-L6-v2",
    "arm": "shipped strategy C_fallback (lexical, then semantic)",
    "scoring": "exactly once, at one commit, by a scorer that has not seen retrieval code",
    "why_v3": (
        "held-out v2 already exists (32 questions, frozen 2026-10-04, already scored). "
        "A second set under that name would make the identifier ambiguous."
    ),
    "source_of_questions": (
        "the ten maintainer questions from the first-hour audit, plus the question "
        "shapes the measured failure classes predict"
    ),
    "rules": [
        "A question is answerable unless expect_state is ABSENT.",
        "For an ABSENT question the only correct behaviour is to return nothing.",
        "A question answered from the wrong document is wrong, not abstention.",
        "Paraphrase, do not quote: the source sentence must not appear verbatim in the question.",
        "The scoring rubric is four outcomes, not an accuracy: correct, abstained when "
        "answerable, answered wrong, answered when it should have abstained.",
    ],
    "categories": {
        "current_fact": "what is true now about one named thing",
        "why_decision": "the reasoning behind a choice, asked indirectly",
        "historical": "what was true before, and what replaced it",
        "absence": "something the archive does not contain; the right answer is silence",
        "scope_confusion": "two similarly named things; the answer is the one that was asked for",
        "relationship": "which component owns which responsibility",
        "constraint": "a limit that constrains an otherwise plausible action",
        "conflict": "two live claims that disagree",
        "multi_topic": "a question spanning two documented areas",
        "near_miss": "a question that sounds answerable and is not, at the vocabulary boundary",
    },
}

# (qid suffix, category, expect_state, question, accept_if_contains)
Q = [
    # --- current_fact: the baseline a memory subsystem exists for -------------
    (
        "cur-01",
        "current_fact",
        "CURRENT",
        "Which database engine does this project depend on today?",
        None,
    ),
    ("cur-02", "current_fact", "CURRENT", "What is the primary data store in use right now?", None),
    ("cur-03", "current_fact", "CURRENT", "Name the component that owns order processing.", None),
    ("cur-04", "current_fact", "CURRENT", "Where does the team log audit records?", None),
    ("cur-05", "current_fact", "CURRENT", "Which message broker carries order events?", None),
    ("cur-06", "current_fact", "CURRENT", "What backs the order event stream today?", None),
    # --- why_decision: the measured weak class (19 of 26 held-out v1 failures)
    (
        "why-01",
        "why_decision",
        "CURRENT",
        "The team moved off the earlier database engine. What capability drove that?",
        None,
    ),
    (
        "why-02",
        "why_decision",
        "CURRENT",
        "Why is order processing handled by the service it is handled by?",
        None,
    ),
    (
        "why-03",
        "why_decision",
        "CURRENT",
        "What problem was the current event transport chosen to solve?",
        None,
    ),
    (
        "why-04",
        "why_decision",
        "CURRENT",
        "The audit destination changed at some point. What motivated that?",
        None,
    ),
    # --- historical: retrieval is not history
    (
        "his-01",
        "historical",
        "HISTORICAL",
        "What database engine was in use before the present one?",
        None,
    ),
    (
        "his-02",
        "historical",
        "HISTORICAL",
        "Which service handled orders previously, and what took over?",
        None,
    ),
    (
        "his-03",
        "historical",
        "HISTORICAL",
        "Where were audit records written before the current destination?",
        None,
    ),
    # --- absence: the safety property, and the one that matters most
    ("abs-01", "absence", "ABSENT", "Which vendor supplies the office coffee?", None),
    ("abs-02", "absence", "ABSENT", "What is the team's end-to-end recovery time objective?", None),
    ("abs-03", "absence", "ABSENT", "Who signs off on hardware purchases?", None),
    (
        "abs-04",
        "absence",
        "ABSENT",
        "What is the monthly budget for the observability stack?",
        None,
    ),
    ("abs-05", "absence", "ABSENT", "Which law firm handles employment disputes?", None),
    ("abs-06", "absence", "ABSENT", "How many support tickets were open last quarter?", None),
    # --- near_miss: the boundary where lexical ranking gives up
    ("near-01", "near_miss", "ABSENT", "Summarise the project's security posture.", None),
    (
        "near-02",
        "near_miss",
        "ABSENT",
        "What is the capital of the country the team operates in?",
        None,
    ),
    ("near-03", "near_miss", "ABSENT", "List every environment variable this project reads.", None),
    # --- scope_confusion: the right answer is the narrower one
    (
        "scope-01",
        "scope_confusion",
        "CURRENT",
        "Which component owns risk decisions, as distinct from the one that owns data?",
        None,
    ),
    (
        "scope-02",
        "scope_confusion",
        "CURRENT",
        "For order events specifically, which component is authoritative?",
        None,
    ),
    # --- relationship
    (
        "rel-01",
        "relationship",
        "CURRENT",
        "Which team is accountable for the service that stores orders?",
        None,
    ),
    ("rel-02", "relationship", "CURRENT", "What does the order-event consumer depend on?", None),
    (
        "rel-03",
        "relationship",
        "CURRENT",
        "Which component is on the critical path for replaying an order?",
        None,
    ),
    # --- constraint
    ("con-01", "constraint", "CURRENT", "What is the retention limit on order events?", None),
    ("con-02", "constraint", "CURRENT", "How many replicas must the event stream run?", None),
    (
        "con-03",
        "constraint",
        "CURRENT",
        "What is the maximum payload size the order endpoint accepts?",
        None,
    ),
    ("con-04", "constraint", "CURRENT", "Which constraint limits how long a replay may run?", None),
    # --- conflict: two live claims, reported not merged
    (
        "cfl-01",
        "conflict",
        "CONFLICTING",
        "Two components are named as owning order routing. Which are they?",
        None,
    ),
    # --- multi_topic: spans two documented areas
    (
        "mul-01",
        "multi_topic",
        "CURRENT",
        "Which datastore holds orders, and which component consumes order events?",
        None,
    ),
    (
        "mul-02",
        "multi_topic",
        "CURRENT",
        "Where are audit records written, and what is that destination's retention limit?",
        None,
    ),
    (
        "mul-03",
        "multi_topic",
        "CURRENT",
        "What replaced the earlier datastore, and what is the present datastore?",
        None,
    ),
    # --- maintainer-shaped: the questions the dogfood actually asked
    (
        "mnt-01",
        "current_fact",
        "CURRENT",
        "Which version of this project can a developer install today?",
        None,
    ),
    ("mnt-02", "current_fact", "CURRENT", "How many tests pass in the suite?", None),
    (
        "mnt-03",
        "constraint",
        "CURRENT",
        "How much disk space does installing this project require?",
        None,
    ),
    ("mnt-04", "current_fact", "CURRENT", "Is erasure from backups supported?", None),
    (
        "mnt-05",
        "current_fact",
        "CURRENT",
        "Which authentication mode does the server deployment profile require?",
        None,
    ),
    (
        "mnt-06",
        "current_fact",
        "CURRENT",
        "What database role should the application connect as?",
        None,
    ),
    (
        "mnt-07",
        "near_miss",
        "ABSENT",
        "Which of these projects has the best retrieval benchmark?",
        None,
    ),
    (
        "mnt-08",
        "relationship",
        "CURRENT",
        "Which components does the event transport sit between?",
        None,
    ),
    (
        "mnt-09",
        "multi_topic",
        "CURRENT",
        "Which datastore holds the archive, and where is the runbook for restoring it?",
        None,
    ),
    ("mnt-10", "current_fact", "CURRENT", "What happens when a corpus is deleted?", None),
]


def canonical(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main() -> int:
    rows = [
        {
            "qid": f"hv3-{suffix}",
            "category": category,
            "expect_state": state,
            "text": text,
        }
        for suffix, category, state, text, _ in Q
    ]
    by_category: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for row in rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
        by_state[row["expect_state"]] = by_state.get(row["expect_state"], 0) + 1

    spec = {**SPEC, "questions": len(rows)}
    payload = {
        "spec": spec,
        "categories": dict(sorted(by_category.items())),
        "expect_states": dict(sorted(by_state.items())),
        "questions": len(rows),
        "dataset_hash": hashlib.sha256(canonical(rows).encode()).hexdigest(),
        "question_rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  questions      {len(rows)}")
    print(f"  dataset_hash   {payload['dataset_hash']}")
    print(f"  by state       {payload['expect_states']}")
    print(f"  by category    {payload['categories']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
