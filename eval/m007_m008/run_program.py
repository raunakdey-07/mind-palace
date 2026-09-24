#!/usr/bin/env python
"""Executable M007–M008 research program.

The runner distinguishes implementation readiness from empirical readiness.
It never converts structural candidates or M006.75 predictions into labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REVIEW = ROOT / "eval/m007/adjudication/review_set.jsonl"
MANIFEST = ROOT / "eval/m007/research-manifest.json"
ADJUDICATED = ROOT / "eval/m007/adjudication/adjudicated.jsonl"


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def adjudication_state() -> dict:
    reviews = load_jsonl(REVIEW)
    adjudicated = load_jsonl(ADJUDICATED)
    completed = len(adjudicated)
    total = len(reviews)
    if total == 0:
        status = "NOT_STARTED"
    elif completed == 0:
        status = "DATA_BLOCKED"
    elif completed < total:
        status = "PARTIAL_DATA"
    else:
        status = "DATA_READY"
    return {
        "status": status,
        "review_items": total,
        "adjudicated_rows": completed,
        "unreviewed_items": max(total - completed, 0),
        "reviewers": sorted(
            {row.get("reviewer_id") for row in adjudicated if row.get("reviewer_id")}
        ),
        "unknown_rows": sum(
            row.get("overall_decision") in {"unknown", "uncertain"} for row in adjudicated
        ),
        "abstentions": sum(row.get("overall_decision") == "abstain" for row in adjudicated),
        "review_set_sha256": digest(REVIEW),
        "adjudicated_sha256": digest(ADJUDICATED),
        "forbidden_fields_present": sorted(
            {
                field
                for row in reviews
                for field in (
                    "resolver_decision",
                    "candidate_rank",
                    "similarity",
                    "embedding",
                    "provider_output",
                    "m00675_prediction",
                )
                if field in row
            }
        ),
    }


def validate_reviewer_file(path: Path) -> dict:
    expected = {row["review_id"] for row in load_jsonl(REVIEW)}
    rows = load_jsonl(path)
    required = {
        "review_id",
        "reviewer_id",
        "reviewed_at",
        "relevance",
        "subject_compatibility",
        "temporal_applicability",
        "evidence_sufficiency",
        "authority_compatibility",
        "conflict_status",
        "overall_decision",
        "rationale",
        "source_reference",
    }
    allowed = {
        "relevance": {"relevant", "irrelevant", "uncertain"},
        "subject_compatibility": {"compatible", "incompatible", "uncertain"},
        "temporal_applicability": {"applicable", "not_applicable", "uncertain", "not_temporal"},
        "evidence_sufficiency": {"sufficient", "insufficient", "uncertain", "conflicting"},
        "authority_compatibility": {
            "current",
            "historical",
            "superseded",
            "conflicted",
            "deleted",
            "restored",
            "uncertain",
            "not_applicable",
        },
        "conflict_status": {
            "none",
            "open",
            "acknowledged",
            "resolved",
            "reopened",
            "superseded",
            "rejected",
            "unknown",
            "not_applicable",
        },
        "overall_decision": {"accept", "reject", "abstain"},
    }
    errors = []
    ids = [row.get("review_id") for row in rows]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        errors.append(f"duplicate review IDs: {duplicates}")
    missing = sorted(expected - set(ids))
    unknown = sorted(set(ids) - expected)
    if missing:
        errors.append(f"missing review IDs: {len(missing)}")
    if unknown:
        errors.append(f"unknown review IDs: {unknown}")
    for index, row in enumerate(rows, 1):
        if not required.issubset(row):
            errors.append(f"record {index}: missing required fields")
        for field, values in allowed.items():
            if field in row and row[field] not in values:
                errors.append(f"record {index}: invalid {field}")
        forbidden = {
            "m00675",
            "resolver",
            "prediction",
            "ranking",
            "similarity",
            "embedding",
            "gold",
            "expected_answer",
        }
        if any(token in field.lower() for field in row for token in forbidden):
            errors.append(f"record {index}: prohibited metadata")
        if (
            not str(row.get("rationale", "")).strip()
            or not str(row.get("source_reference", "")).strip()
        ):
            errors.append(f"record {index}: rationale/source reference required")
    return {
        "path": str(path),
        "valid": not errors,
        "records": len(rows),
        "expected": len(expected),
        "errors": errors,
    }


def stage_states(data: dict) -> list[dict]:
    ready = data["status"] in {"PARTIAL_DATA", "DATA_READY"}
    return [
        {"id": "M007.1", "implementation": "READY", "empirical": data["status"]},
        {
            "id": "M007.2",
            "implementation": "READY",
            "empirical": "BLOCKED" if not ready else "READY",
        },
        {
            "id": "M007.3",
            "implementation": "READY",
            "empirical": "BLOCKED" if not ready else "READY",
        },
        {"id": "M007.4", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M007.5", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M007.6", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M007.7", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M007.8", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.1", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.2", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.3", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.4", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.5", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.6", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.7", "implementation": "READY", "empirical": "BLOCKED"},
        {"id": "M008.8", "implementation": "READY", "empirical": "BLOCKED"},
    ]


def write_report(command: str, data: dict, semantic: dict | None = None) -> None:
    payload = {
        "command": command,
        "program": "m007-m008",
        "data": data,
        "stages": stage_states(data),
        "synthetic_semantic_validation": semantic,
        "release_decision": (
            "PROGRAM_READY_FOR_ADJUDICATION"
            if data["status"] == "DATA_BLOCKED"
            else "EVALUATION_REQUIRED"
        ),
        "note": "No labels or empirical results are fabricated when adjudication is incomplete.",
    }
    target = ROOT / "eval/m007_m008/latest-program-report.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "audit",
            "adjudication",
            "validate-reviewer",
            "synthetic",
            "temporal",
            "longitudinal",
            "conflict",
            "security",
            "baseline",
            "m007",
            "m008",
            "compare",
            "full",
            "report",
        ),
    )
    parser.add_argument("reviewer_file", nargs="?")
    args = parser.parse_args()
    if args.command == "validate-reviewer":
        if not args.reviewer_file:
            parser.error("validate-reviewer requires a JSONL path")
        print(
            json.dumps(validate_reviewer_file(Path(args.reviewer_file)), indent=2, sort_keys=True)
        )
        return
    data = adjudication_state()
    semantic = None
    if args.command in {
        "synthetic",
        "temporal",
        "longitudinal",
        "conflict",
        "security",
        "full",
        "report",
    }:
        from api.services.memory_evaluation_lab import run_all  # noqa: PLC0415

        semantic = run_all()
    write_report(args.command, data, semantic)


if __name__ == "__main__":
    main()
