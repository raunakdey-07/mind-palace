"""Every path the project's claim-bearing documents name must exist in git.

    python scripts/check_doc_paths.py
    python scripts/check_doc_paths.py --verbose

## Why this exists

Four documents were written, reported as deliverables, and **never committed**:
`docs/wedge-decision-note.md`, `docs/release/tag-checklist.md`,
`docs/release/v1.0.0-readiness.md` and
`docs/evaluation/m007-jev-decision-layer-spike.md`. All four were silently
dropped by a blanket `*.md` in `.gitignore` under a hand-maintained list of
exceptions. A clone of the repository did not have them, and
`docs/PROGRESS.md` quoted figures from one of them for three sessions.

`scripts/check_evidence.py::check_markdown_links` did not catch it, and would
not have:

* it only scans markdown **links** `](path)`. These files are named in backticks
  in prose, not linked, so there was nothing to resolve;
* its `ALLOWED_DIRS` explicitly permits `eval/results`, which is where nine
  **frozen experiment artifacts** live -- all gitignored, and several cited by
  name from tracked documents.

"It resolved on my machine" is the exact failure this ends. A path that exists
but is not in `git ls-files` ships as a dead reference in every clone but the
author's.

## Scope, and what is deliberately outside it

**Blocking:** the documents that carry the project's claims -- `README.md`,
`CHANGELOG.md`, `docs/STATUS.md`, `docs/PROGRESS.md`, `docs/release-map.md`.

**Advisory, not blocking:** `docs/research/`, `docs/withdrawn/` and
`examples/`. Those carry rot that predates this gate -- stale paths to files
deleted months ago. Listing them is useful; failing the build on them would make
this gate cry wolf, and a gate that cries wolf gets deleted rather than fixed.

A **directory** counts as tracked when any tracked file sits under it. Git
tracks files, not directories, and `docs/security.md` naming
`examples/marketing-campaign-memory` is a live reference to real tracked code.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The documents whose claims this gate protects. Exactly the set the brief
#: names: PROGRESS.md, STATUS.md, the release map and the changelog, plus the
#: README because it is the first thing anybody reads.
DOCS = (
    "README.md",
    "CHANGELOG.md",
    "docs/STATUS.md",
    "docs/PROGRESS.md",
    "docs/release-map.md",
)

#: Advisory only. Reported, never fatal.
ADVISORY = ("docs/research", "docs/withdrawn", "examples")

#: A repo-rooted path-shaped token. Narrow on purpose: a loose pattern matches
#: prose, and a gate that matches prose is noise.
PATH = re.compile(
    r"\b((?:docs|eval|scripts|tests|api|cli|migrations|examples|content|content_eval)/"
    r"[\w./*-]+\.(?:md|json|yaml|yml|py|sh|txt|xml|toml))\b"
)

LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)\)")


def tracked() -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.split()
    return set(out)


def is_tracked(rel: str, known: set[str]) -> bool:
    """A path is tracked if git knows it, or if a tracked file sits under it."""
    if rel in known:
        return True
    prefix = rel.rstrip("/") + "/"
    return any(p.startswith(prefix) for p in known)


def untracked_not_ignored() -> list[str]:
    """Files git has never heard of AND no rule hides.

    This is the state that hid seven documents. `--ignored` would not list them
    (nothing ignores them), and `git ls-files` would not list them (nothing
    tracks them), so every existing check passed while they were invisible.
    """
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.split()
    return [p for p in out if p.startswith(("docs/", "eval/")) and not p.endswith(".pyc")]


def ignored_documents() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--others", "--ignored", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.split()
    return [
        p
        for p in out
        if "__pycache__" not in p and not p.endswith(".pyc") and p.startswith(("docs/", "eval/"))
    ]


def candidates_in(doc: Path) -> set[tuple[str, bool]]:
    """`(target, doc_relative)` pairs.

    The flag matters, and getting it wrong is the whole bug. A markdown link is
    **always** relative to the containing document: `docs/STATUS.md` writing
    `[x](performance/five-minute-path.md)` means `docs/performance/...`. A
    repo-rooted path token -- `eval/results/x.json` appearing in prose -- is
    always root-relative no matter where it appears. Resolving a link against the
    root reports five files in `docs/` as missing that exist.

    A path on a line that *writes* it is not a reference. The README's
    `--save eval/results/memory.json` names a file the command is about to
    create; demanding it already exist would be demanding the opposite of what
    the line says.
    """
    found: set[tuple[str, bool]] = set()
    for line in doc.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "--save" in line or "--out" in line:
            continue
        found |= {(raw, False) for raw in PATH.findall(line)}
        found |= {
            (raw, True)
            for raw in LINK.findall(line)
            if not raw.startswith(("http", "mailto:", "#"))
        }
    return found


def resolve(doc: Path, target: str, doc_relative: bool) -> Path:
    if doc_relative:
        local = (doc.parent / target).resolve()
        return local if local.exists() else (ROOT / target)
    return ROOT / target


def scan(paths: tuple[str, ...], known: set[str]) -> tuple[list[str], int]:
    problems: list[str] = []
    checked = 0
    for relative in paths:
        doc = ROOT / relative
        if not doc.exists():
            problems.append(f"{relative}: listed as a claim-bearing document but missing")
            continue
        for raw, doc_relative in sorted(candidates_in(doc)):
            target = raw.split("#", 1)[0]
            if not target or "*" in target:
                continue
            resolved = resolve(doc, target, doc_relative)
            if resolved is None:
                continue
            try:
                rel = str(resolved.relative_to(ROOT))
            except ValueError:
                continue
            checked += 1
            if not is_tracked(rel, known):
                state = (
                    "EXISTS ON DISK BUT IS NOT TRACKED" if resolved.exists() else "does not exist"
                )
                problems.append(f"{relative}: names {rel} -- {state}")
    return problems, checked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    known = tracked()

    problems, checked = scan(DOCS, known)
    advisory: list[str] = []
    advisory_checked = 0
    for base in ADVISORY:
        for md in sorted((ROOT / base).rglob("*.md")):
            found, n = scan((str(md.relative_to(ROOT)),), known)
            advisory += found
            advisory_checked += n

    ignored = ignored_documents()
    # Every .md under docs/ or eval/ must land in exactly one of three states:
    #
    #   1. TRACKED                                     -- normal, and the goal
    #   2. ignored AND named explicitly in .gitignore  -- a decision somebody wrote
    #   3. neither                                      -- FORGOTTEN, and the failure
    #
    # State 3 is the one that produced this gate. Seven documents sat there for
    # months: written, reported as deliverables, in no clone, and invisible to
    # every check because nothing looked for "untracked and not ignored".
    # State 2 without the naming -- ignored by a blanket rule nobody re-read -- is
    # the same failure wearing a disguise.
    gitignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8", errors="ignore")
    deliberate = {
        line.strip().lstrip("/")
        for line in gitignore_text.splitlines()
        if line.strip() and not line.strip().startswith("#") and line.strip().endswith(".md")
    }

    for path in ignored:
        if path.endswith(".md") and path not in deliberate:
            problems.append(
                f"IGNORED DOCUMENT: {path} -- ignored under docs/ or eval/, but no "
                f".gitignore rule names that exact path. Name it if the decision is "
                f"deliberate."
            )

    forgotten = untracked_not_ignored()
    for path in forgotten:
        if path.endswith(".md"):
            problems.append(
                f"FORGOTTEN FILE: {path} -- a Markdown file under docs/ or eval/ that git "
                f"neither tracks nor ignores. `git add -A` will not stage it unless "
                f"forced, and nothing has failed until now. Commit it, or add an "
                f"explicit .gitignore rule naming it."
            )

    print(f"claim-bearing documents : {len(DOCS)}")
    print(f"paths checked           : {checked} (blocking)")
    print(f"paths checked           : {advisory_checked} (advisory, not blocking)")
    print(f"tracked files           : {len(known)}")
    print(f"ignored, non-cache files under docs/ and eval/ : {len(ignored)}")

    if advisory:
        print(f"\nADVISORY ({len(advisory)}) -- pre-existing rot, not failing the build:")
        for line in advisory:
            print(f"  --  {line}")
        if args.verbose:
            pass

    if problems:
        print(f"\nPROBLEMS ({len(problems)}):")
        for problem in problems:
            print(f"  !!  {problem}")
        print(
            "\nA claim-bearing document may only name a path git tracks. To track an\n"
            "ignored file, add an explicit exception in .gitignore -- do not force-add\n"
            "one file and leave the rule that hid it in place for the next one."
        )
        return 1

    print("\nevery path the claim-bearing documents name is tracked by git")
    return 0


if __name__ == "__main__":
    sys.exit(main())
