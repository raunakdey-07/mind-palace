"""Reranker service: wraps sentence-transformers CrossEncoder for reranking."""

from __future__ import annotations

import os

from sentence_transformers import CrossEncoder


class Reranker:
    """Singleton wrapper around a CrossEncoder model for reranking."""

    _instance: Reranker | None = None

    def __new__(cls) -> Reranker:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_model"):
            model_name = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
            self._model = CrossEncoder(model_name)

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Score query-document pairs using the cross-encoder.

        Args:
            query: The search query.
            documents: List of document texts to score against the query.

        Returns:
            List of scores (higher = more relevant) as plain Python floats.
        """
        if not documents:
            return []
        # CrossEncoder.predict expects list of [query, doc] pairs
        pairs = [[query, doc] for doc in documents]
        scores = self._model.predict(pairs)
        # Ensure we return plain Python floats, not numpy types
        return [float(score) for score in scores]
