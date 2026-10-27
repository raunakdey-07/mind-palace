"""Git-verified documentation truth check."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "docs" / "STATUS.md", ROOT / "docs" / "release-map.md", ROOT / "CHANGELOG.md"]


def git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def tags():
    return set(git("tag", "--list").split())


def resolve(ref):
    try:
        return git("rev-parse", ref)
    except subprocess.CalledProcessError:
        return None


def main():
    problems = []
    pyproject = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    version = m.group(1) if m else None
    if version is None:
        problems.append("could not read version from pyproject.toml")
    else:
        status_path = ROOT / "docs" / "STATUS.md"
        status = status_path.read_text() if status_path.exists() else ""
        if version not in status:
            problems.append("STATUS.md does not mention current version " + version)

    all_tags = tags()

    for doc in DOCS:
        if not doc.exists():
            problems.append("missing document: " + str(doc.relative_to(ROOT)))
            continue
        text = doc.read_text()
        for tag in set(re.findall(r"`(v0\.\d+\.\d+(?:\.\d+)?)`", text)):
            if tag not in all_tags:
                # The current release tag may not exist yet before PR3 publishes it.
                if tag != "v" + version and tag != "v0.10.0":
                    problems.append(doc.name + ": tag " + tag + " cited but does not exist in git")
        # Commit-hash verification only in release-map and CHANGELOG; STATUS.md
        # contains CI run ids that are not commits.
        if doc.name in ("release-map.md", "CHANGELOG.md"):
            for h in set(re.findall(r"`([0-9a-f]{7,12})`", text)):
                if resolve(h) is None:
                    problems.append(doc.name + ": commit hash " + h + " does not resolve")

    # immutable tag check
    if "v0.9.3" in all_tags:
        try:
            if not git("rev-parse", "v0.9.3").startswith("cb04ccb"):
                problems.append("v0.9.3 tag moved from cb04ccb")
        except Exception:
            problems.append("v0.9.3 tag missing")

    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  !!", p)
        return 1
    print("release truth: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
