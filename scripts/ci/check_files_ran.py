#!/usr/bin/env python3
"""check_files_ran.py — verify that every test in the listed files actually ran.

Reads the junitxml produced by "Run tests" and, for each file in the
mandatory list, asserts:
  - collected == passed (no failures, errors, or skipped)
  - skipped == 0
  - the file contributed at least one testcase (so a renamed or deleted
    file cannot silently disappear from the list)

The expected count per file comes from `pytest --collect-only -q <file>`,
run in the same job, so the gate cannot rot as tests are added.

Usage:
    python scripts/ci/check_files_ran.py --junit /tmp/pytest-main.xml --files file1 file2 ...
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def collect_test_ids(files, extra_args=None):
    """Run pytest --collect-only -q on the given files and return
    {file_path: set of test_ids}."""
    collected = defaultdict(set)
    cmd = ["python", "-m", "pytest", "--collect-only", "-q"] + files
    if extra_args:
        cmd += extra_args
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Parse output: lines like "tests/test_foo.py::test_bar"
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith(" "):
            # Extract file path (everything before first ::)
            file_path = line.split("::")[0]
            collected[file_path].add(line)
    return collected


def parse_junit(path):
    """Parse junitxml and return {file_path: {passed, skipped, failed, errors, test_ids}}."""
    tree = ET.parse(path)
    root = tree.getroot()
    per_file = defaultdict(
        lambda: {"passed": 0, "skipped": 0, "failed": 0, "errors": 0, "ids": set()}
    )
    for tc in root.iter("testcase"):
        # testcase has attributes: classname, name, time
        # The classname is typically the module path: e.g. "tests.test_tenant_isolation"
        classname = tc.get("classname", "")
        # Convert module path to file path: tests.test_foo -> tests/test_foo.py
        file_path = classname.replace(".", "/") + ".py"
        # Handle test classes (classname may include class name)
        # Extract just the module path
        if "/" in file_path:
            parts = file_path.split("/")
            # Remove the last part if it looks like a class name (PascalCase)
            if parts[-1][0].isupper():
                file_path = "/".join(parts[:-1]) + ".py"

        tc_id = f"{tc.get('classname')}.{tc.get('name')}"

        skipped = tc.find("skipped")
        failure = tc.find("failure")
        error = tc.find("error")

        if skipped is not None:
            per_file[file_path]["skipped"] += 1
        elif failure is not None:
            per_file[file_path]["failed"] += 1
        elif error is not None:
            per_file[file_path]["errors"] += 1
        else:
            per_file[file_path]["passed"] += 1

        per_file[file_path]["ids"].add(tc_id)

    return per_file


def main():
    parser = argparse.ArgumentParser(description="Verify all listed files' tests ran.")
    parser.add_argument("--junit", required=True, help="Path to junitxml from Run tests")
    parser.add_argument("--files", nargs="+", required=True, help="Files to check")
    parser.add_argument(
        "--deselect", action="append", default=[], help="Test ID to exclude from collection"
    )
    args = parser.parse_args()

    # Get expected test counts from --collect-only
    extra_args = []
    for d in args.deselect:
        extra_args += ["--deselect", d]
    expected = collect_test_ids(args.files, extra_args=extra_args)
    collected_counts = {f: len(ids) for f, ids in expected.items()}

    # Parse the junit
    per_file = parse_junit(args.junit)

    # Check each file
    errors = []
    for f in args.files:
        # Find the matching file in the junit (may have different path prefix)
        junit_key = None
        for key in per_file:
            if key.endswith(f) or key == f or f.endswith(key):
                junit_key = key
                break

        if junit_key is None:
            errors.append(f"{f}: NO testcases found in junit (file missing or no tests ran)")
            continue

        info = per_file[junit_key]
        expected_count = collected_counts.get(f, 0)

        # Check 1: file contributed at least one testcase
        if expected_count == 0:
            errors.append(f"{f}: NO testcases collected (file contributed zero tests)")
            continue

        # Check 2: all non-skipped tests passed
        if info["passed"] != expected_count:
            errors.append(
                f"{f}: passed={info['passed']} collected={expected_count} "
                f"(failed={info['failed']} errors={info['errors']} skipped={info['skipped']})"
            )

        # Check 3: no unexpected failures or errors
        if info["failed"] > 0:
            errors.append(f"{f}: {info['failed']} failed (expected 0)")
        if info["errors"] > 0:
            errors.append(f"{f}: {info['errors']} errors (expected 0)")

    if errors:
        print("::error::check_files_ran failed:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print("check_files_ran: all files passed (passed == collected, no failures or errors)")


if __name__ == "__main__":
    main()
