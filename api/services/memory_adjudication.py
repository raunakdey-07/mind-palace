"""Research-only validation and canonical hashing for M007.1 adjudications.

This module deliberately does not persist memory state, run the released
resolver, or convert review records into training data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class BlindReviewItem:
    review_id: str
    question_id: str
    question: str
    candidate_id: str
    candidate_claim: str
    candidate_path: str
    candidate_key: str
    evidence: tuple[str, ...]
    stage: str
    intent: str
    sampling_stratum: str
    information_need: str


Dimension = Literal[
    "relevant",
    "irrelevant",
    "uncertain",
    "compatible",
    "incompatible",
    "applicable",
    "not_applicable",
    "not_temporal",
    "sufficient",
    "insufficient",
    "conflicting",
    "current",
    "historical",
    "superseded",
    "deleted",
    "restored",
    "uncertain",
    "not_applicable",
    "accept",
    "reject",
    "abstain",
]


@dataclass(frozen=True)
class AdjudicationRecord:
    adjudication_id: str
    dataset_version: str
    question_id: str
    candidate_id: str
    reviewer_id: str
    reviewed_at: str
    relevance: str
    subject_compatibility: str
    temporal_applicability: str
    evidence_sufficiency: str
    authority_compatibility: str
    overall_decision: str
    rationale: str
    source_reference: str
    review_id: str | None = None
    conflict_status: str = "unknown"

    def validate(self) -> None:
        required = (
            "relevant",
            "irrelevant",
            "uncertain",
            "compatible",
            "incompatible",
            "applicable",
            "not_applicable",
            "not_temporal",
            "sufficient",
            "insufficient",
            "conflicting",
            "current",
            "historical",
            "superseded",
            "deleted",
            "restored",
            "accept",
            "reject",
            "abstain",
            "unknown",
            "open",
            "resolved",
            "reopened",
            "superseded",
        )
        for name in (
            "relevance",
            "subject_compatibility",
            "temporal_applicability",
            "evidence_sufficiency",
            "authority_compatibility",
            "conflict_status",
            "overall_decision",
        ):
            if getattr(self, name) not in required:
                raise ValueError(f"invalid {name}: {getattr(self, name)}")

        for name in (
            "adjudication_id",
            "dataset_version",
            "review_id",
            "question_id",
            "candidate_id",
            "reviewer_id",
            "rationale",
            "source_reference",
        ):
            if name == "review_id" and getattr(self, name) is None:
                continue
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")


def canonical_payload(record: AdjudicationRecord) -> dict:
    """Return a timestamp-independent representation for hashing."""
    payload = asdict(record)
    payload.pop("reviewed_at", None)
    return payload


def record_hash(record: AdjudicationRecord) -> str:
    encoded = json.dumps(canonical_payload(record), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def dataset_hash(records: list[AdjudicationRecord]) -> str:
    payload = [canonical_payload(record) for record in sorted(records, key=record_hash)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def generate_blind_review_set(examples, *, target_size: int = 120) -> list[BlindReviewItem]:
    """Build a deterministic, resolver-blind candidate review package.

    Strata are provenance/lifecycle buckets, not inferred gold labels. The
    function never receives resolver output, rankings, or M006.75 artifacts.
    """
    grouped: dict[str, list] = {}
    for example in examples:
        if example.hard_negative:
            stratum = "structural_negative_review"
        elif example.relevance == "positive":
            stratum = "authored_expected_claim_review"
        else:
            stratum = "unknown_abstention_review"
        grouped.setdefault(stratum, []).append(example)
    items = []
    for stratum, candidates in sorted(grouped.items()):
        for example in sorted(candidates, key=lambda item: (item.question_id, item.candidate_key)):
            items.append(
                BlindReviewItem(
                    review_id=(
                        f"{example.question_id}:{example.stage}:"
                        f"{example.candidate_key}:{example.candidate_path}"
                    ),
                    question_id=example.question_id,
                    question=example.question,
                    candidate_id=example.candidate_key,
                    candidate_claim=example.candidate_claim,
                    candidate_path=example.candidate_path,
                    candidate_key=example.candidate_key,
                    evidence=example.evidence,
                    stage=example.stage,
                    intent=example.intent,
                    sampling_stratum=stratum,
                    information_need=example.intent,
                )
            )
    if target_size >= len(items):
        return items
    per_stratum = max(1, target_size // len(grouped))
    selected = []
    for stratum in sorted(grouped):
        stratum_items = [item for item in items if item.sampling_stratum == stratum]
        selected.extend(stratum_items[:per_stratum])
    return sorted(selected, key=lambda item: item.review_id)


def blind_review_payload(item: BlindReviewItem) -> dict:
    """Serialize only reviewer-visible evidence, never sampling provenance."""
    payload = asdict(item)
    payload.pop("sampling_stratum", None)
    return payload


def validate_records(records: list[AdjudicationRecord]) -> None:
    for record in records:
        record.validate()


def read_jsonl(path: Path) -> list[AdjudicationRecord]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(AdjudicationRecord(**json.loads(line)))
    validate_records(records)
    return records
