import json

from scripts.benchmark.m00675 import build_artifact, canonical, fixture_hash, sha256

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_frozen_inputs_and_canonical_result_are_stable():
    result = json.loads((ROOT / "eval/results/m00675-heldout.json").read_text())
    artifact = build_artifact(result)
    assert artifact["dataset_sha256"] == sha256(ROOT / "eval/memory_questions_heldout.yaml")
    assert artifact["policy_sha256"] == sha256(ROOT / "eval/memory_query_policy.json")
    assert artifact["policy"] == "frozen"
    assert artifact["database_fixture_sha256"] == fixture_hash()
    assert len(artifact["questions"]) == 60
    assert artifact["summary"]["total"]["exact"] == 47
    assert artifact["categories"]["abstention"]["exact"] == 10


def test_canonicalization_excludes_timing_and_timestamps():
    value = {"timestamp": 1, "timings": {"elapsed": 2}, "nested": [{"x": 3}]}
    assert canonical(value) == {"nested": [{"x": 3}]}
