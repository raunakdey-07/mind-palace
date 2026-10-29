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
    "test_latency_and_explain_initplan": (
        "migration 015's per-row trap",
        "known product bug: mindpalace_allowed_corpus calls "
        "mp_verified_scope_corpora() once per row instead of hoisting it to "
        "an InitPlan; fix requires a new migration; remove when fixed",
    ),
}

#: Skips that are correct only where `sentence-transformers` is absent, which is
#: the job whose dependency set is the project's default.
#:
#: `sentence-transformers` is the `[torch]` extra. The main CI job installs the
#: default set, so it has no torch and the torch-only tests skip there. That is the
#: intended consequence of the default, not a defect, and this list records which
#: tests it covers -- keyed on name and reason, like ALLOWLIST above.
#:
#: It is scoped to one job on purpose. `embedding-backends` installs `[torch]`
#: precisely so those tests run, so a skip there means the extra stopped being
#: installed or the test stopped running, and the job must fail. That job passes
#: `--forbid-skips`, which tolerates none.
BACKEND_ABSENT: dict[str, str] = {
    "test_each_text_is_the_same_vector_under_both_backends": "embedding-backends CI job",
    "test_both_backends_produce_the_same_number_of_vectors": "embedding-backends CI job",
    "test_both_backends_produce_the_same_dimension": "embedding-backends CI job",
    "test_both_vectors_are_unit_length": "embedding-backends CI job",
    "test_the_batch_is_not_silently_reordered": "embedding-backends CI job",
    "test_cached_embedder_requests_local_files_only": "[torch] extra is not installed",
    "test_rerank_scores_are_logits_can_be_negative": "[torch] extra",
}


def base_name(name: str) -> str:
    """The test's own name, with any parametrization suffix removed.

    junit records `test_foo[0]` .. `test_foo[11]` as twelve separate entries. An
    allowlist keyed on those exact strings would need sixteen lines for one test
    and would break the moment a parameter was added, so the suffix comes off
    first and the lookup is on the function.
    """
    return name.split("[", 1)[0]


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
    parser.add_argument(
        "--default-dependency",
        action="store_true",
        help="also allow the skips that are correct only where the [torch] extra "
        "is absent, which is the job that installs the default dependency set",
    )
    parser.add_argument(
        "--forbid-skips",
        action="store_true",
        help="fail on any skip at all, for a job that is supposed to run everything",
    )
    args = parser.parse_args()

    if args.default_dependency and args.forbid_skips:
        print("::error::--default-dependency and --forbid-skips contradict each other")
        return 2

    if not args.junit.exists():
        print(f"::error::no junitxml at {args.junit}")
        return 1

    cases = skipped_cases(args.junit)
    unexpected: list[tuple[str, str, str]] = []
    allowed: list[str] = []

    for classname, name, reason in cases:
        if args.forbid_skips:
            unexpected.append((classname, name, reason))
            continue
        key = base_name(name)
        entry = ALLOWLIST.get(key)
        if entry is not None:
            if entry[0] not in reason:
                # The opt-in variable has to still be the stated reason. A test can
                # keep its name and start skipping for something else entirely.
                unexpected.append((classname, name, reason))
            else:
                allowed.append(f"{name} (opt-in via {entry[0]})")
            continue
        if args.default_dependency and key in BACKEND_ABSENT:
            fragment = BACKEND_ABSENT[key]
            if fragment in reason:
                allowed.append(f"{name} (default dependency set: {fragment})")
            else:
                # Same name, different reason: the extra went away for some other
                # reason, and that is the thing worth stopping for.
                unexpected.append((classname, name, reason))
            continue
        unexpected.append((classname, name, reason))

    print(f"skipped tests read from {args.junit}: {len(cases)}")
    scope = "default dependency set" if args.default_dependency else "base allowlist"
    if args.forbid_skips:
        scope = "no skips permitted"
    print(f"scope: {scope}")
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


def _self_test() -> int:
    """The gate, checked against the gate.

    `--forbid-skips` exists to make the embedding-backends job fail if torch stops
    being installed there. That only works if it is true, and the only honest way
    to know is to run it over a junit that records a skip and watch it fail. Run
    without arguments:

        python scripts/check_skips.py --self-test
    """
    import io
    import tempfile

    def check(label: str, xml: str, argv: list[str], want: int) -> list[str]:
        path = Path(tempfile.mkdtemp()) / "junit.xml"
        path.write_text(xml, encoding="utf-8")
        saved, sys.argv = sys.argv, ["check_skips", "--junit", str(path), *argv]
        buf, real = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            got = main()
        finally:
            sys.stdout = real
            sys.argv = saved
        ok = got == want
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: exit {got}, expected {want}")
        if not ok:
            print(buf.getvalue())
        return [line for line in buf.getvalue().splitlines() if line.strip()]

    def junit(*cases: tuple[str, str]) -> str:
        body = "".join(
            f'<testcase classname="t" name="{n}"><skipped message="{r}"/></testcase>'
            for n, r in cases
        )
        return f'<?xml version="1.0"?><testsuites><testsuite>{body}</testsuite></testsuites>'

    torch_absent = junit(
        (
            "test_both_vectors_are_unit_length",
            "comparing the two backends needs both; the embedding-backends CI job has them",
        ),
        (
            "test_cached_embedder_requests_local_files_only",
            "the [torch] extra is not installed; the ONNX backend has its own tests",
        ),
        (
            "test_rerank_scores_are_logits_can_be_negative",
            "reranking needs the [torch] extra; the default install does not have it",
        ),
    )
    opt_in = junit(
        ("test_migrations_apply_downgrade_and_reapply", "needs MIND_PALACE_MIGRATION_ROUNDTRIP"),
    )

    print("check_skips self-test")
    problems = 0
    # 1. The torch-dependent skips are fine in the default-dependency job.
    problems += (
        0
        if check(
            "torch skips allowed where the default is installed",
            torch_absent,
            ["--default-dependency"],
            0,
        )
        else 1
    )
    # 2. The mutation: the same skips must FAIL the job that has torch, which is
    #    what makes this gate able to notice the extra stopping being installed.
    problems += (
        0
        if check(
            "torch skips refused where [torch] is installed", torch_absent, ["--forbid-skips"], 1
        )
        else 1
    )
    problems += (
        0 if check("torch skips refused under the base allowlist", torch_absent, [], 1) else 1
    )
    # 3. Zero skips must pass under --forbid-skips: the job is not merely
    #    configured to tolerate nothing, it has to be able to prove it.
    problems += 0 if check("no skips passes --forbid-skips", junit(), ["--forbid-skips"], 0) else 1
    # 4. The pre-existing opt-in behaviour is untouched.
    problems += 0 if check("opt-in skip still allowed", opt_in, [], 0) else 1
    # 5. A test keeping its name but changing its reason is still a problem.
    problems += (
        0
        if check(
            "wrong reason for a known name is refused",
            junit(("test_migrations_apply_downgrade_and_reapply", "no database")),
            [],
            1,
        )
        else 1
    )
    # 6. The two flags together are contradictory rather than silently lenient.
    problems += (
        0
        if check(
            "contradictory flags rejected",
            torch_absent,
            ["--default-dependency", "--forbid-skips"],
            2,
        )
        else 1
    )

    print(f"\n{6 - problems}/{6} self-tests passed")
    return 1 if problems else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.argv.remove("--self-test")
        sys.exit(_self_test())
    sys.exit(main())
