"""CI check: sealed_runs.log must not exceed 2 final entries per seed.

One line per sealed run (timestamp, git SHA, condition set). Baseline + final
release run are the only two allowed per seed before the v0.10.0 tag exists.

If MP_FINAL_SEED is set in the environment, the latest valid run must record it
in the log line (final deployments require an explicit seed).
"""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "benchmarks" / "agency_readiness" / "sealed_runs.log"


def git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main() -> int:
    if not LOG.exists():
        print("sealed_runs.log missing")
        return 1
    lines = [line for line in LOG.read_text().splitlines() if line.strip()]
    valid = [line for line in lines if not line.startswith("INVALIDATED")]
    per_seed: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        if line.startswith("INVALIDATED") and "reason:" not in line:
            print(f"invalidated entry missing reason: {line}")
            return 1
        parts = line.split()
        offset = 1 if parts and parts[0] == "INVALIDATED" else 0
        if len(parts) < offset + 3 or not parts[offset + 1].isdigit():
            print(f"sealed_runs.log entry is missing a numeric seed: {line}")
            return 1
    for line in valid:
        parts = line.split()
        seed = parts[offset + 1]
        kind = parts[offset + 3] if len(parts) > offset + 3 else "FINAL"
        if kind != "DEV_SET":
            per_seed[seed].append(line)

    # Check max 2 valid runs per seed
    over_limit = []
    for seed, runs in per_seed.items():
        if len(runs) > 2:
            over_limit.append((seed, len(runs), runs))

    try:
        tags = git("tag", "--list", "v0.10.0")
    except subprocess.CalledProcessError:
        tags = ""
    tagged = bool(tags)

    if over_limit:
        if not tagged:
            for seed, count, runs in over_limit:
                print(
                    f"sealed_runs.log has {count} valid entries for seed {seed} "
                    "(>2) but v0.10.0 is not tagged"
                )
                for entry in runs:
                    print("  ", entry)
            return 1

    # MP_FINAL_SEED handling: if set, the latest valid run must record it
    mp_final_seed = __import__("os").environ.get("MP_FINAL_SEED")
    if mp_final_seed:
        latest_valid = valid[-1] if valid else ""
        if mp_final_seed not in latest_valid:
            print(f"MP_FINAL_SEED={mp_final_seed} not recorded in latest valid run: {latest_valid}")
            return 1

    n_invalidated = len(lines) - len(valid)
    total_valid = len(valid)
    print(
        f"sealed_runs.log: {total_valid} valid entry/entries "
        f"({len(per_seed)} seed(s)), {n_invalidated} invalidated, v0.10.0 tagged={bool(tags)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
