"""Development-only memory decision examples.

This module derives candidate examples from authored corpus structure and
development labels. It never reads the frozen held-out question file and never
produces authoritative memory state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

DecisionLabel = Literal["positive", "negative", "unknown"]


@dataclass(frozen=True)
class MemoryDecisionExample:
    question_id: str
    question: str
    stage: str
    intent: str
    candidate_key: str
    candidate_claim: str
    candidate_path: str
    evidence: tuple[str, ...]
    relevance: DecisionLabel
    subject: DecisionLabel
    temporal: DecisionLabel
    evidence_sufficiency: Literal["sufficient", "insufficient", "unknown"]
    hard_negative: bool
    label_source: Literal["authored_expected_claim", "structural_negative", "unknown"]
    label_confidence: float
    adjudication_status: Literal["authored", "unadjudicated", "unknown"]


def _quotes(value) -> tuple[str, ...]:
    values = [value] if isinstance(value, str) else value
    return tuple(values or ())


def _claims(root: Path, stage: str, deleted: set[str]) -> list[dict]:
    directory = root / "examples" / "evaluation" / "corpus" / f"stage-{stage.lower()}"
    claims = []
    for source in sorted(directory.rglob("*.md")):
        path = source.relative_to(directory).as_posix()
        if path in deleted:
            continue
        _, frontmatter, _ = source.read_text(encoding="utf-8").split("---", 2)
        for claim in yaml.safe_load(frontmatter).get("claims", []):
            claims.append(
                {
                    "key": claim["key"],
                    "claim": claim["claim"],
                    "path": path,
                    "evidence": _quotes(claim.get("evidence")),
                }
            )
    return claims


def _development_questions(root: Path) -> list[dict]:
    benchmark = yaml.safe_load(
        (root / "eval" / "memory_benchmarks.yaml").read_text(encoding="utf-8")
    )
    questions = yaml.safe_load(
        (root / "eval" / "memory_questions.yaml").read_text(encoding="utf-8")
    )["queries"]
    by_stage = {stage["id"]: set(stage["delete"]) for stage in benchmark["stages"]}
    return [
        {
            **question,
            "delete": by_stage[question["stage"]],
            "source": "memory_questions.yaml",
        }
        for question in questions
    ]


def load_development_examples(root: Path | None = None) -> list[MemoryDecisionExample]:
    """Build labeled candidate pairs from development questions only.

    Positive labels come from existing expected claims. Non-expected authored
    claims become hard negatives only when the question has an explicit
    nonempty expected key set. Abstention questions are retained as unknown
    candidate decisions until independently authored candidate-level labels
    exist; treating every corpus claim as negative would manufacture labels.
    """

    root = Path(root or Path(__file__).resolve().parents[2])
    examples = []
    for question in _development_questions(root):
        expected_claims = set(question["expected_current_claims"])
        expected_claims.update(question["expected_historical_claims"])
        expected_claims.update(claim for pair in question["expected_conflicts"] for claim in pair)
        expected_keys = set()
        for claim in _claims(root, question["stage"], set(question["delete"])):
            if claim["claim"] in expected_claims:
                expected_keys.add(claim["key"])
        for claim in _claims(root, question["stage"], set(question["delete"])):
            is_positive = claim["claim"] in expected_claims
            if not expected_keys:
                relevance: DecisionLabel = "unknown"
                subject: DecisionLabel = "unknown"
                hard_negative = False
            else:
                relevance = "positive" if is_positive else "negative"
                subject = "positive" if claim["key"] in expected_keys else "negative"
                hard_negative = not is_positive
            examples.append(
                MemoryDecisionExample(
                    question_id=question["id"],
                    question=question["question"],
                    stage=question["stage"],
                    intent=question["intent"],
                    candidate_key=claim["key"],
                    candidate_claim=claim["claim"],
                    candidate_path=claim["path"],
                    evidence=claim["evidence"],
                    relevance=relevance,
                    subject=subject,
                    temporal="unknown",
                    evidence_sufficiency=(
                        "sufficient" if is_positive and claim["evidence"] else "unknown"
                    ),
                    hard_negative=hard_negative,
                    label_source=(
                        "authored_expected_claim"
                        if is_positive and expected_keys
                        else "structural_negative" if expected_keys else "unknown"
                    ),
                    label_confidence=1.0 if is_positive and expected_keys else 0.0,
                    adjudication_status=(
                        "authored"
                        if is_positive and expected_keys
                        else "unadjudicated" if expected_keys else "unknown"
                    ),
                )
            )
    return examples


def dataset_integrity_summary(examples: list[MemoryDecisionExample]) -> dict[str, int]:
    """Summarize label provenance without promoting structural negatives to gold labels."""
    return {
        "examples": len(examples),
        "authored": sum(item.adjudication_status == "authored" for item in examples),
        "unadjudicated": sum(item.adjudication_status == "unadjudicated" for item in examples),
        "unknown": sum(item.adjudication_status == "unknown" for item in examples),
    }
