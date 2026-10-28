"""CI check: sealed_runs.log must not exceed 2 entries before v0.10.0 is tagged.

One line per sealed run (timestamp, git SHA, condition set). Baseline + final
release run are the only two allowed before the v0.10.0 tag exists.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "benchmarks" / "agency_readiness" / "sealed_runs.log"


def git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def main() -> int:
    if not LOG.exists():
        print("sealed_runs.log missing")
        return 1
    lines = [l for l in LOG.read_text().splitlines() if l.strip()]
    valid = [l for l in lines if not l.startswith("INVALIDATED")]
    for l in lines:
        if l.startswith("INVALIDATED") and "reason:" not in l:
            print(f"invalidated entry missing reason: {l}")
            return 1
    try:
        tags = git("tag", "--list", "v0.10.0")
    except subprocess.CalledProcessError:
        tags = ""
    tagged = bool(tags)
    if len(valid) > 2 and not tagged:
        print(f"sealed_runs.log has {len(valid)} valid entries (>2) but v0.10.0 is not tagged")
        for l in valid:
            print("  ", l)
        return 1
    print(f"sealed_runs.log: {len(valid)} valid entry/entries ({len(lines)-len(valid)} invalidated), v0.10.0 tagged={tagged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
