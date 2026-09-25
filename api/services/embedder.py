"""Embedding service: wraps sentence-transformers for generating vector embeddings.

The embedding model is explicit configuration (EMBEDDING_MODEL env var).
Model name and dimension are exposed so they can be persisted alongside
chunks — mixing vectors from different models in one index is a silent
corruption bug, so the model identity must always travel with the vectors.
"""

from __future__ import annotations

import os
import threading
from typing import Any, ClassVar, Self

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class Embedder:
    """Singleton wrapper around a Sentence-Transformer model."""

    _instance: ClassVar[Embedder | None] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()
    _model_lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls) -> Self:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "model_name"):
            self.model_name = os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
            self._model: Any | None = None

    def _get_model(self) -> Any:
        """Load the resident model once, on the first actual embedding use."""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension."""
        return self._get_model().get_embedding_dimension()

    @property
    def version(self) -> str:
        """Identity string persisted with every chunk's embedding metadata."""
        return f"{self.model_name}:{self.dimension}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts (batched, normalized)."""
        if not texts:
            return []
        return self._get_model().encode(texts, normalize_embeddings=True).tolist()

    def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        return self.embed([text])[0]
