"""Same-candidate, offline decision-provider benchmark for M007.1."""

from __future__ import annotations

from collections import Counter
import time

from api.services.memory_decision_dataset import load_development_examples
from api.services.memory_relevance import terms
from api.services.memory_decision_spike import (
    DecisionProvider,
    DecisionTrace,
    timed_decision,
)


def _candidate(example):
    query_terms = terms(example.question)
    candidate_terms = terms(
        f"{example.candidate_claim} {example.candidate_key} {example.candidate_path}"
    )
    overlap = len(query_terms & candidate_terms) / max(len(query_terms), 1)
    return {
        "id": example.candidate_key,
        "claim": example.candidate_claim,
        "path": example.candidate_path,
        "evidence": list(example.evidence),
        "relevance": overlap,
        "lexical_overlap": overlap,
    }


def _group(examples):
    grouped = {}
    for example in examples:
        grouped.setdefault(example.question_id, []).append(example)
    return grouped


def run_provider(provider: DecisionProvider, examples=None, *, provider_name="unknown"):
    """Run one provider on identical development candidate pools.

    Labels are retained for adjudication analysis only. They are never passed
    to the provider as state or converted into authoritative memory output.
    """

    rows = []
    for question_id, group in _group(examples or load_development_examples()).items():
        first = group[0]
        candidates = [_candidate(example) for example in group]
        result, latency = timed_decision(provider, first.question, candidates)
        selected = result["selected_id"]
        selected_example = next((item for item in group if item.candidate_key == selected), None)
        trace = DecisionTrace(
            question=first.question,
            candidate_id=selected,
            decision="abstain" if result["abstained"] else "select",
            confidence=float(result["choice_confidence"]),
            provider=provider_name,
            provider_version=getattr(provider, "provider_version", "local"),
            request_schema_version=getattr(provider, "request_schema_version", "local"),
            policy_version="m00675-baseline-candidate-pool",
            candidate_features={"candidate_count": len(candidates)},
            evidence=selected_example.evidence if selected_example else (),
            timestamp=time.time(),
        )
        rows.append(
            {
                "question_id": question_id,
                "trace": trace.as_dict(include_timestamp=True),
                "latency_ms": latency,
                "known_selected": (
                    selected_example.relevance == "positive" if selected_example else None
                ),
                "label_status": (
                    "known" if any(item.relevance == "positive" for item in group) else "unknown"
                ),
            }
        )
    return {
        "provider": provider_name,
        "candidate_rows": sum(
            len(group) for group in _group(examples or load_development_examples()).values()
        ),
        "question_rows": len(rows),
        "rows": rows,
    }


def classify_disagreement(baseline: dict, alternate: dict) -> str:
    b = baseline["trace"]["decision"]
    a = alternate["trace"]["decision"]
    if b == a == "abstain":
        return "AGREE_ABSTAIN"
    if b == a == "select":
        return "AGREE_SELECT"
    if b == "select" and a == "reject":
        return "BASELINE_SELECT_JEV_REJECT"
    if b == "select" and a == "abstain":
        return "BASELINE_SELECT_JEV_ABSTAIN"
    if b == "abstain" and a == "select":
        return "BASELINE_ABSTAIN_JEV_SELECT"
    if b == "reject" and a == "select":
        return "BASELINE_REJECT_JEV_SELECT"
    return "BASELINE_REJECT_JEV_SELECT"


def compare_runs(baseline: dict, alternate: dict) -> dict:
    by_id = {row["question_id"]: row for row in alternate["rows"]}
    disagreements = []
    counts = Counter()
    for row in baseline["rows"]:
        kind = classify_disagreement(row, by_id[row["question_id"]])
        counts[kind] += 1
        if not kind.startswith("AGREE"):
            disagreements.append({"question_id": row["question_id"], "type": kind})
    return {"counts": dict(counts), "disagreements": disagreements}


def build_review_queue(comparison: dict) -> list[dict]:
    """Rank disagreements for independent adjudication, not gold labeling."""

    priority = {
        "BASELINE_ABSTAIN_JEV_SELECT": 0,
        "BASELINE_SELECT_JEV_ABSTAIN": 1,
        "BASELINE_REJECT_JEV_SELECT": 2,
    }
    return sorted(
        comparison["disagreements"],
        key=lambda row: (priority.get(row["type"], 9), row["question_id"]),
    )
