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

#: Written only by an actual pytest run. See `check_test_counts`.
RUN_RECORD = "docs/performance/test-runs.json"

#: Links that are allowed to point at something git does not track, with the
#: reason. `/eval/results/` is excluded by .gitignore on purpose: it is the
#: output of an experiment, not an input, and it is large. The documents that
#: cite it were written by the person who ran the experiment.
ALLOWED_DIRS = ("eval/results",)  # checked above on the resolved path

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


def _recorded_run_counts() -> dict[str, str]:
    """Read the record of real suite runs, keyed by commit.

    Written only by `scripts/record_test_run.py`, which in turn only runs after an
    actual pytest invocation. It exists so a document claiming "N passed" can be
    compared with a number somebody actually observed, rather than with whatever
    the author believed that day.
    """
    path = ROOT / RUN_RECORD
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    runs = data.get("runs")
    if not isinstance(runs, dict):
        return {}
    return {
        run_id: entry["passed"]
        for run_id, entry in runs.items()
        if isinstance(entry, dict) and re.fullmatch(r"\d+", str(entry.get("passed", "")))
    }


def check_test_counts(text_by_doc: dict[str, str], head: str | None) -> tuple[list[str], list[str]]:
    """Every "N passed" a document states must match a recorded run.

    This exists because of one specific failure. STATUS.md carried `1583 passed`
    for a milestone; no run had produced 1583, and the real count was 1567.
    Nothing in the build caught it, because a plausible number in a table looks
    exactly like a measured one until it is compared with something.

    If no run has ever been recorded the check says so rather than passing
    quietly -- silence in that state is how an unmeasured figure survives.
    """
    problems: list[str] = []
    notes: list[str] = []
    recorded = _recorded_run_counts()
    seen = sorted({int(v) for v in recorded.values()})

    for doc, text in text_by_doc.items():
        stated = {int(n.replace(",", "")) for n in re.findall(r"(\d[\d,]*)\s+passed", text)}
        if not stated:
            continue
        if not recorded:
            problems.append(
                f"{doc}: states {'/'.join(str(n) for n in sorted(stated))} passed but "
                f"{RUN_RECORD} records no run. Record one with scripts/record_test_run.py, "
                f"or remove the figure."
            )
            continue
        for count in sorted(stated):
            if count not in seen:
                problems.append(
                    f"{doc}: claims {count} passed; no run in {RUN_RECORD} produced that "
                    f"(recorded counts: {', '.join(str(n) for n in seen)})"
                )
            else:
                commits = sorted(run_id for run_id, v in recorded.items() if int(v) == count)
                notes.append(f"  ok  {doc}: {count} passed matches run(s) {', '.join(commits)}")

    if head and not any(head[:7] in run_id for run_id in recorded):
        notes.append(
            f"  --  no run recorded for HEAD {head[:7]}; counts were checked against "
            f"earlier runs"
        )
    return problems, notes


def check_markdown_links() -> tuple[list[str], list[str]]:
    """Every relative markdown link must point at a file git tracks.

    This is not a link-rot check. It is a *shipping* check: a page the README
    points at that `.gitignore` excludes is a dead link in every clone, and it
    looks fine on the machine of whoever wrote it.

    That is not hypothetical. Five pages the README cited -- errors.md,
    providers.md, deletion-and-erasure.md and two others -- were untracked,
    because `/docs/*` and `*.md` exclude them and nobody had added an exception.
    A fresh `git clone` got a README whose links all 404.

    Tracked-ness rather than existence is the right test precisely because it is
    the thing that was wrong.
    """
    problems: list[str] = []
    notes: list[str] = []
    allowances: list[str] = []

    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.splitlines()
    except (subprocess.SubprocessError, OSError) as exc:
        return [f"git ls-files failed: {exc}"], []

    tracked = {str((ROOT / t).resolve()) for t in out}
    # A link to a directory is fine when *something* inside it is tracked.
    tracked_parents = {str(Path(t).parent) for t in tracked} | tracked

    pattern = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
    checked = 0
    for doc in sorted(ROOT.rglob("*.md")):
        if any(part in {".git", "venvmp", "build", "dist", "node_modules"} for part in doc.parts):
            continue
        for raw in pattern.findall(doc.read_text(encoding="utf-8", errors="ignore")):
            if raw.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = raw.partition("#")[0]
            if not target:
                continue
            checked += 1
            resolved = (doc.parent / target).resolve()
            # Matched on the resolved path, not the raw string: the same artifact
            # is reached as `eval/results/x.json` and `../../eval/results/x.json`.
            if resolved.is_relative_to(ROOT / "eval" / "results"):
                allowances.append(f"{doc.relative_to(ROOT)} -> {raw}")
                continue
            if not resolved.exists():
                problems.append(f"{doc.relative_to(ROOT)}: link target does not exist: {raw}")
            elif str(resolved) not in tracked_parents:
                # tracked_parents holds tracked files *and* the directories holding
                # them, so a link to either passes. Anything left is a file that
                # exists on this machine and in nobody's clone.
                problems.append(
                    f"{doc.relative_to(ROOT)}: link target is NOT TRACKED BY GIT: {raw} "
                    f"-- ships as a dead link in every clone"
                )
    if allowances:
        notes.append(
            f"  --  {len(allowances)} link(s) point at deliberately untracked local "
            f"artifacts under eval/: frozen experiment output, excluded by .gitignore"
        )
    notes.append(f"  --  {checked} relative markdown links resolve to tracked files")
    return problems, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    problems: list[str] = []
    notes: list[str] = []
    referenced: set[str] = set()
    citations = 0
    text_by_doc: dict[str, str] = {}

    for doc in DOCS:
        path = ROOT / doc
        if not path.exists():
            problems.append(f"{doc}: missing")
            continue
        text = path.read_text()
        text_by_doc[doc] = text

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

    head = None
    try:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    count_problems, count_notes = check_test_counts(text_by_doc, head)
    problems.extend(count_problems)
    notes.extend(count_notes)

    link_problems, link_notes = check_markdown_links()
    problems.extend(link_problems)
    notes.extend(link_notes)

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
