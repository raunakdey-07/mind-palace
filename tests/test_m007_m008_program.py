import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "eval/m007_m008/run_program.py"


def test_program_runner_reports_data_blocked_without_fabrication(tmp_path):
    result = subprocess.run(
        [sys.executable, str(RUNNER), "audit"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["data"]["status"] == "DATA_BLOCKED"
    assert payload["data"]["adjudicated_rows"] == 0
    assert payload["release_decision"] == "PROGRAM_READY_FOR_ADJUDICATION"
    assert all(stage["empirical"] != "PASSED" for stage in payload["stages"])


def test_validate_reviewer_path_does_not_require_embedding_runtime():
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "validate-reviewer",
            str(ROOT / "eval/m007/adjudication/adjudication_template.jsonl"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert "missing review IDs: 118" in payload["errors"]


def test_validate_reviewer_rejects_forbidden_metadata():
    review = json.loads(
        (ROOT / "eval/m007/adjudication/review_set.jsonl").read_text().splitlines()[0]
    )
    row = {
        "review_id": review["review_id"],
        "reviewer_id": "reviewer-A",
        "reviewed_at": "2026-09-24T00:00:00Z",
        "relevance": "uncertain",
        "subject_compatibility": "uncertain",
        "temporal_applicability": "uncertain",
        "evidence_sufficiency": "uncertain",
        "authority_compatibility": "uncertain",
        "conflict_status": "unknown",
        "overall_decision": "abstain",
        "rationale": "Structural test only.",
        "source_reference": "reviewer_context.jsonl",
        "resolver_output": "forbidden",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        handle.write(json.dumps(row) + "\n")
        path = Path(handle.name)
    try:
        result = subprocess.run(
            [sys.executable, str(RUNNER), "validate-reviewer", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        assert "record 1: prohibited metadata" in payload["errors"]
    finally:
        path.unlink()


def test_required_input_is_machine_readable():
    row = json.loads((ROOT / "eval/m007_m008/REQUIRED_INPUT.jsonl").read_text())
    assert row["dependency"] == "independent_adjudication"
    assert "review_id" in row["required_fields"]
