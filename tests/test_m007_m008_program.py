import json
import subprocess
import sys
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


def test_required_input_is_machine_readable():
    row = json.loads((ROOT / "eval/m007_m008/REQUIRED_INPUT.jsonl").read_text())
    assert row["dependency"] == "independent_adjudication"
    assert "review_id" in row["required_fields"]
