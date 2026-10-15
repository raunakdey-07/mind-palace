"""Fail the build when a test skips for a reason nobody allowed.

    python scripts/check_skips.py --junit /tmp/pytest-main.xml

## Why this exists

A skip is invisible in a green build. `1619 passed, 8 skipped` reads like a
result, and the eight are as likely to be eight broken tests as eight opt-in
ones. Nothing in `pytest` distinguishes the two, so nothing did.

That is not hypothetical, it is the last four sessions. Six tests in
`tests/test_ingestion_statistics.py` skipped on every runner and passed on every
workstation, and the skip reason they carried --

    no reachable DATABASE_URL

-- was a lie. The database was up and reachable; `create_engine` had resolved
bare `postgresql://` to psycopg (v3) under SQLAlchemy 2.1, the project installs
`psycopg2-binary`, and `except Exception: return False` turned a
`ModuleNotFoundError` into "no database". Reproduced 2026-10-13 on one reachable
database, changing only the SQLAlchemy version:

    2.0.46 -> psycopg2 -> 6 passed
    2.1.4  -> psycopg  -> 6 skipped

Four sessions read "8 skipped", saw two of them explained, and did not ask about
the other six. The count was a number nobody could act on.

## What this gate does and does not claim

It fails the job when a skipped test is not on the allowlist below. That is a
claim about *accountability*, not about correctness: an allowlisted skip is a
skip somebody looked at and wrote down.

It cannot tell you a skip is harmless. It can only tell you every skip is either
on the list or somebody's problem.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: The skips this project has decided to allow, and why each one exists.
#:
#: Both are opt-in for a substantive reason -- one creates and drops a database,
#: the other spends minutes building a wheel -- and both are run deliberately by
#: CI on their own steps. They are keyed on the module and a fragment of the
#: reason, not on a line number, because a line number moves and a name does not.
#:
#: Keying on the reason fragment rather than the test name is deliberate: a test
#: whose *reason* changed is a different decision and needs a new entry.
ALLOWLIST: dict[str, tuple[str, str]] = {
    "test_migrations_apply_downgrade_and_reapply": (
        "MIND_PALACE_MIGRATION_ROUNDTRIP",
        "creates and drops a database, so it is opt-in; CI runs it in its own "
        "step with the variable set",
    ),
    "test_a_built_wheel_contains_every_declared_module": (
        "MIND_PALACE_TEST_BUILD_WHEEL",
        "builds a wheel, which takes minutes; CI builds one in the wheel job",
    ),
}


def skipped_cases(junit: Path) -> list[tuple[str, str, str]]:
    """(classname, name, reason) for every skipped testcase in a junitxml."""
    root = ET.parse(junit).getroot()
    suites = root.iter("testsuite") if root.tag == "testsuites" else [root]
    out: list[tuple[str, str, str]] = []
    for suite in suites:
        for case in suite.iter("testcase"):
            for skip in case.findall("skipped"):
                out.append(
                    (
                        case.get("classname", "") or "",
                        case.get("name", "") or "",
                        (skip.get("message", "") or "").strip(),
                    )
                )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", required=True, type=Path)
    args = parser.parse_args()

    if not args.junit.exists():
        print(f"::error::no junitxml at {args.junit}")
        return 1

    cases = skipped_cases(args.junit)
    unexpected: list[tuple[str, str, str]] = []
    allowed: list[str] = []

    for classname, name, reason in cases:
        entry = ALLOWLIST.get(name)
        if entry is None:
            unexpected.append((classname, name, reason))
        elif entry[0] not in reason:
            # The opt-in variable has to still be the stated reason. A test can
            # keep its name and start skipping for something else entirely.
            unexpected.append((classname, name, reason))
        else:
            allowed.append(f"{name} (opt-in via {entry[0]})")

    print(f"skipped tests read from {args.junit}: {len(cases)}")
    for line in sorted(allowed):
        print(f"  ok  allowlisted: {line}")

    if unexpected:
        print(f"\nUNALLOWED SKIPS ({len(unexpected)}):")
        for classname, name, reason in unexpected:
            print(f"  !!  {classname}::{name}")
            print(f"      reason: {reason or '(none recorded)'}")
        print(
            "\nA skip that is not on scripts/check_skips.py's ALLOWLIST fails the "
            "build.\n"
            "If it is deliberate, add it there with the reason it is deliberate.\n"
            "If it is a defect, fix the defect -- do not widen the allowlist to "
            "make the\nbuild green, and do not delete or weaken the test."
        )
        return 1

    print(f"\nevery skip is allowlisted ({len(allowed)} of {len(cases)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
