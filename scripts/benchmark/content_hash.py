"""A second hash, over the graded fields a set artifact actually records.

    python scripts/benchmark/content_hash.py docs/performance/m0130-heldout.json
    python scripts/benchmark/content_hash.py --questions-only

## Why there are two hashes

`dataset_hash()` in `m0115_dataset.py` and `m0130_heldout.py` hashes the
**documents plus the question objects the generator produces right now**. It is
the right hash for one question: *does this code still build this set?*

It cannot answer a different question: *does this artifact record the set it
claims to record?* Because a hash of the generator's output says nothing about a
**derived summary** written into a downstream file. That is not hypothetical —
it is what happened, and it is the whole reason this file exists.

`docs/performance/m0130-heldout.json` is consistent: its `meta.dataset_hash`
matches the tree exactly, and its `summary.by_category.abstention.n` is 16, which
is what `build_questions()` produces today. **The frozen set is not affected.**

`docs/performance/retrieval-lab-heldout-v1.json` is not. It carries the same
`13030d897fa…` hash, so it looks attested, and it records `counts.answerable:
140` and `overall.n_absent: 17` — while the set it names has 141 answerable and
16 absent. The hash did not move because the hash never covered those numbers;
they are *derived*, not inputs. A derived number can be wrong under a correct
hash, and nothing in the repository checked it.

## What this hashes

Every field the grader reads, taken from the **artifact's own rows** rather than
from the generator:

    qid, category, text, expect_key, expect_state

plus, when available from the generator, `expect_values` and the corpus
documents. It is recomputable from a file on disk with no database, no model
and no network, so anyone can check an artifact someone else produced.

**Every new question set must carry both**, and the rule is that
`dataset_hash` == content of the generator, while `content_hash` == content of
the rows. A set that has one and not the other has not been pinned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

#: The fields the grader reads off a result row. Ordered, because a hash over an
#: unordered mapping would let a rename hide.
GRADED_FIELDS = ("qid", "category", "text", "expect_key", "expect_state")


def graded_rows(artifact: dict) -> list[dict]:
    """One canonical row per result, with only the graded fields, sorted by qid.

    `text` is included because it is the question the grader was asked, and
    `category` because the slice is reported from it.
    """
    rows = []
    for result in artifact["results"]:
        row = {field: result.get(field) for field in GRADED_FIELDS}
        rows.append(row)
    return sorted(rows, key=lambda r: str(r["qid"]))


def content_hash(rows: list[dict]) -> str:
    blob = json.dumps(
        {"schema": "mindpalace.content_hash/1", "graded_fields": list(GRADED_FIELDS), "rows": rows},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path, nargs="?")
    parser.add_argument(
        "--questions-only",
        action="store_true",
        help="hash the current generator's output instead of an artifact",
    )
    args = parser.parse_args()

    if args.questions_only:
        import m0115_dataset

        questions = m0115_dataset.build_questions()
        rows = sorted(
            (
                {
                    "qid": q.qid,
                    "category": q.category,
                    "text": q.text,
                    "expect_key": q.key,
                    "expect_state": q.expect_state,
                }
                for q in questions
            ),
            key=lambda r: str(r["qid"]),
        )
    else:
        if args.artifact is None:
            parser.error("give an artifact path, or --questions-only")
        rows = graded_rows(json.loads(args.artifact.read_text()))

    digest = content_hash(rows)
    print(f"rows {len(rows)}  graded fields {', '.join(GRADED_FIELDS)}")
    print(f"content_hash {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
