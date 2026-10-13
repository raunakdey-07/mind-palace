"""Freeze the downstream evaluation protocol's hash, once.

    python scripts/freeze_eval_protocol.py            # freeze, or verify if already frozen

The point is that the protocol in `docs/performance/downstream-eval-protocol.md`
is a pre-registration: the hypotheses, arms, metrics, statistics and decision
rule are fixed before any scored run, so the result cannot be reinterpreted
afterwards. A digest that can be recomputed at will proves nothing; a digest
written once and refused afterwards does.

This refuses to overwrite an existing freeze without `--force`, and `--force`
prints what changed. Deleting the freeze file is always possible -- nothing on
disk prevents an honest person from breaking a promise. It makes the change
*conspicuous*, which is the part that actually helps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/performance/downstream-eval-protocol.md"
FREEZE = ROOT / "docs/performance/downstream-eval-protocol.freeze.json"


def digest() -> str:
    return hashlib.sha256(DOC.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="re-freeze after an edit")
    args = parser.parse_args()

    current = digest()
    if FREEZE.exists() and not args.force:
        recorded = json.loads(FREEZE.read_text())
        if recorded["sha256"] == current:
            print(f"unchanged: {current}")
            print(f"frozen:   {recorded['frozen_at']} at {recorded['commit'][:7]}")
            return 0
        print("PROTOCOL HAS CHANGED SINCE IT WAS FROZEN")
        print(f"  frozen: {recorded['sha256']}")
        print(f"  now:    {current}")
        print("  re-freeze with --force only if the change was made BEFORE any scored run")
        return 1

    commit = subprocess_head()
    FREEZE.write_text(
        json.dumps(
            {"sha256": current, "frozen_at": "2026-10-11", "commit": commit, "scored_runs": 0},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"frozen {current} at {commit[:7]}")
    return 0


def subprocess_head() -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a detached checkout is fine
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
