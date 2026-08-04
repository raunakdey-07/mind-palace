"""Embedding service: wraps sentence-transformers for generating vector embeddings."""

from __future__ import annotations

import os
from typing import List

from sentence_transformers import SentenceTransformer


class Embedder:
    """Singleton wrapper around a Sentence-Transformer model."""

    _instance: "Embedder | None" = None

    def __new__(cls) -> "Embedder":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_model"):
            model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
            self._model = SentenceTransformer(model_name)

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension."""
        return self._model.get_sentence_embedding_dimension()

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts (batched)."""
        if not texts:
            return []
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_single(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        return self.embed([text])[0]
