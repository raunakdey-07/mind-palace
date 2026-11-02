#!/usr/bin/env python3
"""Benchmark: scope-token enforcement overhead on recall latency.

Manual or nightly run; not gated in CI. Latency numbers for enforcement ON vs
OFF vs at revision 014 are recorded here, while CI keeps only deterministic
checks (plan shape, function-call counts, row counts).

Usage:
    DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/<db> \\
    MIND_PALACE_SCOPE_SECRET=<secret> \\
    python scripts/bench_enforcement_latency.py [--n 200]

Requires: a database migrated to head, runtime roles provisioned, and a secret
rotated into memory_server_secrets.
"""

from __future__ import annotations

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--n", type=int, default=200, help="Number of recall requests per condition"
    )
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL must be set", file=sys.stderr)
        sys.exit(1)
    secret = os.environ.get("MIND_PALACE_SCOPE_SECRET")
    if not secret:
        print("ERROR: MIND_PALACE_SCOPE_SECRET must be set", file=sys.stderr)
        sys.exit(1)

    print(f"Enforcement latency benchmark: n={args.n}")
    print(f"DATABASE_URL={url}")
    print(f"MIND_PALACE_SCOPE_SECRET={secret[:16]}...")
    print("Not yet implemented: wire up recall latency measurements here.")


if __name__ == "__main__":
    main()
