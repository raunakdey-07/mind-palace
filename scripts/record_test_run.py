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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--passed", type=int)
    parser.add_argument("--failed", type=int, default=0)
    parser.add_argument("--skipped", type=int, default=0)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--command", help="run this first and record its result")
    parser.add_argument("--note", default="")
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
    }
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"recorded {passed} passed / {failed} failed / {skipped} skipped as {run_id!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
