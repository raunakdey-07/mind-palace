"""Embedding service: wraps sentence-transformers for generating vector embeddings.

The embedding model is explicit configuration (EMBEDDING_MODEL env var).
Model name and dimension are exposed so they can be persisted alongside
chunks — mixing vectors from different models in one index is a silent
corruption bug, so the model identity must always travel with the vectors.
"""

from __future__ import annotations

import os

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class Embedder:
    """Singleton wrapper around a Sentence-Transformer model."""

    _instance: Embedder | None = None

    from typing import Self

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_model"):
            self.model_name = os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
            self._model = SentenceTransformer(self.model_name)

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension."""
        return self._model.get_embedding_dimension()

    @property
    def version(self) -> str:
        """Identity string persisted with every chunk's embedding metadata."""
        return f"{self.model_name}:{self.dimension}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts (batched, normalized)."""
        if not texts:
            return []
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        return self.embed([text])[0]
