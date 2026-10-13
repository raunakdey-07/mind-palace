"""The evidence gate must reject a figure that only an older run produced.

`check_evidence.py` has asserted for a while that every "N passed" in STATUS.md
matches *some* recorded run. That is weaker than it looks, and this file is the
regression test for why.

A run record is keyed by commit, so a figure copied forward from last week's
tree keeps matching, and keeps matching forever, because the old record never
expires. STATUS.md carried `1619 passed`, which some older commit genuinely
produced, while CI on the current tree reported a different split. Nothing
complained, because the question being asked was "did any run ever produce
1619", and the answer stayed yes after the tree moved.

So the figure is tied to a commit now, in exactly two honest forms:

    1619 passed                 -> must be a run at HEAD
    1619 passed (as of 3108404) -> must be a run at 3108404

The second form is not a loophole. It is the claim made explicit: this number
describes that tree, and here is which one.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module():
    """Load `scripts/check_evidence.py` the way CI does, not by importing `scripts`."""
    spec = importlib.util.spec_from_file_location(
        "check_evidence", ROOT / "scripts" / "check_evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HEAD = "312958d9" + "0" * 32
OLD = "3108404" + "0" * 32


@pytest.fixture
def recorded(monkeypatch):
    """Two recorded runs: one at HEAD, one at an older commit. 1619 at both."""
    module = _module()
    monkeypatch.setattr(
        module,
        "_recorded_run_counts",
        lambda: {
            f"{HEAD[:7]}: full suite at head": {"passed": "1619", "commit": HEAD},
            f"{OLD[:7]}: full suite, older": {"passed": "1619", "commit": OLD},
        },
    )
    monkeypatch.setattr(module, "collected_test_count", lambda: 1621)
    return module


def test_a_figure_matching_only_an_older_run_is_rejected(recorded):
    """The whole point: 1619 exists at OLD, and HEAD never produced it."""
    # Drop the HEAD run from this case only: the claim under test is that a
    # figure with NO run of this commit behind it is refused.
    recorded._recorded_run_counts = lambda: {
        f"{OLD[:7]}: full suite, older": {"passed": "1619", "commit": OLD}
    }
    problems, _ = recorded.check_test_counts(
        {"docs/STATUS.md": "tests: 1619 passed, 0 failed, 2 skipped"}, HEAD[:7], True
    )
    assert problems, "a figure backed only by an older run must be rejected"
    assert any("as of <commit>" in p for p in problems), problems
    assert any(HEAD[:7] in p for p in problems), "the message must name HEAD"


def test_a_figure_matching_the_head_run_passes(recorded):
    problems, notes = recorded.check_test_counts(
        {"docs/STATUS.md": "tests: 1619 passed, 2 skipped"}, HEAD[:7], True
    )
    assert not problems, problems
    assert any("HEAD run" in n for n in notes), notes


def test_an_as_of_label_is_accepted_when_that_commit_ran(recorded):
    """The escape hatch has to be a real one, or nobody will ever use it."""
    problems, notes = recorded.check_test_counts(
        {"docs/STATUS.md": f"tests: 1619 passed (as of {OLD[:7]})"}, HEAD[:7], True
    )
    assert not problems, problems
    assert any("as of" in n for n in notes), notes


def test_an_as_of_label_naming_a_commit_that_never_ran_is_rejected(recorded):
    """Otherwise the label is just a way to make the check quiet."""
    problems, _ = recorded.check_test_counts(
        {"docs/STATUS.md": "tests: 1619 passed (as of deadbee)"}, HEAD[:7], True
    )
    assert problems, "a label naming a commit with no recorded run must be rejected"
    assert any("deadbee" in p for p in problems), problems


def test_without_the_flag_the_old_behaviour_is_unchanged(recorded):
    """`--strict` on its own must not start failing documents that always passed.

    The new behaviour is opt-in via --require-head, so turning it on is a
    decision rather than a surprise that breaks a build nobody was watching.
    """
    problems, notes = recorded.check_test_counts(
        {"docs/STATUS.md": "tests: 1619 passed, 2 skipped"}, HEAD[:7], False
    )
    assert not problems, problems
    assert any("matches run" in n for n in notes), notes


def test_the_record_on_disk_names_a_commit_for_every_run():
    """The gate is only as good as the record, so check the record's shape.

    A run key is either the commit prefix (a local run, which
    `record_test_run.py` writes) or a GitHub Actions run id (a runner run,
    which is the only thing that can attest to the CI figure). Both must still
    carry a full 40-character commit, because that is what `--require-head`
    compares against and what makes a record falsifiable.
    """
    path = ROOT / "docs" / "performance" / "test-runs.json"
    data = json.loads(path.read_text())
    runs = data["runs"]
    assert runs, "no run has ever been recorded"
    for run_id, entry in runs.items():
        assert len(entry["commit"]) == 40, f"{run_id}: commit is not a full sha"
        assert entry["commit"].isascii(), run_id
        assert entry["passed"].isdigit(), f"{run_id}: passed is not a number"
        assert entry["skipped"].isdigit(), f"{run_id}: skipped is not a number"
        is_local = run_id.startswith(entry["commit"][:7])
        is_ci = run_id.split(":", 1)[0].isdigit()
        assert is_local or is_ci, (
            f"{run_id}: neither a <commit-prefix>: label nor a <run-id>: label, "
            f"so the gate cannot tell which commit it describes"
        )
