"""Record a real suite run, so a document can be checked against it.

    python scripts/record_test_run.py --passed 1600 --skipped 1 --seconds 538
    python scripts/record_test_run.py --command "pytest tests/ -q"   # run it first

Writes `docs/performance/test-runs.json`, keyed by commit. `scripts/check_evidence.py`
then refuses to let STATUS.md or the README state an "N passed" that no run
produced.

## Why this exists

STATUS.md once carried **1583 passed**. No run had produced 1583; the measured
count was 1567. It survived because a plausible number in a table is
indistinguishable from a measured one, and nobody compared it with anything. By
the time the discrepancy was noticed it had been copied forward through two
documents.

The fix is not vigilance. It is a number in a file that only a test run can
write, and a check that compares the two.

## What it does not do

It does not decide whether a run was *correct* -- only that it happened. A run
recorded here can still be a run against the wrong database, which is exactly
what the hermetic session database (`tests/session_database.py`) exists to
prevent.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "docs/performance/test-runs.json"

SUMMARY = re.compile(
    r"(?P<passed>\d+) passed(?:,\s*(?P<failed>\d+) failed)?(?:,\s*(?P<skipped>\d+) skipped)?"
)


def head_commit() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def load() -> dict:
    if not RECORD.exists():
        return {
            "note": "Written only by an actual pytest run. See scripts/record_test_run.py.",
            "runs": {},
        }
    try:
        data = json.loads(RECORD.read_text())
    except json.JSONDecodeError:
        return {"runs": {}}
    data.setdefault("runs", {})
    return data


def _normalise_junit(path: str) -> dict:
    """Rewrite the checkout's absolute path out of the junitxml artifact.

    ## Why this is a script and not an edit

    The artifact is evidence, so the numbers in it are pytest's and nobody
    touches them by hand. But pytest bakes the *absolute path of the checkout*
    into `skipped` messages, so committing the file on one machine publishes
    `/home/someone/...` to everyone. That is a host-specific path in committed
    evidence, and the pre-push audit is right to flag it.

    So the substitution happens here, in the recorder that already owns writing
    the run record, and it is a pure prefix replacement of one string:

    * every occurrence of the absolute repository root becomes `.`
    * test counts, times, classnames and skip reasons are untouched
    * the number of replacements is reported, so a silent no-op is visible

    If it ever needed to touch anything else, this would be the wrong place to
    do it and the artifact should not be committed at all.
    """
    target = Path(path)
    if not target.exists():
        return {"normalised": False, "reason": f"{path} does not exist"}
    root = str(ROOT.resolve())
    text = target.read_text(encoding="utf-8", errors="replace")
    occurrences = text.count(root)
    if occurrences == 0:
        return {"normalised": False, "reason": "no absolute checkout path present"}
    target.write_text(text.replace(root, "."), encoding="utf-8")
    return {"normalised": True, "replacements": occurrences, "replaced_with": "."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--passed", type=int)
    parser.add_argument("--failed", type=int, default=0)
    parser.add_argument("--skipped", type=int, default=0)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--command", help="run this first and record its result")
    parser.add_argument("--note", default="")
    parser.add_argument("--collected", type=int, help="how many tests the tree collected")
    parser.add_argument("--junitxml", help="path to the junitxml artifact pytest wrote")
    parser.add_argument(
        "--label", help="what this run covered, e.g. 'tests/test_corpus_isolation.py'"
    )
    parser.add_argument("--force", action="store_true", help="replace an existing record")
    args = parser.parse_args()

    passed, failed, skipped, seconds = args.passed, args.failed, args.skipped, args.seconds

    if args.command:
        started = time.monotonic()
        result = subprocess.run(args.command, shell=True, cwd=ROOT, capture_output=True, text=True)
        seconds = round(time.monotonic() - started, 1)
        match = None
        for line in reversed(result.stdout.splitlines()):
            match = SUMMARY.search(line)
            if match:
                break
        if result.returncode != 0:
            print(
                f"the command failed (exit {result.returncode}); not recording a run",
                file=sys.stderr,
            )
            print(result.stdout[-2000:], file=sys.stderr)
            return 1
        if match:
            passed = int(match["passed"])
            failed = int(match["failed"] or 0)
            skipped = int(match["skipped"] or 0)
        else:
            print("could not find a pytest summary line; not recording a run", file=sys.stderr)
            return 1

    if passed is None:
        parser.error("give --passed, or --command to run something first")

    data = load()
    commit = head_commit()
    label = args.label or (args.command or "full suite")
    run_id = f"{commit[:7]}: {label}"
    if run_id in data["runs"] and not args.force:
        parser.error(f"a run is already recorded as {run_id!r}; pass --force to replace it")

    data["runs"][run_id] = {
        "commit": commit,
        "label": label,
        "passed": str(passed),
        "failed": str(failed),
        "skipped": str(skipped),
        **({"seconds": seconds} if seconds is not None else {}),
        **({"command": args.command} if args.command else {}),
        **({"note": args.note} if args.note else {}),
        **({"collected": str(args.collected)} if args.collected is not None else {}),
        **({"junitxml": args.junitxml} if args.junitxml else {}),
        **({"junit_normalisation": _normalise_junit(args.junitxml)} if args.junitxml else {}),
    }
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"recorded {passed} passed / {failed} failed / {skipped} skipped as {run_id!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
