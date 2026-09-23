"""Offline semantic retrieval experiment over the unchanged M006 workload.

Run with python -m api.services.memory_query_benchmark --save eval/results/m0065.json.
All database work is scoped to the existing rollback-only benchmark sandbox.
"""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import yaml

from api.services.memory_benchmark import (
    _baseline,
    _measure,
    _selection,
    _set_score,
    check_provenance,
    memory_benchmark_workload,
)
from api.services.memory_public import _claims
from api.services.memory_query import rank_claims


async def run():
    rows = []
    async with memory_benchmark_workload(embeddings="cached") as workload:
        for stage in workload.spec["stages"]:
            await workload.apply_stage(stage["id"])
            for q in workload.spec["queries"]:
                expected = set(q.get("expected_current_claims", []))
                if q["stage"] != stage["id"] or not expected:
                    continue
                request = await workload.request_for(q, operation="pack", budget=128000)
                request = request.model_copy(update={"query": ""})
                full = await workload.execute("pack", request)
                claims = {c.id: c for c in _claims(full)}
                for strategy in ("lexical", "embedding", "hybrid"):
                    ranked = rank_claims(full, q["question"], workload.embedder, strategy)
                    # Candidate scores propagate within authored keys, NOT authority.
                    key_scores = {}
                    for item in ranked:
                        key = claims[item.id].key
                        key_scores[key] = max(key_scores.get(key, -1), item.score)
                    current = sorted(
                        full.current_memories,
                        key=lambda c: (-key_scores[c.key], c.id),
                    )
                    if q.get("path"):
                        current = [c for c in current if c.path == q["path"]]
                    texts = list(dict.fromkeys(c.claim for c in current))
                    top = key_scores[current[0].key] if current else 0
                    # Predeclared conservative relative band, measured not label-trained.
                    selected = {
                        c.claim
                        for c in current
                        if key_scores[c.key] >= top * 0.9
                        and (strategy != "embedding" or key_scores[c.key] >= 0.3)
                    }
                    rows.append(
                        {
                            "id": q["id"],
                            "question": q["question"],
                            "strategy": strategy,
                            "expected": sorted(expected),
                            "selected": sorted(selected),
                            "exact": selected == expected,
                            "recall": {
                                str(k): len(expected & set(texts[:k])) / len(expected)
                                for k in (1, 3, 5, 10)
                            },
                            "ranking": [
                                {"claim": c.claim, "score": key_scores[c.key]} for c in current
                            ],
                        }
                    )
        summary = {}
        for strategy in ("lexical", "embedding", "hybrid"):
            subset = [r for r in rows if r["strategy"] == strategy]
            summary[strategy] = {
                "n": len(subset),
                "exact": sum(r["exact"] for r in subset),
                "recall": {
                    str(k): sum(r["recall"][str(k)] for r in subset) / len(subset)
                    for k in (1, 3, 5, 10)
                },
            }
        return {"summary": summary, "cases": rows, "spec_hash": workload.spec_hash}


BUDGETS = (1000, 2000, 4000, 8000, 16000)
LABELS = ("expected_current_claims", "expected_historical_claims", "expected_conflicts")


def _exact_scores(q, response):
    actual = {
        "expected_current_claims": [c.claim for c in response.current_memories],
        "expected_historical_claims": [c.claim for c in response.historical_memories],
        "expected_conflicts": [sorted(c.claim for c in g.claims) for g in response.conflicts],
    }
    return {
        label: _set_score(
            sorted(map(sorted, q[label])) if label == "expected_conflicts" else q[label],
            actual[label],
        )
        for label in LABELS
        if label in q
    }


def _rankings(q, full, embedder):
    """Diagnostic candidate recall only; key propagation never changes status."""
    claims = {c.id: c for c in _claims(full)}
    expected = set(q.get("expected_current_claims", []))
    expected.update(q.get("expected_historical_claims", []))
    expected.update(c for group in q.get("expected_conflicts", []) for c in group)
    rows = {}
    for strategy in ("lexical", "embedding", "hybrid"):
        ranked = rank_claims(full, q["question"], embedder, strategy)
        key_scores = {}
        for item in ranked:
            key = claims[item.id].key
            key_scores[key] = max(key_scores.get(key, -1), item.score)
        candidates = (
            full.current_memories
            if q["intent"] in {"current", "temporal"} and not q.get("expected_conflicts")
            else list(claims.values())
        )
        candidates = sorted(candidates, key=lambda c: (-key_scores[c.key], c.id))
        if q.get("path"):
            candidates = [c for c in candidates if c.path == q["path"]]
        texts = list(dict.fromkeys(c.claim for c in candidates))
        rows[strategy] = {
            "expected": sorted(expected),
            "recall": {
                str(k): len(expected & set(texts[:k])) / len(expected) if expected else None
                for k in (1, 3, 5, 10)
            },
            "ranking": [
                {"id": c.id, "claim": c.claim, "status": c.status, "score": key_scores[c.key]}
                for c in candidates
            ],
        }
    return rows


async def _safety(workload, response, full, authority, budget):
    selected = {c.id: c for c in _claims(response)}
    authoritative = {c.id: c for c in _claims(authority)}
    current_ids = {c.id for c in authority.current_memories}
    returned_groups = {g.id: {c.id for c in g.claims} for g in response.conflicts}
    # Check even groups omitted from the query's relevance-filtered full result.
    broken_groups = [
        g.id
        for g in authority.conflicts
        if set(selected) & {c.id for c in g.claims}
        and not (
            {c.id for c in g.claims} <= selected.keys()
            and returned_groups.get(g.id) == {c.id for c in g.claims}
        )
    ]
    promoted = sorted(
        {c.id for c in response.current_memories if c.id not in current_ids}
        | {
            c.id
            for c in selected.values()
            if c.status == "CURRENT"
            and (c.id not in authoritative or authoritative[c.id].status != "CURRENT")
        }
    )
    dropped = _selection(response) != _selection(full)
    return {
        "provenance": await check_provenance(workload, response),
        "within_budget": len(response.canonical_json()) <= budget,
        "conflict_closed": not broken_groups,
        "incomplete_conflicts": broken_groups,
        "current_authority_preserved": not promoted,
        "promoted_claims": promoted,
        "content_dropped": dropped,
        "truncated_correct": response.truncated
        == (
            dropped
            or len(response.model_copy(update={"truncated": False}).canonical_json()) > budget
        ),
    }


def _availability(q, results):
    expected = set(q.get("expected_current_claims", []))
    expected.update(q.get("expected_historical_claims", []))
    expected.update(c for group in q.get("expected_conflicts", []) for c in group)
    available = sorted(c for c in expected if any(c in r.text for r in results))
    return {
        "expected": sorted(expected),
        "available": available,
        "expected_count": len(expected),
        "available_count": len(available),
        "recall": len(available) / len(expected) if expected else None,
        "note": "Live chunk text availability ONLY, not current/history/conflict authority; "
        "empty labels are not scoreable. Baseline has no temporal or path selectors.",
    }


def _aggregate(rows):
    scores = [r for r in rows if "scores" in r]
    return {
        "n": len(rows),
        "executed": len(scores),
        "exact": sum(all(s["passed"] for s in r["scores"].values()) for r in scores),
        "labels": {
            label: {
                "n": sum(label in r.get("expectations", {}) for r in rows),
                "exact": sum(r["scores"].get(label, {}).get("passed", False) for r in scores),
            }
            for label in LABELS
        },
        "recall": {
            strategy: {
                str(k): {
                    "n": len(values),
                    "mean": sum(values) / len(values) if values else None,
                }
                for k in (1, 3, 5, 10)
                for values in [
                    [
                        r["rankings"][strategy]["recall"][str(k)]
                        for r in rows
                        if r.get("rankings", {}).get(strategy, {}).get("recall", {}).get(str(k))
                        is not None
                    ]
                ]
            }
            for strategy in ("lexical", "embedding", "hybrid")
        },
        "safety": {
            check: {
                "n": sum("safety" in r for r in rows),
                "passed": sum(
                    (
                        bool(r.get("safety", {}).get(check, {}).get("passed"))
                        if check == "provenance"
                        else bool(r.get("safety", {}).get(check, False))
                    )
                    for r in rows
                ),
            }
            for check in (
                "provenance",
                "within_budget",
                "conflict_closed",
                "current_authority_preserved",
                "truncated_correct",
            )
        },
        "baseline_text_availability": {
            "expected_count": sum(r.get("baseline", {}).get("expected_count", 0) for r in rows),
            "available_count": sum(r.get("baseline", {}).get("available_count", 0) for r in rows),
            "not_authority": True,
        },
    }


async def run_end_to_end():
    question_file = Path(__file__).resolve().parents[2] / "eval/memory_questions.yaml"
    additional_spec = yaml.safe_load(question_file.read_text(encoding="utf-8"))
    additional = additional_spec["queries"]
    if additional_spec["version"] != 1 or len(additional) != 40:
        raise ValueError("Expected the 40 hand-authored v1 additional questions")
    rows, safety_failures, execution_failures = [], [], []

    def record_checks(row, measured):
        checks = measured["safety"]
        for name in (
            "within_budget",
            "conflict_closed",
            "current_authority_preserved",
            "truncated_correct",
            "provenance",
        ):
            passed = checks[name]["passed"] if name == "provenance" else checks[name]
            if not passed:
                safety_failures.append(
                    {
                        "id": row["id"],
                        "budget": measured["budget"],
                        "check": name,
                        "details": checks,
                    }
                )

    async with memory_benchmark_workload(embeddings="cached") as workload:
        originals = [q for q in workload.spec["queries"] if q.get("expected_current_claims")]
        if len(originals) != 24:
            raise ValueError("Expected the original 24 nonempty current-label diagnostics")
        cases = [({**q, "intent": "current"}, "original24") for q in originals] + [
            ({**q, "query": ""}, "additional40") for q in additional
        ]
        with patch("api.services.embedder.Embedder", return_value=workload.embedder):
            for stage in workload.spec["stages"]:
                await workload.apply_stage(stage["id"])
                for q, suite in cases:
                    if q["stage"] != stage["id"]:
                        continue
                    # Original evaluation is a current diagnostic, not remapped history QA.
                    labels = {k: q[k] for k in LABELS if k in q}
                    if suite == "original24":
                        labels = {"expected_current_claims": q["expected_current_claims"]}
                    row = {
                        "id": q["id"],
                        "suite": suite,
                        "question": q["question"],
                        "stage": q["stage"],
                        "intent": q["intent"],
                        "category": q.get("query_type", q["intent"]),
                        "expectations": labels,
                        "budgets": [],
                    }
                    rows.append(row)
                    try:
                        request = await workload.request_for(q, operation="pack", budget=128000)
                        request = request.model_copy(
                            update={"query": q["question"], "intent": q["intent"]}
                        )
                        row["request"] = workload.normalize(request.model_dump(mode="json"))
                        authority_operation = "replay" if "snapshot" in q else "history"
                        authority_request = await workload.request_for(
                            {**q, "query": "", "path": None}, operation=authority_operation
                        )
                        authority = await workload.execute(authority_operation, authority_request)
                        full = await workload.execute("query", request)
                        row.update(
                            budget=128000,
                            response=workload.normalize(full),
                            scores=_exact_scores(labels, full),
                            safety=await _safety(workload, full, full, authority, 128000),
                        )
                        record_checks(row, row)
                        ranking_request = await workload.request_for(
                            {**q, "query": ""}, operation="pack", budget=128000
                        )
                        candidates = await workload.execute("pack", ranking_request)
                        row["rankings"] = _rankings(
                            {**{k: v for k, v in q.items() if k not in LABELS}, **labels},
                            candidates,
                            workload.embedder,
                        )
                        for budget in BUDGETS if suite == "additional40" else ():
                            measured = {"budget": budget}
                            row["budgets"].append(measured)
                            try:
                                response = await workload.execute(
                                    "query", request.model_copy(update={"budget": budget})
                                )
                                measured.update(
                                    response=workload.normalize(response),
                                    characters=len(response.canonical_json()),
                                    scores=_exact_scores(labels, response),
                                    safety=await _safety(
                                        workload, response, full, authority, budget
                                    ),
                                )
                                record_checks(row, measured)
                            except Exception as exc:  # retain failures, never relabel as relevance
                                error = {
                                    "id": q["id"],
                                    "budget": budget,
                                    "type": type(exc).__name__,
                                    "message": str(exc),
                                }
                                measured["error"] = error
                                execution_failures.append(error)
                        vector = workload.embedder.embed_single(q["question"])
                        baseline = await _baseline(workload, q, vector)
                        row["baseline"] = {
                            **_availability(labels, baseline),
                            "candidates": [
                                {"path": r.source_path, "text": r.text, "score": r.score}
                                for r in baseline
                            ],
                        }
                    except Exception as exc:  # rollback sandbox still unwinds on fatal setup errors
                        error = {"id": q["id"], "type": type(exc).__name__, "message": str(exc)}
                        row["error"] = error
                        execution_failures.append(error)
            # One fixed stage/selector/question/budget, not a distribution across questions.
            timing_q = next(
                q for q in additional if q["id"] == "question-restored-jwt-path-dispute"
            )
            fixed = await workload.request_for(
                {**timing_q, "query": ""}, operation="pack", budget=8000
            )
            fixed = fixed.model_copy(
                update={"query": timing_q["question"], "intent": timing_q["intent"]}
            )
            _, query_timing = await _measure(lambda: workload.execute("query", fixed), 20)
            vector = workload.embedder.embed_single(timing_q["question"])
            _, baseline_timing = await _measure(lambda: _baseline(workload, timing_q, vector), 20)
            for name, measured in (("query", query_timing), ("baseline", baseline_timing)):
                if measured["errors"]:
                    execution_failures.append({"timing": name, "errors": measured["errors"]})
            timings = {
                "id": timing_q["id"],
                "stage": "G",
                "repetitions": 20,
                "request": workload.normalize(fixed.model_dump(mode="json")),
                "query": query_timing,
                "live_baseline_search_preembedded": baseline_timing,
            }
        summary = {
            suite: _aggregate([r for r in rows if r["suite"] == suite])
            for suite in ("original24", "additional40")
        }
        summary["categories"] = {
            suite: {
                category: _aggregate(
                    [r for r in rows if r["suite"] == suite and r["category"] == category]
                )
                for category in sorted({r["category"] for r in rows if r["suite"] == suite})
            }
            for suite in ("original24", "additional40")
        }
        summary["budgets"] = {
            str(budget): _aggregate(
                [
                    {**b, "expectations": r["expectations"]}
                    for r in rows
                    for b in r["budgets"]
                    if b["budget"] == budget
                ]
            )
            for budget in BUDGETS
        }
        summary["safety_failure_count"] = len(safety_failures)
        summary["execution_failure_count"] = len(execution_failures)
        return {
            "mode": "end-to-end",
            "summary": summary,
            "cases": rows,
            "spec_hash": workload.spec_hash,
            "additional_spec": additional_spec,
            "embeddings": "cached",
            "model": workload.embedder.model_name,
            "counts": {
                "original_queries": len(workload.spec["queries"]),
                "evaluated": dict(Counter(r["suite"] for r in rows)),
                "budget_cases": sum(len(r["budgets"]) for r in rows),
                "public_query_cases": sum("response" in r for r in rows),
                "final_database": await workload.counts(),
            },
            "stages": workload.stage_results,
            "timings": timings,
            "safety_failures": safety_failures,
            "execution_failures": execution_failures,
            "relevance_failures": [
                {"id": r["id"], "labels": [k for k, v in r["scores"].items() if not v["passed"]]}
                for r in rows
                if "scores" in r and not all(v["passed"] for v in r["scores"].values())
            ],
            "passed": not safety_failures and not execution_failures,
            "gate_scope": "Safety and execution only; exact relevance misses retained, not gated",
            "original39": {"status": "NOT RUN", "reason": "Optional mapped-intent evaluation"},
            "generation": {"status": "NOT RUN", "reason": "Retrieval only; no LLM or judge"},
        }


class EvaluationEmbeddingCache:
    """Transient exact-text memoization for evaluation only; never installed in production."""

    def __init__(self, embedder, *, memoize=True):
        self.embedder = embedder
        self.memoize = memoize
        self.vectors = {}
        self.counts = Counter()

    def embed(self, texts):
        self.counts.update(calls=1, texts=len(texts))
        missing = list(dict.fromkeys(t for t in texts if t not in self.vectors))
        if not self.memoize:
            missing = texts
        if missing:
            vectors = self.embedder.embed(missing)
            if len(vectors) != len(missing):
                raise ValueError("Cached model returned the wrong number of vectors")
            self.counts.update(model_calls=1, model_texts=len(missing))
            if not self.memoize:
                return vectors
            self.vectors.update(zip(missing, vectors))
        return [self.vectors[t] for t in texts]


def _frozen_policy(path):
    """SHA is over UTF-8 canonical JSON of the complete policy object.

    File contract: {"frozen": true, "policy": {approach, minimum, strong,
    relative, max_topics}, "sha256": <sha256 of sorted compact policy JSON>}.
    This validates an external freeze, not proof of when/how parameters were chosen.
    """
    from api.services.memory_relevance import RelevancePolicy

    raw = Path(path).read_bytes()
    document = json.loads(raw)
    params = document.get("policy", {})
    if document.get("frozen") is not True or set(params) != set(asdict(RelevancePolicy())):
        raise ValueError("Policy file requires frozen=true and all five policy parameters")
    if type(params["max_topics"]) is not int:
        raise ValueError("max_topics must be an integer")
    digest = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if document.get("sha256") != digest:
        raise ValueError("Frozen policy SHA-256 mismatch")
    return RelevancePolicy(**params), {
        "policy": params,
        "sha256": digest,
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "frozen": True,
    }


def _development_policies():
    from api.services.memory_relevance import RelevancePolicy

    return {
        "A": RelevancePolicy(approach="A", minimum=0.30),
        "B": RelevancePolicy(approach="B", minimum=0.30),
        **{
            f"{approach}-{minimum:.2f}": RelevancePolicy(
                approach=approach, minimum=minimum, strong=0.65, relative=0.90
            )
            for approach in "CDE"
            for minimum in (
                (0.35, 0.40, 0.45, 0.50) if approach == "C" else (0.30, 0.35, 0.40, 0.45, 0.50)
            )
        },
    }


def _expected_claims(q):
    return (
        set(q.get("expected_current_claims", []))
        | set(q.get("expected_historical_claims", []))
        | {c for group in q.get("expected_conflicts", []) for c in group}
    )


def _no_relevant_expected(q):
    if q.get("expected") == "no_relevant_memory":
        return True
    return q.get(
        "expected_no_relevant_memory", q.get("no_relevant_memory", not _expected_claims(q))
    )


def _reliability_scores(q, response):
    scores = _exact_scores(q, response)
    claims = {c.id: c for c in _claims(response)}
    actual_texts = {c.claim for c in claims.values()}
    topics = [
        {
            "id": topic["id"],
            **_set_score(topic["claims"], sorted(actual_texts & set(topic["claims"]))),
        }
        for topic in q.get("expected_topics", [])
    ]
    expected_empty = _no_relevant_expected(q)
    actual_empty = not claims
    constraint = "NO_RELEVANT_MEMORY" in response.constraints
    result = {
        "scores": scores,
        "exact": bool(scores) and all(s["passed"] for s in scores.values()),
        "abstention": {
            "expected": expected_empty,
            "actual_empty": actual_empty,
            "constraint_present": constraint,
            "passed": expected_empty == actual_empty and constraint == expected_empty,
            "constraint_consistent": constraint == actual_empty,
            "confusion": (
                ("TN" if actual_empty else "FP")
                if expected_empty
                else ("FN" if actual_empty else "TP")
            ),
        },
        "topics": topics,
        "subject": (
            _set_score(q["expected_keys"], sorted({c.key for c in claims.values()}))
            if "expected_keys" in q
            else None
        ),
        "missing_claims": sorted(_expected_claims(q) - actual_texts),
        "unexpected_claims": sorted(actual_texts - _expected_claims(q)),
        "label_false_positive_count": sum(len(s["unexpected"]) for s in scores.values()),
        "label_false_negative_count": sum(len(s["missing"]) for s in scores.values()),
    }
    result["fully_correct"] = (
        result["exact"]
        and result["abstention"]["passed"]
        and all(t["passed"] for t in topics)
        and (result["subject"] is None or result["subject"]["passed"])
    )
    return result


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _classification_metrics(tp, fp, fn, tn=None):
    return {
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "specificity": _ratio(tn, tn + fp) if tn is not None else None,
        "F1": _ratio(2 * tp, 2 * tp + fp + fn),
    }


def _score_micro(scores):
    # Conflict groups are atomic labels, matching _exact_scores, not individual sides.
    tp = sum(len(s["expected"]) - len(s["missing"]) for s in scores)
    return _classification_metrics(
        tp,
        sum(len(s["unexpected"]) for s in scores),
        sum(len(s["missing"]) for s in scores),
    )


def _derived_metrics(rows):
    executed = [r for r in rows if "scores" in r]
    confusion = Counter(r["abstention"]["confusion"] for r in executed)
    topics = [t for r in executed for t in r["topics"]]
    topic_cases = [r for r in executed if r["topics"]]
    subjects = [r["subject"] for r in executed if r["subject"] is not None]
    sizes = [r["characters"] for r in executed]
    return {
        "answer_detection": _classification_metrics(
            confusion["TP"],
            confusion["FP"],
            confusion["FN"],
            confusion["TN"],
        ),
        "claim_labels_micro": _score_micro([s for r in executed for s in r["scores"].values()]),
        "subject_keys_micro": _score_micro(subjects),
        "subject_exact": sum(s["passed"] for s in subjects),
        "temporal_scope_passed": sum(r["safety"]["temporal_scope_preserved"] for r in executed),
        "status_passed": sum(r["safety"]["status_preserved"] for r in executed),
        "final_exact": sum(
            r["fully_correct"]
            and r["safety"]["temporal_scope_preserved"]
            and r["safety"]["status_preserved"]
            for r in executed
        ),
        "topics": {
            "cases": len(topic_cases),
            "groups": len(topics),
            "groups_fully_covered": sum(t["passed"] for t in topics),
            "all_groups_cases": sum(all(t["passed"] for t in r["topics"]) for r in topic_cases),
            "mean_group_claim_recall": _ratio(sum(t["recall"] for t in topics), len(topics)),
            "mean_case_group_coverage": _ratio(
                sum(sum(t["passed"] for t in r["topics"]) / len(r["topics"]) for r in topic_cases),
                len(topic_cases),
            ),
            "per_group": {
                key: {
                    "n": len(group),
                    "fully_covered": sum(t["passed"] for t in group),
                    "mean_claim_recall": _ratio(sum(t["recall"] for t in group), len(group)),
                }
                for key in sorted({t["id"] for t in topics})
                for group in [[t for t in topics if t["id"] == key]]
            },
        },
        "output": {
            "n": len(sizes),
            "characters_total": sum(sizes),
            "characters_mean": _ratio(sum(sizes), len(sizes)),
            "characters_min": min(sizes, default=None),
            "characters_max": max(sizes, default=None),
            "utf8_bytes_total": sum(r["utf8_bytes"] for r in executed),
            "truncated": sum(r["truncated"] for r in executed),
            "empty": sum(r["abstention"]["actual_empty"] for r in executed),
            "relevance_omitted_labels": sum(r["relevance_omitted_labels"] for r in executed),
            "budget_omitted_labels": sum(r["budget_omitted_labels"] for r in executed),
            "unexpected_current_labels": sum(
                len(r["scores"].get("expected_current_claims", {}).get("unexpected", []))
                for r in executed
            ),
            "false_current_authority_claims": sum(
                len(r["safety"]["promoted_claims"]) for r in executed
            ),
        },
    }


def _reliability_summary(rows):
    executed = [r for r in rows if "scores" in r]
    unrelated = [r for r in executed if r["unrelated"]]
    topics = [t for r in executed for t in r["topics"]]
    subjects = [r["subject"] for r in executed if r["subject"] is not None]
    return {
        "n": len(rows),
        "executed": len(executed),
        "execution_failures": len(rows) - len(executed),
        "exact": sum(r["exact"] for r in executed),
        "fully_correct": sum(r["fully_correct"] for r in executed),
        "derived": _derived_metrics(rows),
        "confusion_relevant_positive": {
            k: sum(r["abstention"]["confusion"] == k for r in executed)
            for k in ("TP", "FN", "FP", "TN")
        },
        "unrelated": {
            "n": len(unrelated),
            "false_positive": sum(not r["abstention"]["actual_empty"] for r in unrelated),
            "true_negative": sum(r["abstention"]["actual_empty"] for r in unrelated),
            "constraint_present": sum(r["abstention"]["constraint_present"] for r in unrelated),
        },
        "abstention_exact": sum(r["abstention"]["passed"] for r in executed),
        "constraint_consistency_failures": sum(
            not r["abstention"]["constraint_consistent"] for r in executed
        ),
        "label_false_positives": sum(r["label_false_positive_count"] for r in executed),
        "label_false_negatives": sum(r["label_false_negative_count"] for r in executed),
        "topics": {"n": len(topics), "covered": sum(t["passed"] for t in topics)},
        "subject": {
            "n": len(subjects),
            "exact": sum(s["passed"] for s in subjects),
            "false_positive_keys": sum(len(s["unexpected"]) for s in subjects),
            "missing_keys": sum(len(s["missing"]) for s in subjects),
        },
        "safety": {
            name: {
                "n": len(executed),
                "passed": sum(
                    r["safety"][name]["passed"] if name == "provenance" else r["safety"][name]
                    for r in executed
                ),
            }
            for name in (
                "provenance",
                "within_budget",
                "conflict_closed",
                "current_authority_preserved",
                "truncated_correct",
                "temporal_scope_preserved",
                "status_preserved",
            )
        },
    }


async def _reliability_measurement(workload, q, response, selected, authority, budget):
    safety = await _safety(workload, response, selected, authority, budget)
    authoritative = {c.id: c for c in _claims(authority)}
    safety["temporal_scope_preserved"] = response.state == authority.state and all(
        c.id in authoritative for c in _claims(response)
    )
    safety["status_preserved"] = all(
        c.id in authoritative and c.status == authoritative[c.id].status for c in _claims(response)
    )
    scores = _reliability_scores(q, response)
    # Budget omission is not evidence of irrelevance; preserve select's decision.
    scores["abstention"]["constraint_consistent"] = (
        "NO_RELEVANT_MEMORY" in response.constraints
    ) == ("NO_RELEVANT_MEMORY" in selected.constraints)
    selected_scores = _exact_scores(q, selected)
    return {
        "budget": budget,
        "characters": len(response.canonical_json()),
        "utf8_bytes": len(response.canonical_json().encode("utf-8")),
        "truncated": response.truncated,
        "relevance_omitted_labels": sum(len(s["missing"]) for s in selected_scores.values()),
        "budget_omitted_labels": sum(
            len(set(s["missing"]) - set(selected_scores[label]["missing"]))
            for label, s in scores["scores"].items()
        ),
        "response": workload.normalize(response),
        **scores,
        "safety": safety,
    }


async def _reliability_timing(workload, q, policy):
    from api.services.memory_relevance import RelevancePolicy

    request = await workload.request_for({**q, "query": ""}, operation="pack", budget=8000)
    request = request.model_copy(update={"query": q["question"], "intent": q["intent"]})
    # Time actual cached-model inference, NOT memoized evaluation vectors.
    instrumented = EvaluationEmbeddingCache(workload.embedder, memoize=False)
    calls = []

    async def invoke():
        before = instrumented.counts.copy()
        try:
            return await workload.execute("query", request)
        finally:
            calls.append(dict(instrumented.counts - before))

    with (
        patch.object(RelevancePolicy, "configured", return_value=policy),
        patch("api.services.embedder.Embedder", return_value=instrumented),
    ):
        response, measured = await _measure(invoke, 20)
    return {
        "id": q["id"],
        "stage": q["stage"],
        "policy": asdict(policy),
        "request": workload.normalize(request.model_dump(mode="json")),
        "repetitions": 20,
        "warmup_calls": calls[:1],
        "measured_calls": calls[1:],
        "embedding_totals_including_warmup": dict(instrumented.counts),
        "memoized_vectors": False,
        **measured,
        "response": workload.normalize(response) if response is not None else None,
    }


async def run_reliability(mode, policy_file=None):
    from api.services.memory_public import bounded_pack
    from api.services.memory_query import interpret, select
    from api.services.memory_relevance import RelevancePolicy, plan, relevance

    root = Path(__file__).resolve().parents[2]
    # Resolve policy file relative to project root for consistent behavior.
    policy_path = (root / policy_file).resolve() if policy_file else None
    frozen, freeze = _frozen_policy(policy_path) if policy_path else (None, None)
    if mode == "heldout" and frozen is None:
        raise ValueError("Heldout requires an externally frozen --policy-file with SHA-256")
    if mode not in {"development", "heldout"}:
        raise ValueError("Unknown reliability mode")
    # Keep the heldout open strictly behind mode selection AND freeze validation.
    question_file = (
        root
        / "eval"
        / ("memory_questions.yaml" if mode == "development" else "memory_questions_heldout.yaml")
    )
    question_bytes = question_file.read_bytes()
    spec = yaml.safe_load(question_bytes)
    additional = spec["queries"]
    expected_count = 40 if mode == "development" else 60
    if spec["version"] != 1 or len(additional) != expected_count:
        raise ValueError(f"Expected {expected_count} v1 {mode} questions")
    if mode == "heldout":
        declared_hash = json.loads(Path(policy_file).read_text(encoding="utf-8")).get(
            "heldout_sha256"
        )
        if declared_hash != hashlib.sha256(question_bytes).hexdigest():
            raise ValueError("Heldout questions do not match the frozen file's heldout_sha256")
        freeze["heldout_sha256"] = declared_hash
        for q in additional:
            if not all(k in q for k in (*LABELS, "expected_keys")):
                raise ValueError(f"{q['id']}: incomplete heldout labels")
            if not (q.get("category") or q.get("query_type")):
                raise ValueError(f"{q['id']}: missing category")
            if q.get("expected") not in (None, "no_relevant_memory"):
                raise ValueError(f"{q['id']}: unknown expected selector")
            if not _expected_claims(q) and q.get("expected") != "no_relevant_memory":
                raise ValueError(f"{q['id']}: empty labels require explicit abstention")
            if q.get("expected") == "no_relevant_memory" and (
                _expected_claims(q) or q["expected_keys"]
            ):
                raise ValueError(f"{q['id']}: inconsistent abstention labels")
            if not isinstance(q["expected_keys"], list) or not all(
                isinstance(k, str) for k in q["expected_keys"]
            ):
                raise ValueError(f"{q['id']}: expected_keys must be a string list")
            if not isinstance(q.get("expected_topics", []), list) or any(
                not isinstance(t, dict)
                or not isinstance(t.get("id"), str)
                or not isinstance(t.get("claims"), list)
                or not t["claims"]
                or not all(isinstance(c, str) for c in t["claims"])
                for t in q.get("expected_topics", [])
            ):
                raise ValueError(f"{q['id']}: expected_topics must contain {{id, claims}}")
            if q.get("category") == "multi-topic" and not q.get("expected_topics"):
                raise ValueError(f"{q['id']}: multi-topic requires topic groups")
        if sum(q.get("expected") == "no_relevant_memory" for q in additional) != 10:
            raise ValueError("Expected ten genuine unrelated heldout questions")
    policies = (
        _development_policies()
        if mode == "development"
        else {
            "A": RelevancePolicy(approach="A", minimum=0.30),
            "frozen": frozen,
        }
    )
    if mode == "development" and frozen is not None:
        policies["frozen"] = frozen
    # Fixed development representatives only, never selected by observed performance.
    representatives = {
        "single": "question-pilot-message-bus",
        "multi": "question-three-system-roles",
        "temporal": "question-pilot-bus-from-restoration",
        "abstain": "question-unrelated-payroll",
    }
    if mode == "heldout":
        # First authored member of each category, fixed before any model evaluation.
        representatives = {
            kind: next(q["id"] for q in additional if q["category"] == category)
            for kind, category in (
                ("single", "current"),
                ("multi", "multi-topic"),
                ("temporal", "temporal"),
                ("abstain", "abstention"),
            )
        }
    timing_questions = {q["id"]: q for q in spec["queries"]}
    rows, timings, failures = [], {}, []
    async with memory_benchmark_workload(embeddings="cached") as workload:
        originals = [q for q in workload.spec["queries"] if q.get("expected_current_claims")]
        if len(originals) != 24:
            raise ValueError("Expected original24 nonempty current diagnostics")
        cases = [({**q, "query": ""}, f"{mode}{expected_count}") for q in additional]
        if mode == "development":
            cases = [
                (
                    {
                        **{k: v for k, v in q.items() if k not in LABELS},
                        "expected_current_claims": q["expected_current_claims"],
                        "intent": "current",
                    },
                    "original24",
                )
                for q in originals
            ] + cases
        if len({q["id"] for q, _ in cases}) != len(cases):
            raise ValueError("Question IDs must be unique")
        if any(q["stage"] not in {s["id"] for s in workload.spec["stages"]} for q, _ in cases):
            raise ValueError("Question references an unknown stage")
        cache = EvaluationEmbeddingCache(workload.embedder)
        for stage in workload.spec["stages"]:
            await workload.apply_stage(stage["id"])
            for q, suite in cases:
                if q["stage"] != stage["id"]:
                    continue
                category = q.get("category", q.get("query_type", q["intent"]))
                if mode == "development" and q["id"] in {
                    "question-three-system-roles",
                    "question-pilot-target-versus-live",
                }:
                    category = "multi"
                unrelated = (
                    q["id"] in {"question-unrelated-weather", "question-unrelated-payroll"}
                    if mode == "development"
                    else q.get("expected") == "no_relevant_memory"
                )
                request = await workload.request_for(q, operation="pack", budget=128000)
                request = request.model_copy(update={"query": q["question"], "intent": q["intent"]})
                operation = "replay" if "snapshot" in q else "history"
                authority_request = await workload.request_for(
                    {**q, "query": "", "path": None}, operation=operation
                )
                # history/replay are unbounded and include the same complete projection
                # as public query's pack projection, without a preliminary budget/path gate.
                authority = await workload.execute(operation, authority_request)
                intent = interpret(request)
                representations = [
                    f"{c.claim} {c.key} {c.path}"
                    for c in {c.id: c for c in _claims(authority)}.values()
                ]
                cache.embed(
                    representations
                    + [
                        text
                        for policy in policies.values()
                        for text in plan(request, intent.name, policy).topics
                    ]
                )
                for name, policy in policies.items():
                    row = {
                        "id": q["id"],
                        "suite": suite,
                        "category": category,
                        "unrelated": unrelated,
                        "policy": name,
                        "question": q["question"],
                        "stage": q["stage"],
                        "intent": q["intent"],
                        "expectations": {
                            k: v
                            for k, v in q.items()
                            if k.startswith("expected_") or k in {"expected", "no_relevant_memory"}
                        },
                        "request": workload.normalize(request.model_dump(mode="json")),
                        "budgets": [],
                    }
                    rows.append(row)
                    try:
                        captured = {}

                        def observe(*args, captured=captured, **kwargs):
                            scores, interpreted, work = relevance(*args, **kwargs)
                            captured.update(key_scores=scores, plan=asdict(interpreted), work=work)
                            return scores, interpreted, work

                        before = cache.counts.copy()
                        with patch("api.services.memory_relevance.relevance", side_effect=observe):
                            selected = select(authority, request, intent, cache, policy)
                        row.update(captured, embedding_work=dict(cache.counts - before))
                        row.update(
                            await _reliability_measurement(
                                workload,
                                q,
                                selected,
                                selected,
                                authority,
                                max(128000, len(selected.canonical_json())),
                            )
                        )
                        row["projection"] = "unbounded select"
                        for budget in BUDGETS if suite != "original24" else ():
                            measured: dict = {"budget": budget}
                            row["budgets"].append(measured)
                            try:
                                response = bounded_pack(selected, budget)
                                # Do not mask constraint loss in the production packer.
                                measured.update(
                                    await _reliability_measurement(
                                        workload,
                                        q,
                                        response,
                                        selected,
                                        authority,
                                        budget,
                                    )
                                )
                            except Exception as exc:  # noqa: BLE001 -- retain each budget failure
                                measured["error"] = {
                                    "type": type(exc).__name__,
                                    "message": str(exc),
                                }
                                failures.append({"id": q["id"], "policy": name, **measured})
                    except Exception as exc:  # noqa: BLE001 -- retain each policy failure
                        row["error"] = {"type": type(exc).__name__, "message": str(exc)}
                        failures.append({"id": q["id"], "policy": name, **row["error"]})
            for kind, qid in representatives.items():
                q = timing_questions[qid]
                if q["stage"] != stage["id"]:
                    continue
                timings[kind] = {}
                for name in ("A", "frozen") if frozen is not None else ("A",):
                    measured = await _reliability_timing(workload, q, policies[name])
                    timings[kind][name] = measured
                    if measured["errors"]:
                        failures.append(
                            {"timing": kind, "policy": name, "errors": measured["errors"]}
                        )
        summary = {}
        for name in policies:
            subset = [r for r in rows if r["policy"] == name]
            summary[name] = {"total": _reliability_summary(subset), "suites": {}}
            for suite in sorted({r["suite"] for r in subset}):
                members = [r for r in subset if r["suite"] == suite]
                summary[name]["suites"][suite] = {
                    **_reliability_summary(members),
                    "categories": {
                        category: _reliability_summary(
                            [r for r in members if r["category"] == category]
                        )
                        for category in sorted({r["category"] for r in members})
                    },
                    "budgets": {
                        str(budget): {
                            **_reliability_summary(
                                [
                                    {**b, "unrelated": r["unrelated"]}
                                    for r in members
                                    for b in r["budgets"]
                                    if b["budget"] == budget
                                ]
                            ),
                            "categories": {
                                category: _reliability_summary(
                                    [
                                        {**b, "unrelated": r["unrelated"]}
                                        for r in members
                                        if r["category"] == category
                                        for b in r["budgets"]
                                        if b["budget"] == budget
                                    ]
                                )
                                for category in sorted({r["category"] for r in members})
                            },
                        }
                        for budget in BUDGETS
                        if suite != "original24"
                    },
                }
        safety_failures = [
            {"id": r["id"], "policy": r["policy"], "budget": measured["budget"], "check": k}
            for r in rows
            for measured in [r, *r["budgets"]]
            for k, v in measured.get("safety", {}).items()
            if k
            in {
                "provenance",
                "within_budget",
                "conflict_closed",
                "current_authority_preserved",
                "truncated_correct",
                "temporal_scope_preserved",
                "status_preserved",
            }
            and not (v["passed"] if k == "provenance" else v)
        ]
        return {
            "mode": mode,
            "summary": summary,
            "cases": rows,
            "policies": {k: asdict(v) for k, v in policies.items()},
            "freeze": freeze,
            "selection": "NONE: externally chosen/frozen parameters only; no automatic tuning",
            "spec_hash": workload.spec_hash,
            "question_sha256": hashlib.sha256(question_bytes).hexdigest(),
            "question_spec": spec,
            "implementation_sha256": {
                name: hashlib.sha256((root / "api/services" / name).read_bytes()).hexdigest()
                for name in (
                    "memory_relevance.py",
                    "memory_query.py",
                    "memory_public.py",
                    "memory_query_benchmark.py",
                )
            },
            "model": workload.embedder.model_name,
            "embeddings": "cached",
            "embedding_cache": {
                "exact_text_only": True,
                "counts": dict(cache.counts),
                "unique_texts": len(cache.vectors),
                "production_cache": False,
            },
            "counts": {
                "questions": len(cases),
                "policies": len(policies),
                "rows": len(rows),
                "budget_rows": sum(len(r["budgets"]) for r in rows),
                "final_database": await workload.counts(),
            },
            "stages": workload.stage_results,
            "timings": timings,
            "frozen_timing": "RUN" if frozen is not None else "NOT RUN: no external frozen policy",
            "heldout": (
                "NOT OPENED OR RUN" if mode == "development" else "RUN: external freeze verified"
            ),
            "safety_failures": safety_failures,
            "execution_failures": failures,
            "relevance_failures": [
                {
                    "id": r["id"],
                    "policy": r["policy"],
                    "category": r["category"],
                    "budget": measured["budget"],
                    "label_exact": measured["exact"],
                    "missing": measured["missing_claims"],
                    "unexpected": measured["unexpected_claims"],
                    "scores": measured["scores"],
                    "subject": measured["subject"],
                    "topics": measured["topics"],
                    "abstention": measured["abstention"],
                }
                for r in rows
                for measured in [r, *r["budgets"]]
                if "scores" in measured and not measured["fully_correct"]
            ],
            "passed": not safety_failures and not failures,
            "gate_scope": (
                "Safety/execution only; relevance and constraint misses retained, not gated"
            ),
            "notes": [
                (
                    "Exact totals use declared claim labels; fully_correct also checks "
                    "abstention/topics/keys."
                ),
                (
                    "Positive confusion class is relevant memory; unrelated confusion is "
                    "reported separately."
                ),
                (
                    "Development empty deleted-path case is not an unrelated question "
                    "(only two unrelated)."
                ),
                (
                    "Budgets use production bounded_pack as-is; constraint loss is "
                    "reported, not repaired."
                ),
                (
                    "Timing uses public query with real local cached model, one warmup plus "
                    "20 repetitions."
                ),
                (
                    "Heldout timing uses first authored case of each of four categories, "
                    "fixed before scoring."
                ),
                (
                    "Answer detection TP means nonempty output on a relevant question, "
                    "NOT correct subject/answer."
                ),
                (
                    "Claim/key micro specificity is undefined: no finite negative label "
                    "universe is declared."
                ),
                (
                    "Topic coverage uses all claims in each authored group, independent "
                    "of status; exact labels check status fields."
                ),
                (
                    "Temporal/status safety can pass on empty or wrong-subject results; "
                    "final exact is separate."
                ),
                (
                    "Temporal safety uses the persistence projection at the structured "
                    "cutoff as oracle, not independent database truth."
                ),
                (
                    "Budget empty output without NO_RELEVANT_MEMORY is omission, not "
                    "relevance abstention."
                ),
                (
                    "Previous development final response erroneously said E .30 multi "
                    "exact 1/2; artifact says 0/2. Frozen selection_basis repeats that "
                    "prose error and was not modified."
                ),
                (
                    "Heldout validation adapted before evaluation: expected:no_relevant_memory, "
                    "topics only for multi-topic; original pre-freeze harness assumed "
                    "boolean/all-case topics."
                ),
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", required=True)
    parser.add_argument(
        "--end-to-end", action="store_true", help="Evaluate real public query calls"
    )
    parser.add_argument("--reliability", choices=("development", "heldout"))
    parser.add_argument(
        "--policy-file",
        help="External frozen JSON: frozen=true, policy (all five fields), "
        "sha256 (sorted compact policy JSON); required for heldout",
    )
    args = parser.parse_args()
    if args.reliability and args.end_to_end:
        parser.error("--reliability and --end-to-end are mutually exclusive")
    if args.policy_file and not args.reliability:
        parser.error("--policy-file requires --reliability")
    if args.reliability == "heldout" and not args.policy_file:
        parser.error("heldout requires an externally frozen --policy-file")
    result = asyncio.run(
        run_reliability(args.reliability, args.policy_file)
        if args.reliability
        else run_end_to_end() if args.end_to_end else run()
    )
    Path(args.save).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    if (args.end_to_end or args.reliability) and not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
