"""Freeze the retrieval lab protocol's digest, once.

    python scripts/freeze_lab_protocol.py          # freeze, or verify
    python scripts/freeze_lab_protocol.py --force  # re-freeze after an edit

A digest that can be recomputed at will proves nothing. One written once and
refused afterwards does -- and it makes the one thing worth catching conspicuous:
a target or a threshold edited after the curve was seen.

Deleting the freeze file is always possible. Nothing on disk prevents an honest
person from breaking a promise; what it does is make the break visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/performance/retrieval-lab-protocol.md"
FREEZE = ROOT / "docs/performance/retrieval-lab-protocol.freeze.json"


def digest() -> str:
    return hashlib.sha256(DOC.read_bytes()).hexdigest()


def head() -> str:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    current = digest()
    if FREEZE.exists() and not args.force:
        recorded = json.loads(FREEZE.read_text())
        if recorded["sha256"] == current:
            print(f"unchanged: {current}")
            return 0
        print("PROTOCOL HAS CHANGED SINCE IT WAS FROZEN")
        print(f"  frozen: {recorded['sha256']}")
        print(f"  now:    {current}")
        print("  re-freeze with --force only if the change predates every scored run")
        return 1

    FREEZE.write_text(
        json.dumps(
            {"sha256": current, "frozen_at": "2026-10-11", "commit": head(), "scored_runs": 0},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"frozen {current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
