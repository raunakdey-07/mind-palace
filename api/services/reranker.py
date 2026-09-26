"""Reranker service: wraps sentence-transformers CrossEncoder for reranking."""

from __future__ import annotations

import os
import threading
from typing import Any, ClassVar, Self

from sentence_transformers import CrossEncoder


class Reranker:
    """Singleton wrapper around a CrossEncoder model for reranking."""

    _instance: ClassVar[Reranker | None] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()
    _model_lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "model_name"):
            self.model_name = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
            self._model: Any | None = None

    def _get_model(self) -> Any:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._model = CrossEncoder(self.model_name)
        return self._model

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
        scores = self._get_model().predict(pairs)
        # Ensure we return plain Python floats, not numpy types
        return [float(score) for score in scores]
