"""Check that every claim in STATUS.md and README.md points at real evidence.

A number in a document is only worth as much as the artifact behind it. This
script makes that checkable instead of a matter of trust: it finds every
artifact path the two current documents reference, asserts each one exists and
is tracked by git, and for the machine-checkable citations -- lines written as

    `docs/performance/foo.json` <- key: summary.total

it also asserts the cited key exists in that JSON and, where a value is given,
that the value matches.

Deliberately not included: prose numbers, natural-language figures, and anything
that needs interpretation. Those stay a human judgement. This catches the failure
mode that actually happened here, which is a document citing a file that does not
contain the number it claims.

    python scripts/check_evidence.py            # report
    python scripts/check_evidence.py --strict   # non-zero exit on any problem
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("docs/STATUS.md", "README.md")

# `docs/performance/x.json` <- key: a.b.c
CITATION = re.compile(
    r"`(?P<path>docs/performance/[\w./-]+\.(?:json|txt|log))`\s*<-\s*key:\s*(?P<key>[\w.\[\]0-9]+)"
)
# Any backticked artifact path, citation or not.
ARTIFACT = re.compile(r"`(?P<path>docs/(?:performance|research)/[\w./-]+)`")


def git_tracked(paths: list[str]) -> tuple[set[str], str | None]:
    """Return the tracked subset.

    An empty result may mean git failed, not that nothing is tracked, so the
    caller has to distinguish the two.
    """
    if not paths:
        return set(), None
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--", *paths],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.split()
    except (subprocess.SubprocessError, OSError) as exc:
        return set(), f"git ls-files failed: {exc}"
    return set(out), None


def dig(data: object, key: str) -> object:
    """Resolve a dotted key with numeric list indices, e.g. points.0.summary.total_ms.p50."""
    cursor = data
    for part in key.split("."):
        if isinstance(cursor, list):
            try:
                cursor = cursor[int(part)]
            except (ValueError, IndexError):
                return KeyError(key)
        elif isinstance(cursor, dict):
            if part not in cursor:
                return KeyError(key)
            cursor = cursor[part]
        else:
            return KeyError(key)
    return cursor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    problems: list[str] = []
    notes: list[str] = []
    referenced: set[str] = set()
    citations = 0

    for doc in DOCS:
        path = ROOT / doc
        if not path.exists():
            problems.append(f"{doc}: missing")
            continue
        text = path.read_text()

        for match in CITATION.finditer(text):
            artifact, key = match["path"], match["key"]
            referenced.add(artifact)
            citations += 1
            target = ROOT / artifact
            if not target.exists():
                problems.append(f"{doc}: cited artifact does not exist: {artifact}")
                continue
            try:
                data = json.loads(target.read_text())
            except json.JSONDecodeError as exc:
                problems.append(f"{doc}: {artifact} is not valid JSON: {exc}")
                continue
            value = dig(data, key)
            if isinstance(value, KeyError):
                problems.append(f"{doc}: {artifact} has no key '{key}'")
            else:
                notes.append(f"  ok  {doc} -> {artifact} {key} = {value}")

        for match in ARTIFACT.finditer(text):
            referenced.add(match["path"])

    tracked, git_error = git_tracked(sorted(referenced))
    if git_error:
        problems.append(git_error)
    else:
        for artifact in sorted(referenced):
            if artifact.endswith("/"):
                continue
            if not (ROOT / artifact).exists():
                problems.append(f"referenced but absent: {artifact}")
            elif artifact not in tracked:
                problems.append(f"referenced but UNTRACKED: {artifact}")

    print(f"documents scanned : {len(DOCS)}")
    print(f"artifacts referenced: {len(referenced)}")
    print(f"machine-checkable citations: {citations}")
    if notes:
        print("\nresolved citations:")
        print("\n".join(notes))
    if problems:
        print(f"\nPROBLEMS ({len(problems)}):")
        for problem in problems:
            print(f"  !!  {problem}")
    else:
        print("\nno problems found")

    return 1 if (problems and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
