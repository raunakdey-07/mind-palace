"""The runner is the only witness whose numbers nobody can write by hand.

Two gates are here, and both exist because of one specific failure.

Six tests in `tests/test_ingestion_statistics.py` skipped on every CI runner
and passed on every workstation, for four sessions. Their skip reason was
`no reachable DATABASE_URL`, and it was false: the database was up and
reachable. `SQLAlchemy 2.1` resolved bare `postgresql://` to psycopg (v3); this
project installs `psycopg2-binary`; and a bare `except Exception: return False`
in `_db_available()` turned the resulting `ModuleNotFoundError` into "no
database". Reproduced 2026-10-13 against one reachable database, changing only
the version:

    2.0.46 -> psycopg2 -> 6 passed
    2.1.4  -> psycopg  -> 6 skipped

Then a second failure, on the evidence side. Session 7 recorded `1625 passed`
for a runner whose own log said `1619`. The number had been read out of a log
archive by hand, from the wrong step, and `check_evidence --require-head` could
not catch it -- it ties a figure to a **commit**, and the record had been
hand-written at the right commit. The limit was known and written down at the
time. A known limit with nothing built against it is the same as no limit.

So:

* `check_skips.py` fails the build when a skipped test is not on an allowlist
  whose every entry names its reason.
* `check_evidence.py --junitxml` takes the counts from the junitxml the run
  itself produced, so an unlabelled figure has to equal a run that happened.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_evidence():
    return _load("check_evidence", "scripts/check_evidence.py")


def _check_skips():
    return _load("check_skips", "scripts/check_skips.py")


HEAD = "312958d9" + "0" * 32
OLD = "3108404" + "0" * 32


def _junit(tmp_path: Path, cases: list[tuple[str, str, str, str]]) -> Path:
    """Write a junitxml shaped like pytest's.

    Each case is ``(classname, name, outcome, reason)``; ``outcome`` is one of
    ``passed``, ``skipped``, ``failure``.
    """
    totals = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
    for _, _, outcome, _ in cases:
        if outcome == "skipped":
            totals["skipped"] += 1
        elif outcome == "failure":
            totals["failures"] += 1
    attrs = " ".join(f'{k}="{v}"' for k, v in totals.items())
    body = []
    for classname, name, outcome, reason in cases:
        child = {
            "skipped": f'<skipped message="{reason}" type="pytest.skip"/>',
            "failure": '<failure message="boom">boom</failure>',
            "passed": "",
        }[outcome]
        body.append(f'<testcase classname="{classname}" name="{name}">{child}</testcase>')
    path = tmp_path / "junit.xml"
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite {attrs}>{"".join(body)}</testsuite></testsuites>'
    )
    return path


def _run(monkeypatch, main, *argv: str) -> None:
    """`main()` reads sys.argv. Patching it beats reaching into the module."""
    monkeypatch.setattr(sys, "argv", ["prog", *argv])


# ---------------------------------------------------------------- skips gate


def test_the_two_opt_in_skips_are_the_whole_allowlist():
    """If this ever grows, a person has to write down why, here."""
    module = _check_skips()
    assert set(module.ALLOWLIST) == {
        "test_migrations_apply_downgrade_and_reapply",
        "test_a_built_wheel_contains_every_declared_module",
    }
    for name, (variable, why) in module.ALLOWLIST.items():
        assert variable.startswith("MIND_PALACE_"), name
        assert len(why) > 40, f"{name} needs a reason, not a placeholder"


def test_an_unlisted_skip_is_rejected(tmp_path, monkeypatch, capsys):
    module = _check_skips()
    junit = _junit(
        tmp_path,
        [("tests.test_x", "test_something_new", "skipped", "no reachable DATABASE_URL")],
    )
    _run(monkeypatch, module.main, "--junit", str(junit))
    assert module.main() == 1
    assert "test_something_new" in capsys.readouterr().out


def test_an_allowlisted_skip_passes(tmp_path, monkeypatch):
    module = _check_skips()
    junit = _junit(
        tmp_path,
        [
            (
                "tests.test_wheel_contents",
                "test_a_built_wheel_contains_every_declared_module",
                "skipped",
                "set MIND_PALACE_TEST_BUILD_WHEEL=1 to build and inspect a wheel",
            )
        ],
    )
    _run(monkeypatch, module.main, "--junit", str(junit))
    assert module.main() == 0


def test_an_allowlisted_name_that_skips_for_another_reason_is_rejected(tmp_path, monkeypatch):
    """The allowlist is keyed on the reason as well as the name.

    A test can keep its name and start skipping because the database went away.
    That is a different decision and must not pass on the old entry.
    """
    module = _check_skips()
    junit = _junit(
        tmp_path,
        [
            (
                "tests.test_wheel_contents",
                "test_a_built_wheel_contains_every_declared_module",
                "skipped",
                "no reachable DATABASE_URL",
            )
        ],
    )
    _run(monkeypatch, module.main, "--junit", str(junit))
    assert module.main() == 1


# ------------------------------------------------- skips that depend on the backend


#: The skips the default dependency set causes, with the reasons the tests carry.
#: Named here as well as in the gate so that a rename in either place is a failure
#: rather than a silently widened allowlist.
BACKEND_ABSENT_CASES = [
    (
        "tests.test_embedder_backend_equivalence",
        "test_each_text_is_the_same_vector_under_both_backends",
        "comparing the two backends needs both; the embedding-backends CI job has them",
    ),
    (
        "tests.test_embedder_backend_equivalence",
        "test_both_backends_produce_the_same_number_of_vectors",
        "comparing the two backends needs both; the embedding-backends CI job has them",
    ),
    (
        "tests.test_embedder_backend_equivalence",
        "test_both_backends_produce_the_same_dimension",
        "comparing the two backends needs both; the embedding-backends CI job has them",
    ),
    (
        "tests.test_embedder_backend_equivalence",
        "test_both_vectors_are_unit_length",
        "comparing the two backends needs both; the embedding-backends CI job has them",
    ),
    (
        "tests.test_embedder_backend_equivalence",
        "test_the_batch_is_not_silently_reordered",
        "comparing the two backends needs both; the embedding-backends CI job has them",
    ),
    (
        "tests.test_memory_benchmark",
        "test_cached_embedder_requests_local_files_only",
        "the [torch] extra is not installed; the ONNX backend has its own tests",
    ),
    (
        "tests.test_search_semantics",
        "test_rerank_scores_are_logits_can_be_negative",
        "reranking needs the [torch] extra; the default install does not have it",
    ),
]


def test_backend_absent_is_exactly_the_seven_skips_the_default_causes():
    """If this ever grows, somebody has to write down why, here."""
    module = _check_skips()
    assert set(module.BACKEND_ABSENT) == {name for _, name, _ in BACKEND_ABSENT_CASES}
    for _classname, name, reason in BACKEND_ABSENT_CASES:
        fragment = module.BACKEND_ABSENT[name]
        assert fragment in reason, (
            f"{name} is allowlisted on the fragment {fragment!r}, which its own "
            f"reason {reason!r} does not contain -- the gate would reject it"
        )


def test_the_default_dependency_job_allows_the_torch_skips(tmp_path, monkeypatch):
    module = _check_skips()
    junit = _junit(tmp_path, [(c, n, "skipped", r) for c, n, r in BACKEND_ABSENT_CASES])
    _run(monkeypatch, module.main, "--junit", str(junit), "--default-dependency")
    assert module.main() == 0


def test_the_same_skips_are_refused_without_the_flag(tmp_path, monkeypatch, capsys):
    """The scoping is the whole point: these are only correct in one job.

    Under the base allowlist they are refused, which is what the
    `embedding-backends` job would see if it did not pass `--forbid-skips`.
    """
    module = _check_skips()
    junit = _junit(tmp_path, [(c, n, "skipped", r) for c, n, r in BACKEND_ABSENT_CASES])
    _run(monkeypatch, module.main, "--junit", str(junit))
    assert module.main() == 1
    assert "test_both_vectors_are_unit_length" in capsys.readouterr().out


def test_forbid_skips_fails_on_any_skip(tmp_path, monkeypatch, capsys):
    """The mutation the embedding-backends job relies on.

    If `[torch]` stopped being installed there, `importorskip` would turn the five
    backend-equivalence tests into five skips, the job would still be green, and
    the one property the archive depends on -- that the two backends are
    interchangeable -- would stop being verified anywhere. This is what catches it.
    """
    module = _check_skips()
    junit = _junit(
        tmp_path,
        [
            (
                "tests.test_embedder_backend_equivalence",
                "test_both_vectors_are_unit_length",
                "skipped",
                "needs torch",
            )
        ],
    )
    _run(monkeypatch, module.main, "--junit", str(junit), "--forbid-skips")
    assert module.main() == 1
    assert "test_both_vectors_are_unit_length" in capsys.readouterr().out


def test_forbid_skips_passes_when_nothing_skipped(tmp_path, monkeypatch):
    """Otherwise `--forbid-skips` would be untestable: a gate that always fails
    would satisfy the mutation above without checking anything."""
    module = _check_skips()
    junit = _junit(tmp_path, [("tests.test_embedder", "test_backend_is_preferred", "passed", "")])
    _run(monkeypatch, module.main, "--junit", str(junit), "--forbid-skips")
    assert module.main() == 0


def test_forbid_skips_also_refuses_the_two_opt_in_skips(tmp_path, monkeypatch):
    """Zero means zero, including the ones that are otherwise allowlisted."""
    module = _check_skips()
    junit = _junit(
        tmp_path,
        [
            (
                "tests.test_migrations",
                "test_migrations_apply_downgrade_and_reapply",
                "skipped",
                "set MIND_PALACE_MIGRATION_ROUNDTRIP=1",
            )
        ],
    )
    _run(monkeypatch, module.main, "--junit", str(junit), "--forbid-skips")
    assert module.main() == 1


def test_a_backend_absent_name_skipping_for_another_reason_is_refused(tmp_path, monkeypatch):
    """Same rule as the base allowlist, and it matters more here.

    These entries exist because the dependency is absent. A test that keeps its
    name and starts skipping because the database went away is a different
    failure and must not pass on the old entry.
    """
    module = _check_skips()
    junit = _junit(
        tmp_path,
        [
            (
                "tests.test_search_semantics",
                "test_rerank_scores_are_logits_can_be_negative",
                "skipped",
                "no reachable DATABASE_URL",
            )
        ],
    )
    _run(monkeypatch, module.main, "--junit", str(junit), "--default-dependency")
    assert module.main() == 1


def test_the_two_flags_together_are_refused_rather_than_being_lenient(tmp_path, monkeypatch):
    """Allowing the torch skips and forbidding all skips cannot both be asked for."""
    module = _check_skips()
    junit = _junit(tmp_path, [(c, n, "skipped", r) for c, n, r in BACKEND_ABSENT_CASES])
    _run(
        monkeypatch,
        module.main,
        "--junit",
        str(junit),
        "--default-dependency",
        "--forbid-skips",
    )
    assert module.main() == 2


def test_the_gate_checks_itself():
    """`scripts/check_skips.py --self-test`, as a test.

    The self-test is how the flags are demonstrated without a CI run to point at,
    and CI does not run it on every push. This does.
    """
    import subprocess

    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_skips.py"), "--self-test"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "self-tests passed" in done.stdout, done.stdout


def test_the_six_ingestion_statistics_skips_are_caught(tmp_path, monkeypatch):
    """The actual regression, not a hypothetical one.

    The names are the six in `tests/test_ingestion_statistics.py`, and the
    reason is the false one they carried for four sessions.
    """
    module = _check_skips()
    names = [
        "test_bulk_sync_leaves_the_five_memory_tables_analysed",
        "test_load_query_makes_version_id_an_index_condition_not_a_filter",
        "test_load_reads_a_bounded_number_of_shared_buffers",
        "test_statistics_do_not_change_a_single_returned_row",
        "test_a_failing_analyze_does_not_fail_the_ingest",
        "test_a_sync_below_the_threshold_never_runs_analyze",
    ]
    junit = _junit(
        tmp_path,
        [
            ("tests.test_ingestion_statistics", n, "skipped", "no reachable DATABASE_URL")
            for n in names
        ],
    )
    _run(monkeypatch, module.main, "--junit", str(junit))
    assert module.main() == 1


def test_a_missing_junit_fails_rather_than_passing_quietly(tmp_path, monkeypatch):
    module = _check_skips()
    _run(monkeypatch, module.main, "--junit", str(tmp_path / "nope.xml"))
    assert module.main() == 1


# --------------------------------------------------------- runner-mode counts


def test_junit_counts_are_read_from_the_file(tmp_path):
    module = _check_evidence()
    junit = _junit(
        tmp_path,
        [
            ("t", "a", "passed", ""),
            ("t", "b", "passed", ""),
            ("t", "c", "passed", ""),
            ("t", "d", "passed", ""),
            ("t", "e", "skipped", "opt-in"),
            ("t", "f", "skipped", "opt-in"),
        ],
    )
    counts = module.junit_counts(junit)
    assert counts == {"passed": "4", "failed": "0", "skipped": "2", "collected": "6"}


def test_junit_counts_count_failures_out_of_passed(tmp_path):
    module = _check_evidence()
    junit = _junit(
        tmp_path,
        [("t", "a", "passed", ""), ("t", "b", "failure", ""), ("t", "c", "skipped", "x")],
    )
    assert module.junit_counts(junit)["passed"] == "1"


def test_junit_counts_reject_a_file_that_is_not_a_junit(tmp_path):
    module = _check_evidence()
    broken = tmp_path / "not.xml"
    broken.write_text("this is not xml at all <<<")
    assert module.junit_counts(broken) is None


def test_a_hand_edited_figure_is_rejected(tmp_path, monkeypatch):
    """(i) The figure was edited by hand to something this run did not produce.

    `1619` is real -- it is in `RUN_RECORD`, from a workstation at an older
    commit. The runner produced 1625. Given the junitxml the gate uses this run's
    numbers and nothing else, so an unlabelled `1619` has to be rejected, and
    naming the commit that produced it makes it pass again.
    """
    module = _check_evidence()
    monkeypatch.setattr(module, "collected_test_count", lambda: 1627)
    junit = _junit(tmp_path, [("t", f"t{i}", "passed", "") for i in range(1625)])
    assert module.junit_counts(junit)["passed"] == "1625"

    recorded = {
        f"{OLD[:7]}: hand-written record": {
            "commit": OLD,
            "passed": "1619",
            "failed": "0",
            "skipped": "8",
            "collected": "1627",
        }
    }

    problems, _ = module.check_test_counts(
        {"docs/STATUS.md": "The suite reported 1619 passed."}, HEAD, True, recorded
    )
    assert any("1619" in p for p in problems), problems

    labelled = {"docs/STATUS.md": "The suite reported 1619 passed (as of 3108404)."}
    problems, notes = module.check_test_counts(labelled, HEAD, True, recorded)
    assert not problems, problems
    assert notes


def test_a_figure_from_an_older_commit_is_rejected(tmp_path, monkeypatch):
    """(ii) The figure is true, and describes a tree that is not this one.

    The same 1619, produced at the older commit and carried forward with no
    label. This is the failure the `--require-head` flag was built for, and it is
    the one the junitxml makes checkable.
    """
    module = _check_evidence()
    monkeypatch.setattr(module, "collected_test_count", lambda: 1627)
    junit = _junit(tmp_path, [("t", f"t{i}", "passed", "") for i in range(1619)])
    assert module.junit_counts(junit)["passed"] == "1619"

    recorded = {
        f"{OLD[:7]}: the same number, older tree": {
            "commit": OLD,
            "passed": "1619",
            "failed": "0",
            "skipped": "2",
            "collected": "1627",
        }
    }
    problems, _ = module.check_test_counts(
        {"docs/STATUS.md": "The suite reported 1619 passed."}, HEAD, True, recorded
    )
    assert problems, "a figure with no 'as of' label must not ride an older run"
    assert any("1619" in p and "as of" in p for p in problems), problems


def test_the_run_it_just_happened_passes(monkeypatch):
    """The control. Without it the two rejections above prove nothing."""
    module = _check_evidence()
    monkeypatch.setattr(module, "collected_test_count", lambda: 1627)
    counts = {"passed": "1619", "failed": "0", "skipped": "8", "collected": "1627"}
    recorded = {f"{HEAD[:7]}: this run": {"commit": HEAD, **counts}}
    problems, notes = module.check_test_counts(
        {"docs/STATUS.md": "The suite reported 1619 passed."}, HEAD, True, recorded
    )
    assert not problems, problems
    assert any("matches the HEAD run" in n for n in notes), notes
