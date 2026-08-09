"""Evaluation service for retrieval and answer quality."""

from __future__ import annotations

from typing import Any

import yaml


class EvaluationService:
    """Service for running evaluations."""

    @staticmethod
    def load_benchmarks(file_path: str) -> list[dict[str, Any]]:
        """Load benchmark dataset from YAML."""
        with open(file_path) as f:
            return yaml.safe_load(f)

    @staticmethod
    def calculate_precision_at_k(expected: list[str], retrieved: list[str], k: int) -> float:
        """Calculate Precision@K."""
        if not expected:
            return 1.0
        expected_set = set(expected)
        retrieved_set = set(retrieved[:k])
        hits = expected_set & retrieved_set
        return len(hits) / len(expected_set)

    @staticmethod
    def calculate_reciprocal_rank(expected: list[str], retrieved: list[str]) -> float:
        """Calculate Reciprocal Rank."""
        for i, doc_id in enumerate(retrieved):
            if doc_id in expected:
                return 1.0 / (i + 1)
        return 0.0

    @staticmethod
    def calculate_ndcg(expected: list[str], retrieved: list[str], k: int) -> float:
        """Calculate Normalized Discounted Cumulative Gain."""
        if not expected:
            return 1.0

        # Calculate DCG
        dcg = 0.0
        for i, doc_id in enumerate(retrieved[:k]):
            if doc_id in expected:
                dcg += 1.0 / (i + 2)  # log2(i+2) approximation for relevance=1

        # Calculate IDCG (ideal DCG)
        idcg = 0.0
        for i in range(min(len(expected), k)):
            idcg += 1.0 / (i + 2)

        return dcg / idcg if idcg > 0 else 0.0
