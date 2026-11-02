from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_per_seed_limit_and_dev_set_exclusion(tmp_path, monkeypatch):
    checker = load("checker", ROOT / "scripts/check_sealed_runs.py")
    log = tmp_path / "sealed_runs.log"
    log.write_text(
        "2026-01-01T00:00:00Z 1 sha FINAL A\n"
        "2026-01-01T00:00:01Z 1 sha FINAL A\n"
        "2026-01-01T00:00:02Z 1 sha DEV_SET A\n"
        "2026-01-01T00:00:03Z 2 sha FINAL A\n"
        "2026-01-01T00:00:04Z 2 sha FINAL A\n"
    )
    monkeypatch.setattr(checker, "LOG", log)
    monkeypatch.setattr(checker, "git", lambda *args: "")
    assert checker.main() == 0

    log.write_text("2026-01-01T00:00:00Z sha FINAL A\n")
    assert checker.main() == 1


def test_three_final_runs_for_one_seed_fail(tmp_path, monkeypatch):
    checker = load("checker", ROOT / "scripts/check_sealed_runs.py")
    log = tmp_path / "sealed_runs.log"
    log.write_text("".join(f"2026-01-01T00:00:0{i}Z 1 sha FINAL A\n" for i in range(3)))
    monkeypatch.setattr(checker, "LOG", log)
    monkeypatch.setattr(checker, "git", lambda *args: "")
    assert checker.main() == 1


def test_final_log_line_is_shaped(tmp_path):
    evaluate = load(
        "evaluate",
        ROOT / "benchmarks/agency_readiness/evaluate.py",
    )
    log = tmp_path / "sealed_runs.log"
    evaluate.append_final_run(log, 7, "abc123", evaluate.datetime(2026, 1, 1, tzinfo=evaluate.timezone.utc))
    assert log.read_text().split() == [
        "2026-01-01T00:00:00+00:00",
        "7",
        "abc123",
        "FINAL",
        "A",
        "B",
        "B_plus",
        "B_star",
        "C",
        "C_plus",
        "D",
    ]
