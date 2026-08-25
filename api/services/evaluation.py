"""Evaluation service: deterministic retrieval metrics and benchmark loading."""

from __future__ import annotations

from typing import Any

import yaml


def recall_at_k(expected: list[str], retrieved: list[str], k: int) -> float:
    """Fraction of expected documents present in the top-k retrieved."""
    if not expected:
        return 0.0
    expected_set = set(expected)
    retrieved_set = set(retrieved[:k])
    return len(expected_set & retrieved_set) / len(expected_set)


def _dedupe(retrieved: list[str]) -> list[str]:
    """Collapse duplicate titles, preserving first-occurrence order.

    Retrieval returns chunks; evaluation compares documents. Multiple chunks
    of one document must not inflate per-document metrics.
    """
    return list(dict.fromkeys(retrieved))


def precision_at_k(expected: list[str], retrieved: list[str], k: int) -> float:
    """Fraction of distinct relevant docs among the top-k distinct results."""
    if k <= 0:
        return 0.0
    top = _dedupe(retrieved)[:k]
    if not top:
        return 0.0
    expected_set = set(expected)
    hits = sum(1 for doc in top if doc in expected_set)
    return hits / len(top)


def reciprocal_rank(expected: list[str], retrieved: list[str]) -> float:
    """Reciprocal rank of the first relevant result (0 if none found)."""
    expected_set = set(expected)
    for i, doc in enumerate(retrieved):
        if doc in expected_set:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(expected: list[str], retrieved: list[str], k: int) -> float:
    """nDCG with binary relevance over distinct retrieved documents."""
    if not expected or k <= 0:
        return 0.0
    expected_set = set(expected)
    ranked = [doc for doc in _dedupe(retrieved)[:k]]

    dcg = sum(1.0 / _log2(i + 2) for i, doc in enumerate(ranked) if doc in expected_set)
    ideal_hits = min(len(expected_set), k)
    idcg = sum(1.0 / _log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def _log2(n: int) -> float:
    import math

    return math.log2(n)


class EvaluationService:
    """Service for loading benchmarks and computing aggregate metrics."""

    @staticmethod
    def load_benchmarks(file_path: str) -> list[dict[str, Any]]:
        """Load and validate benchmark dataset from YAML.

        Raises ValueError on malformed entries so bad data fails loudly.
        """
        with open(file_path) as f:
            data = yaml.safe_load(f)

        if not isinstance(data, list):
            raise ValueError(f"Benchmark file must contain a list, got {type(data).__name__}")

        validated: list[dict[str, Any]] = []
        seen_queries: set[str] = set()
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise ValueError(f"Benchmark entry {i} must be a mapping")
            query = entry.get("query")
            expected = entry.get("expected") or entry.get("relevant_documents")
            if not query or not isinstance(query, str):
                raise ValueError(f"Benchmark entry {i} missing non-empty 'query'")
            if not expected or not isinstance(expected, list):
                raise ValueError(f"Benchmark entry {i} ('{query}') missing 'expected' list")
            if not all(isinstance(d, str) and d for d in expected):
                raise ValueError(f"Benchmark entry {i} ('{query}') has invalid expected docs")
            if query in seen_queries:
                raise ValueError(f"Duplicate benchmark query: '{query}'")
            seen_queries.add(query)
            validated.append(
                {
                    "query": query,
                    "expected": list(dict.fromkeys(expected)),  # dedupe, preserve order
                    "category": entry.get("category", "general"),
                }
            )
        return validated
