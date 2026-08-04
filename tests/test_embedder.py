"""Tests for the embedding service."""

from api.services.embedder import Embedder


def test_embedder_singleton():
    e1 = Embedder()
    e2 = Embedder()
    assert e1 is e2


def test_embedder_dimension():
    embedder = Embedder()
    assert embedder.dimension > 0


def test_embed_single():
    embedder = Embedder()
    vec = embedder.embed_single("Hello world")
    assert len(vec) == embedder.dimension
    assert all(isinstance(v, float) for v in vec)


def test_embed_batch():
    embedder = Embedder()
    texts = ["Hello world", "Goodbye world", "Test sentence"]
    vecs = embedder.embed(texts)
    assert len(vecs) == 3
    assert all(len(v) == embedder.dimension for v in vecs)


def test_embed_empty():
    embedder = Embedder()
    vecs = embedder.embed([])
    assert vecs == []
