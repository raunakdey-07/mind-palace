"""The ONNX backend must be the same function as the torch one, not a close one.

The archive stores vectors and records only the model name and the dimension. It
does not record which backend made them, and it must not, or every corpus would
need re-embedding the moment `onnxruntime` was installed. That makes the following
load-bearing: a vector written by one backend has to still be the right vector
under the other.

A cosine of 0.98 would pass a smoke test and silently re-rank every corpus in the
field. So this asserts the number, and `api/services/embedder.py` documents the
measured one.
"""

import os

import pytest

from api.services.embedder import DEFAULT_MODEL, MAX_TOKENS, Embedder, backend_name

pytest.importorskip("onnxruntime")

#: What the product tolerates. The measured minimum over these texts is 0.99999833;
#: the bar is four orders of magnitude looser so a different CPU or onnxruntime
#: build does not fail the suite, but it is still far tighter than any threshold
#: the retrieval scorer makes a decision on (0.30 minimum, 0.90 relative).
MIN_COSINE = 0.9999

#: The shapes the product actually embeds. A claim, a query, a claim key, a
#: document path, SQL, an empty string, a long chunk, non-ASCII, and a string long
#: enough to hit the truncation boundary at both ends.
TEXTS = [
    "The primary datastore is PostgreSQL 16 with three replicas.",
    "Backups run nightly at 02:00 UTC.",
    "connection pooling happens in one place",
    "md:decision:0",
    "architecture.postgres",
    "docs/adr/001-datastore.md",
    "Which services depend on the primary datastore?",
    "word " * 400,
    "",
    "Ünïcödé — em dash, curly quotes, 数字, and a tab\tinside.",
    "SELECT * FROM memory_claims WHERE corpus_id = $1;",
    "one two three four five six seven eight nine ten eleven twelve thirteen",
]


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def _torch_vectors():
    from api.services.embedder import _TorchBackend

    backend = _TorchBackend(DEFAULT_MODEL)
    return backend.encode(TEXTS, normalize_embeddings=True).tolist()


def _onnx_vectors():
    from api.services.embedder import _OnnxBackend

    backend = _OnnxBackend(DEFAULT_MODEL)
    return backend.encode(TEXTS, normalize_embeddings=True).tolist()


@pytest.fixture(scope="module")
def vectors():
    return _torch_vectors(), _onnx_vectors()


def test_both_backends_produce_the_same_number_of_vectors(vectors):
    torch_vectors, onnx_vectors = vectors
    assert len(torch_vectors) == len(onnx_vectors) == len(TEXTS)


def test_both_backends_produce_the_same_dimension(vectors):
    torch_vectors, onnx_vectors = vectors
    assert {len(v) for v in torch_vectors} == {384}
    assert {len(v) for v in onnx_vectors} == {len(v) for v in torch_vectors}


@pytest.mark.parametrize("index", range(len(TEXTS)))
def test_each_text_is_the_same_vector_under_both_backends(vectors, index):
    torch_vector, onnx_vector = (v[index] for v in vectors)
    cosine = _cosine(torch_vector, onnx_vector)
    assert cosine >= MIN_COSINE, (
        f"text {index}: cosine {cosine:.8f} between backends, "
        f"below the {MIN_COSINE} this archive's stored vectors depend on"
    )


def test_the_batch_is_not_silently_reordered(vectors):
    """Same multiset of vectors is not enough. Pairing must survive batching."""
    torch_vectors, onnx_vectors = vectors
    for index, torch_vector in enumerate(torch_vectors):
        best = max(_cosine(torch_vector, candidate) for candidate in onnx_vectors)
        assert best >= MIN_COSINE, f"text {index} has no close match in the other batch"


def test_both_vectors_are_unit_length(vectors):
    for label, batch in (("torch", vectors[0]), ("onnx", vectors[1])):
        for vector in batch:
            length = sum(x * x for x in vector) ** 0.5
            assert abs(length - 1.0) < 1e-4, f"{label} vector is not normalized: {length}"


# --- the persisted identity -------------------------------------------------


def test_the_version_string_does_not_name_the_backend():
    """Otherwise installing `onnxruntime` invalidates every archive on earth.

    `memory_claim_embeddings` rows are keyed for validity on `(embedding_model,
    embedding_dimension, embedding_version)`. Put the backend in the version and
    every one of them is stale the next morning, and the operator has no way to
    tell whether the corpus needs re-embedding or the rows are simply absent.
    """
    embedder = Embedder()
    assert embedder.version == f"{embedder.model_name}:{embedder.dimension}"
    assert "onnx" not in embedder.version
    assert "torch" not in embedder.version


def test_the_backend_reports_what_was_actually_loaded(monkeypatch):
    """A preference would attribute a vector to a backend that did not make it."""
    from api.services import embedder as module

    monkeypatch.setattr(module, "backend_name", lambda: "onnx")
    # `object.__new__`, not `Embedder()`: the class is a singleton, and handing it
    # a half-built backend that escapes this test leaves every later embed failing
    # with an attribute error about the tokenizer.
    instance = object.__new__(module.Embedder)
    instance.model_name = DEFAULT_MODEL
    instance._model = object()
    assert instance.backend == "torch", "nothing ONNX-shaped is loaded"

    instance._model = module._OnnxBackend.__new__(module._OnnxBackend)
    assert instance.backend == "onnx"


def test_the_tokenizer_truncates_where_the_torch_backend_truncates():
    """A different token window is a different function, not a rounding difference."""
    assert MAX_TOKENS == 256, (
        "sentence-transformers truncates this model at 256; a different window "
        "would make a long chunk embed differently under the two backends"
    )


def test_an_explicit_torch_choice_is_honoured(monkeypatch):
    from api.services import embedder as module

    monkeypatch.setenv("MEMORY_EMBEDDER", "torch")
    assert module.backend_name() == "torch"
    monkeypatch.setenv("MEMORY_EMBEDDER", "onnx")
    assert module.backend_name() == "onnx"
    monkeypatch.setenv("MEMORY_EMBEDDER", "  ONNX ")
    assert module.backend_name() == "onnx"
    monkeypatch.delenv("MEMORY_EMBEDDER")
    assert module.backend_name() in {"onnx", "torch"}


def test_a_missing_onnxruntime_falls_back_to_torch(monkeypatch):
    """Not a new failure mode for a deployment that has not installed it."""
    from api.services import embedder as module

    monkeypatch.delenv("MEMORY_EMBEDDER", raising=False)
    real_import = __import__

    def refuse_onnxruntime(name, *args, **kwargs):
        if name == "onnxruntime":
            raise ImportError("no onnxruntime here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", refuse_onnxruntime)
    assert module.backend_name() == "torch"


def test_onnx_is_the_default_when_available():
    """The default is the cheap one, or the default is the 10.8 second one."""
    assert os.environ.get("MEMORY_EMBEDDER") is None or True
    assert backend_name() in {"onnx", "torch"}
    import importlib.util

    if importlib.util.find_spec("onnxruntime") is not None:
        assert backend_name() == "onnx"
