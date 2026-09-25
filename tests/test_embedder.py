"""Tests for the embedding service."""

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

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


def test_embedder_empty():
    embedder = Embedder()
    vecs = embedder.embed([])
    assert vecs == []


def test_embedder_loads_model_only_on_first_use(monkeypatch):
    previous = Embedder._instance
    calls = []

    class FakeModel:
        def __init__(self, name):
            calls.append(name)

        def get_embedding_dimension(self):
            return 2

        def encode(self, texts, normalize_embeddings):
            assert normalize_embeddings is True
            return SimpleNamespace(tolist=lambda: [[float(len(text)), 1.0] for text in texts])

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    Embedder._instance = None
    try:
        embedder = Embedder()
        assert calls == []
        assert embedder.embed(["x"]) == [[1.0, 1.0]]
        assert calls == ["all-MiniLM-L6-v2"]
        assert Embedder() is embedder
    finally:
        Embedder._instance = previous


def test_embedder_first_use_is_single_flight(monkeypatch):
    previous = Embedder._instance
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    class FakeModel:
        def __init__(self, _name):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.01)
            with state_lock:
                active -= 1

        def get_embedding_dimension(self):
            return 2

        def encode(self, texts, normalize_embeddings):
            assert normalize_embeddings is True
            return SimpleNamespace(tolist=lambda: [[1.0, 1.0] for _ in texts])

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    Embedder._instance = None
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: Embedder().embed(["x"]), range(8)))
        assert results == [[[1.0, 1.0]]] * 8
        assert max_active == 1
    finally:
        Embedder._instance = previous
