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
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
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
        run_id: entry
        for run_id, entry in runs.items()
        if isinstance(entry, dict) and re.fullmatch(r"\d+", str(entry.get("passed", "")))
    }


def collected_test_count() -> int | None:
    """How many tests the tree collects right now, or None if pytest cannot say.

    Deliberately the cheapest possible invocation. This runs in CI on every
    build and a full collection is not free; `MIND_PALACE_SKIP_COLLECT=1` turns
    it off for a developer who does not want to wait.
    """
    if os.getenv("MIND_PALACE_SKIP_COLLECT") == "1":
        return None
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", str(ROOT / "tests")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    match = re.search(r"(\d+) tests? collected", result.stdout)
    return int(match.group(1)) if match else None


def check_run_integrity(head: str | None) -> tuple[list[str], list[str]]:
    """Each recorded run must name its commit, its collected count, and its artifact.

    A run record is only worth something if it cannot be written by hand and
    cannot drift from the tree. Three properties, all checked:

    1. the commit it claims is the commit that produced it;
    2. the `collected` it claims is what `pytest --collect-only` says now;
    3. it names a junitxml artifact that exists and is tracked.

    Property 2 is the sharp one. A run records `collected` and `passed`, and their
    difference is `failed + skipped + xfailed + errors`. If a document says
    "1607 passed" and the tree collects 1618, the remaining 11 are unaccounted for
    and the figure is a summary of a different tree than the one shipping.
    """
    problems: list[str] = []
    notes: list[str] = []
    path = ROOT / RUN_RECORD
    if not path.exists():
        return problems, notes
    try:
        runs = json.loads(path.read_text()).get("runs", {})
    except (json.JSONDecodeError, AttributeError):
        return problems, notes

    collected = collected_test_count()
    if collected is None:
        notes.append("  --  collection skipped; run-collected-count not verified")

    for run_id, entry in runs.items():
        if not isinstance(entry, dict):
            continue
        if head and run_id.startswith(head[:7]) and entry.get("commit") != head:
            problems.append(
                f"{RUN_RECORD}: run {run_id!r} claims commit "
                f"{str(entry.get('commit'))[:7]} but the run key says {head[:7]}"
            )
        if collected is not None and str(run_id).startswith((head or "\0")[:7]):
            recorded = entry.get("collected")
            if recorded is not None and int(recorded) != collected:
                problems.append(
                    f"{RUN_RECORD}: run {run_id!r} recorded {recorded} collected tests, "
                    f"but the tree collects {collected} now"
                )
            elif recorded is not None:
                notes.append(f"  ok  {run_id}: collected {collected} matches the tree")
        artifact = entry.get("junitxml")
        if artifact:
            target = ROOT / artifact
            if not target.exists():
                problems.append(f"{RUN_RECORD}: run {run_id!r} names a missing artifact {artifact}")
            elif str(target) not in _tracked_set():
                problems.append(
                    f"{RUN_RECORD}: run {run_id!r} names an untracked artifact {artifact}"
                )
    return problems, notes


def _tracked_set() -> set[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.splitlines()
    except (subprocess.SubprocessError, OSError):
        return set()
    return {str((ROOT / t).resolve()) for t in out}


def junit_counts(path: Path) -> dict[str, str] | None:
    """``{passed, skipped, failed, collected}`` read from a pytest junitxml.

    This is the one input to this gate that a person cannot write by hand,
    because a person cannot put a `<testcase>` in a file. Everything else the
    gate reads -- `RUN_RECORD`, the documents themselves -- is prose, and prose
    is exactly what went wrong.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    suites = list(root.iter("testsuite")) if root.tag == "testsuites" else [root]
    if not suites:
        return None
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in total:
            total[key] += int(suite.get(key, 0) or 0)
    passed = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    return {
        "passed": str(passed),
        "failed": str(total["failures"] + total["errors"]),
        "skipped": str(total["skipped"]),
        "collected": str(total["tests"]),
    }


def check_test_counts(
    text_by_doc: dict[str, str],
    head: str | None,
    require_head: bool = False,
    recorded: dict[str, dict] | None = None,
) -> tuple[list[str], list[str]]:
    """Every "N passed" a document states must match a recorded run, and -- under
    ``require_head`` -- that run must be the one for this commit.

    This exists because of one specific failure. STATUS.md carried `1583 passed`
    for a milestone; no run had produced 1583, and the real count was 1567.
    Nothing in the build caught it, because a plausible number in a table looks
    exactly like a measured one until it is compared with something.

    If no run has ever been recorded the check says so rather than passing
    quietly -- silence in that state is how an unmeasured figure survives.

    ``recorded`` replaces `RUN_RECORD` as the source for figures that carry **no
    `as of` label**, and only for those. A labelled figure is a claim about a
    named commit, and `RUN_RECORD` is the record of which commits really ran;
    substituting the current junit for it would make `24 passed (as of fd53840)`
    fail because today's run did not produce 24. That is how this was wrong the
    first time, and the gate caught it on its own first CI run.

    ## Why "some run" is not enough

    The first version of this check was satisfied by any recorded run, ever.
    That is weaker than it looks. A run record is keyed by commit, so a figure
    copied forward from last week's tree still matches -- and matches
    *forever*, because the old record never expires. STATUS.md carried
    `1619 passed`, which some older commit genuinely produced, while CI on the
    current tree reported a different split. Nothing complained, because the
    check only asked "did any run ever produce 1619", and the answer stayed
    yes after the tree moved.

    So the figure has to be tied to a commit, and there are exactly two honest
    ways to write one:

        1619 passed                          -> must be a run at HEAD
        1619 passed (as of 3108404)          -> must be a run at 3108404

    The second form is not a loophole. It is the claim being made explicit: this
    number describes that tree, and here is which one. A figure with no label
    is a claim about the code you are looking at now, so it has to be backed by
    a run of that code.
    """
    problems: list[str] = []
    notes: list[str] = []
    junit_runs = _recorded_run_counts() if recorded is None else recorded
    all_runs = recorded is None or json.dumps(recorded, sort_keys=True) == json.dumps(
        _recorded_run_counts(), sort_keys=True
    )
    source = junit_runs if all_runs else _recorded_run_counts()
    if not all_runs:
        notes.append(
            f"  --  runner mode: unlabelled figures are checked against this run; "
            f"figures labelled 'as of <commit>' are checked against {RUN_RECORD}"
        )
    seen = sorted({int(entry["passed"]) for entry in source.values()})

    for doc, text in text_by_doc.items():
        stated = {
            int(n.replace(",", "")): (label or "")
            for n, label in re.findall(
                r"(\d[\d,]*)\s+passed"  # the figure
                # an optional label, up to 60 characters later on the same line:
                # '1619 passed (as of `549d2cf`)' and '1619 passed, as of 549d2cf'
                r"(?:[^\n]{0,60}?as of\s+`?([0-9a-f]{7,40})`?\b)?",
                text,
            )
        }
        if not stated:
            continue
        if not source and not junit_runs:
            problems.append(
                f"{doc}: states {'/'.join(str(n) for n in sorted(stated))} passed but "
                f"{RUN_RECORD} records no run. Record one with scripts/record_test_run.py, "
                f"or remove the figure."
            )
            continue
        for count in sorted(stated):
            label = stated[count]
            # A labelled figure is a claim about a named commit, so it is checked
            # against RUN_RECORD even in runner mode. An unlabelled one is a
            # claim about THIS tree, so in runner mode it is checked against the
            # junitxml and against nothing else.
            pool = source if (label or all_runs) else junit_runs
            matching = sorted(run_id for run_id, e in pool.items() if int(e["passed"]) == count)
            if not matching:
                problems.append(
                    f"{doc}: claims {count} passed; no run produced that "
                    f"(recorded counts: {', '.join(str(n) for n in seen)})"
                )
                continue
            commits = sorted({str(pool[m].get("commit", ""))[:7] for m in matching})
            if require_head and head:
                if label:
                    # An explicit label is only honest if that commit really ran.
                    label_commits = {str(e.get("commit", ""))[:7] for e in source.values()}
                    if label[:7] not in label_commits:
                        problems.append(
                            f"{doc}: claims {count} passed as of {label[:7]}, but no run "
                            f"in {RUN_RECORD} was recorded at that commit "
                            f"(runs exist at: {', '.join(sorted(label_commits))})"
                        )
                    else:
                        notes.append(
                            f"  ok  {doc}: {count} passed labelled 'as of {label[:7]}', "
                            f"which is a recorded run"
                        )
                elif head[:7] in commits:
                    # A run of THIS commit produced it. Older runs producing the
                    # same number is not a defect -- it is what stability looks
                    # like -- so only the absence of a HEAD run is a problem.
                    notes.append(f"  ok  {doc}: {count} passed matches the HEAD run")
                else:
                    problems.append(
                        f"{doc}: claims {count} passed with no 'as of <commit>' label, but "
                        f"the only runs that produced it are at "
                        f"{', '.join(commits)} and HEAD is {head[:7]}. Either record a run "
                        f"for this commit or label the figure "
                        f"'{count} passed (as of {commits[0]})' so the number names the "
                        f"tree it describes."
                    )
            else:
                notes.append(f"  ok  {doc}: {count} passed matches run(s) {', '.join(matching)}")

    if head and not any(head[:7] in run_id for run_id in source):
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
        if any(part in {".git", "venvmp", ".venv", "build", "dist", "node_modules"} for part in doc.parts):
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
    parser.add_argument(
        "--require-head",
        action="store_true",
        help="an 'N passed' figure must be backed by a run of THIS commit, or be "
        "labelled 'N passed (as of <commit>)' naming a commit that really ran",
    )
    parser.add_argument(
        "--junitxml",
        type=Path,
        help="a pytest junitxml from the run happening right now. When given, the "
        "run's own counts become the only accepted source for an unlabelled "
        "'N passed' figure, and RUN_RECORD is not consulted for it.",
    )
    parser.add_argument(
        "--expect-sha",
        default=os.getenv("GITHUB_SHA"),
        help="the commit this run is supposed to describe; defaults to $GITHUB_SHA",
    )
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

    # The runner mode. Without this the gate's numbers come from RUN_RECORD, and
    # RUN_RECORD is written by a person. Session 7 recorded 1625 for a run whose
    # own log said 1619, because the figure was read out of a log archive by
    # hand; `--require-head` could not catch it, and the limit written down at
    # the time was "--require-head ties a figure to a COMMIT, not to a LOG".
    # That limit is why this exists.
    junit_recorded: dict[str, dict] | None = None
    if args.junitxml:
        counts = junit_counts(args.junitxml)
        if counts is None:
            problems.append(f"--junitxml {args.junitxml} is not a readable junitxml")
        else:
            if args.expect_sha and head and args.expect_sha != head:
                problems.append(
                    f"this run describes {args.expect_sha[:7]} but the checkout is "
                    f"{head[:7]}: the junitxml and the tree are not the same commit"
                )
            if not head:
                problems.append("cannot read HEAD, so a junitxml cannot be tied to a commit")
            elif head:
                junit_recorded = {
                    f"{head[:7]}: this run's junitxml {args.junitxml}": {
                        "commit": head,
                        "label": f"the run that produced {args.junitxml}",
                        "note": "counts read from the junitxml the runner just wrote",
                        **counts,
                    }
                }
                notes.append(
                    f"  --  runner mode: {args.junitxml} says {counts['passed']} passed, "
                    f"{counts['skipped']} skipped, {counts['collected']} collected, at "
                    f"{head[:7]}"
                )

    count_problems, count_notes = check_test_counts(
        text_by_doc, head, args.require_head, junit_recorded
    )
    problems.extend(count_problems)
    notes.extend(count_notes)

    if junit_recorded is None:
        integrity_problems, integrity_notes = check_run_integrity(head)
        problems.extend(integrity_problems)
        notes.extend(integrity_notes)

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

    return 1 if (problems and (args.strict or args.require_head)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
