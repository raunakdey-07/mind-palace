"""Run the lab.

    python scripts/retrieval_lab/run.py --set dev --arms A,C,D --out out.json

Writes one JSON artifact and prints the per-slice table. The artifact is the
evidence; the table is for reading it.

## Determinism

No sampling anywhere. MiniLM in eval mode, float32, no temperature, no LLM
judge. Two runs on the same machine produce byte-identical output, and the
embedding fingerprint is recorded in the artifact so a result cannot be
compared against one produced with different weights.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from retrieval_lab import arms as arm_module  # noqa: E402
from retrieval_lab import metrics as M  # noqa: E402
from retrieval_lab.corpus import load_dev, load_heldout_v1  # noqa: E402
from retrieval_lab.gitderived import load_git_derived  # noqa: E402
from retrieval_lab.embeddings import (  # noqa: E402
    SHIPPED_EMBEDDER,
    describe,
    fingerprint,
)

#: `git-derived` returns a third element: its passage-level gold, which lives
#: beside the questions because it is passage-level only. No document in this
#: repository carries an authored claim, so there is no claim key to attach.
LOADERS = {"dev": load_dev, "heldout-v1": load_heldout_v1, "git-derived": load_git_derived}

#: The operating point the report leads with. Fixed here, before any arm has
#: been scored, so that "which arm is best" cannot be answered by picking the
#: threshold that flatters it. The risk-coverage curve in the artifact is what
#: anyone should actually read.
DEFAULT_THRESHOLD = 0.30


def run_arm(name: str, index, corpus, questions, bm25) -> list[M.Run]:
    runs = []
    for question in questions:
        if name == "A":
            runs.append(arm_module.arm_a(index, question, corpus))
        elif name == "B":
            runs.append(arm_module.arm_b(index, question, corpus, bm25))
        elif name == "C":
            runs.append(arm_module.arm_c(index, question, corpus))
        elif name == "D":
            runs.append(arm_module.arm_d(index, question, corpus, bm25))
        elif name == "E":
            runs.append(arm_module.arm_e(index, question, corpus, bm25))
        elif name == "F":
            runs.append(arm_module.arm_f(index, question, corpus))
        elif name == "G":
            runs.append(arm_module.arm_g(index, question, corpus, bm25))
        else:
            raise SystemExit(f"unknown arm {name!r}")
    return runs


def per_slice(runs: list[M.Run], threshold: float) -> dict:
    out: dict[str, dict] = {}
    slices = sorted({r.slice for r in runs})
    for slice_name in slices:
        subset = [r for r in runs if r.slice == slice_name]
        if len(subset) < 3:
            # Three questions is not a rate. Reported, not scored.
            out[slice_name] = {"n": len(subset), "note": "too few questions to report a rate"}
            continue
        out[slice_name] = M.metrics_for_run(subset, threshold)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default="dev", choices=sorted(LOADERS))
    parser.add_argument("--arms", default="A,B,C,D,E,F")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--embedder",
        default=SHIPPED_EMBEDDER,
        help="the encoder every arm uses. Arm G needs the reference one.",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    loaded = LOADERS[args.set]()
    corpus, questions = loaded[0], loaded[1]
    passage_gold = loaded[2] if len(loaded) > 2 else None
    print(
        f"set {args.set}: {len(corpus.claims)} claims, {len(corpus.passages)} passages, "
        f"{len(questions)} questions"
    )

    index = arm_module._Index(corpus, args.embedder)
    bm25 = arm_module._BM25(index.claim_token_lists)

    M.attach_gold({q.qid: q.key for q in questions})
    if passage_gold is None:
        M.attach_gold_passages(
            {q.qid: [p.passage_id for p in corpus.passages_for(q.key)] for q in questions if q.key}
        )
    else:
        M.attach_gold_passages({qid: list(pids) for qid, pids in passage_gold.items() if pids})

    results: dict[str, dict] = {}
    for name in args.arms.split(","):
        name = name.strip()
        if name != "F" and not corpus.claims:
            # Claim-ranking arms have nothing to rank. Recorded rather than
            # silently scored as zero, which would read as "the arm failed"
            # when the honest answer is "the question was not applicable".
            results[name] = {
                "status": "not_applicable",
                "reason": "this set is passage-level only; the corpus has no authored claims",
            }
            print(f"  arm {name}: NOT APPLICABLE -- passage-level set")
            continue
        if name == "G":
            ok, why = arm_module.arm_g_available()
            if not ok or args.embedder == SHIPPED_EMBEDDER:
                reason = why if not ok else "needs --embedder with the reference model"
                results[name] = {"status": "unreachable", "reason": reason}
                print(f"  arm G: UNREACHABLE -- {reason}")
                continue
        started = time.perf_counter()
        runs = run_arm(name, index, corpus, questions, bm25)
        elapsed = time.perf_counter() - started
        results[name] = {
            "seconds": round(elapsed, 2),
            "overall": M.metrics_for_run(runs, args.threshold),
            "per_slice": per_slice(runs, args.threshold),
        }
        overall = results[name]["overall"]
        primary = "passage_recall@1" if not corpus.claims else "recall@1"
        print(
            f"  arm {name}: {primary}={overall[primary]} recall@3={overall['recall@3']} "
            f"recall@5={overall['recall@5']} mrr={overall['mrr']:.3f} "
            f"auroc={overall['answerable_vs_absent_auroc']} "
            f"conf-wrong={overall['at_threshold']['wrong_answer_rate']} ({elapsed:.1f}s)"
        )

    artifact = {
        "set": args.set,
        "notes": corpus.notes,
        "dataset_hash": corpus.dataset_hash,
        "embedding_fingerprint": fingerprint(),
        "models": describe(),
        "threshold": args.threshold,
        "embedder": args.embedder,
        "counts": {
            "claims": len(corpus.claims),
            "passages": len(corpus.passages),
            "passages_without_claims": sum(1 for p in corpus.passages if not p.claim_keys),
            "questions": len(questions),
            "answerable": sum(1 for q in questions if q.answerable),
        },
        "arms": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
