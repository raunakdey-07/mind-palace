"""Reranker service: wraps sentence-transformers CrossEncoder for reranking.

`sentence-transformers` is the `[torch]` extra, not a default dependency, so the
import is allowed to fail here. Binding it at module level meant that moving it to
an extra broke every `import api.services.reranker`, including the import inside
`retrieval`'s own rerank branch and the CI step that provisions the model --
failures with nothing to do with reranking. `embedder.py` resolves its backend the
same way: present or not, decided when it is asked for rather than at import.

The name is still bound at module scope, because that is what tests patch.
"""

from __future__ import annotations

import os
import threading
from typing import Any, ClassVar, Self

try:
    from sentence_transformers import CrossEncoder
except ImportError:  # the [torch] extra is not installed
    CrossEncoder = None


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
                    if CrossEncoder is None:
                        raise ImportError(
                            "reranking needs the [torch] extra; "
                            "pip install 'mindpalace-os[torch]'"
                        )
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
