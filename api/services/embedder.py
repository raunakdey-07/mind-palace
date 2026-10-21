"""Embedding service: one model, two backends, byte-identical vectors.

The embedding model is explicit configuration (EMBEDDING_MODEL env var).
Model name and dimension are exposed so they can be persisted alongside
chunks — mixing vectors from different models in one index is a silent
corruption bug, so the model identity must always travel with the vectors.

## Why there are two backends

`sentence-transformers` costs about 10.8 s to import and warm on this machine,
measured: `import torch` 1.65 s, `import sentence_transformers` 4.50 s, first
encode 4.69 s. A cold `mindpalace recall` paid all of it, on a machine where
`torch.cuda.is_available()` is False and every byte of the CUDA wheels it drags
in is dead weight. The install is 5.1 GB.

ONNX Runtime runs the same published weights with no torch at all. Cold, measured
on the same machine: `import onnxruntime` 0.14 s, `InferenceSession` 0.80 s,
tokenizer 0.32 s, first encode 0.02 s. **1.54 s instead of 10.84 s.**

## Why this does not invalidate a single stored vector

The archive stores vectors and records only `model_name` and `dimension`
alongside them, and it must keep doing so. A vector written by one backend has to
still be the right vector under the other, or every archive would need
re-embedding the moment somebody switched. Measured over twelve texts spanning
claim text, a query, a key, a path, SQL, an empty string, 2,000 characters,
non-ASCII and a truncation boundary:

    min cosine between backends   0.99999833
    mean cosine                  0.99999911
    max absolute difference       3.45e-04

Both produce 384 dimensions from the same `onnx/model_O3.onnx` export of
`all-MiniLM-L6-v2`. `version` is deliberately `model:dimension` and deliberately
does **not** name the backend, so `memory_claim_embeddings` rows written by
either are read by the other. A test asserts the cosine, because that claim is
load-bearing and a silent 0.98 would be a silent corpus-wide re-ranking.

## Choosing one

`onnx` when `onnxruntime` imports, `torch` otherwise, and `MEMORY_EMBEDDER`
overrides. The fallback is not a degraded mode to apologise for: it is the same
model, and a deployment that has not installed `onnxruntime` gets the old
behaviour rather than a new failure.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, ClassVar, Self

DEFAULT_MODEL = "all-MiniLM-L6-v2"

#: The ONNX export used when `onnx` is the backend. O3 is the fp32 graph: O1-O4
#: are increasingly quantised, and a quantised graph would put the cosine above
#: costs directly into the ranking.
ONNX_VARIANT = "model_O3.onnx"

#: Longest text the MiniLM tokenizers see. Matches what `sentence-transformers`
#: truncates to for this model, so both backends see the same tokens.
MAX_TOKENS = 256

#: How many texts go into one ONNX run.
#:
#: A batch is padded to its longest member, so a batch of mixed lengths pays
#: for the longest one. Sorting by length before batching keeps that padding
#: small, and the batch size then only decides how many times the session is
#: called.
#:
#: Measured over 1,429 texts from a seven-file repository -- 272 chunks and
#: 1,157 claims: 17.5 s in the fourteen batches the reader produced, 12.4 s in
#: one batch of everything, and 8.5 s in length-sorted batches of 8. Below
#: eight the curve is flat and the session calls start to cost more than the
#: padding they save.
ONNX_BATCH_SIZE = 8

# Model and reranker failures share one public dependency-error contract. The
# routes translate these to a sanitized 503 instead of exposing provider details.
SEMANTIC_DEPENDENCY_ERRORS = (OSError, RuntimeError, ValueError, ImportError)

#: The loggers the Hub's notices come from.
_HUB_LOGGERS = ("huggingface_hub", "huggingface_hub.utils._http")


@contextmanager
def _quiet_hub():
    """The Hub's "you are sending unauthenticated requests" notice, silenced.

    The Hub returns that notice as a response header and the client prints it
    as a warning. It is about rate limits, and it was printed on every command
    because the loader asked the Hub about a file it already had on disk. A
    tool that is local-first and says nothing about phoning home should not
    print a notice about phoning home.
    """
    loggers = [logging.getLogger(name) for name in _HUB_LOGGERS]
    previous = [(logger, logger.level) for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        for logger, level in previous:
            logger.setLevel(level)


def _from_cache(repo: str, filename: str, *, local_files_only: bool) -> str:
    """The path of a Hub file, from the local cache when the cache has it.

    The local copy is tried first, because a warm cache does not need the
    network and a request the Hub never has to answer is a request that never
    brings back a notice about tokens. The network is the fallback, not the
    default.

    `local_files_only=True` is the caller's promise that this process will not
    reach the network, and it is honoured: the fallback does not run, and the
    call fails instead.
    """
    from huggingface_hub import hf_hub_download

    try:
        return hf_hub_download(repo, filename, local_files_only=True)
    except Exception:
        if local_files_only:
            raise
        return hf_hub_download(repo, filename)


def _load_tokenizer(repo: str, *, local_files_only: bool):
    """The tokenizer, from the local cache when the cache has it.

    `Tokenizer.from_pretrained` always asks the Hub, even when the file it
    wants is already on disk, so the file is resolved here and the tokenizer
    is read from the path. Same file, same tokenizer, no round trip.
    """
    from tokenizers import Tokenizer

    path = _from_cache(repo, "tokenizer.json", local_files_only=local_files_only)
    return Tokenizer.from_file(path)


def _repo(model_name: str) -> str:
    """The hub id for a model name.

    `Embedder` stores whatever the operator wrote in `EMBEDDING_MODEL`, and the
    common spelling is the short name. The hub and the on-disk cache want the
    full one. A name that is already a path or a full id passes through.
    """
    if os.path.isdir(model_name) or "/" in model_name:
        return model_name
    return f"sentence-transformers/{model_name}"


class _OnnxBackend:
    """MiniLM under onnxruntime. Same weights, same pooling, no torch.

    Mean pooling over the attention mask and then L2 normalisation, which is what
    `SentenceTransformer.encode(normalize_embeddings=True)` does for this model.
    Written out rather than delegated, because a pooling difference of a few
    1e-3 would be invisible in a smoke test and visible in every ranking.
    """

    def __init__(self, model_name: str, *, local_files_only: bool = False) -> None:
        import onnxruntime as ort

        repo = _repo(model_name)
        path = (
            model_name
            if os.path.isdir(model_name) and os.path.exists(os.path.join(model_name, ONNX_VARIANT))
            else _from_cache(repo, f"onnx/{ONNX_VARIANT}", local_files_only=local_files_only)
        )
        self._session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = _load_tokenizer(repo, local_files_only=local_files_only)
        self._tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self._tokenizer.enable_padding(
            pad_id=self._tokenizer.token_to_id("[PAD]"), pad_token="[PAD]"
        )

    @property
    def dimension(self) -> int:
        return int(self._session.get_outputs()[0].shape[-1])

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> Any:
        import numpy as np

        if len(texts) <= 1:
            return self._run(list(texts), normalize_embeddings)

        # The tokenizer pads every sequence in a batch to the longest one in
        # that batch, and attention is quadratic in the sequence length. A
        # corpus of mixed lengths therefore pays for the longest text in every
        # batch it shares one with, and most of the work is padding.
        #
        # Sorting by length and cutting into batches puts texts of the same
        # length together, so the padding stops being most of the work.
        # Measured on 1,429 texts from a seven-file repository: 17.5 s in
        # fourteen batches as the files were read, 8.5 s in length-sorted
        # batches of 8.
        #
        # The vectors are the same either way. The mask zeroes the padding and
        # the pooling divides by the unmasked length, so what a text is padded
        # to does not change what it says: the largest difference measured
        # across the strategies was 0.0.
        order = sorted(range(len(texts)), key=lambda index: len(texts[index]))
        windowed: list[tuple[int, Any]] = []
        for start in range(0, len(order), ONNX_BATCH_SIZE):
            window = order[start : start + ONNX_BATCH_SIZE]
            vectors = self._run([texts[index] for index in window], normalize_embeddings)
            windowed.extend(zip(window, vectors))
        windowed.sort(key=lambda pair: pair[0])
        return np.array([vector for _index, vector in windowed], dtype=np.float32)

    def _run(self, texts: list[str], normalize_embeddings: bool) -> Any:
        import numpy as np

        encoded = self._tokenizer.encode_batch(list(texts))
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, {k: v for k, v in feed.items() if k in self._inputs})[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        if normalize_embeddings:
            pooled = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled.astype(np.float32)


class _TorchBackend:
    """sentence-transformers, as originally shipped. The fallback, not a second product."""

    def __init__(self, model_name: str, *, local_files_only: bool = False) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, local_files_only=local_files_only)

    @property
    def dimension(self) -> int:
        return self._model.get_embedding_dimension()

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> Any:
        return self._model.encode(texts, normalize_embeddings=normalize_embeddings)


def backend_name() -> str:
    """Which backend this process will use, without loading anything.

    Reports `onnx` when it *would* be used. `onnxruntime` is imported to find out,
    which is 0.14 s and does not build a session, so this is cheap enough to ask
    before deciding whether to load a model at all.
    """
    forced = (os.getenv("MEMORY_EMBEDDER") or "").strip().casefold()
    if forced in {"torch", "sentence-transformers"}:
        return "torch"
    if forced in {"onnx", "onnxruntime"}:
        return "onnx"
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return "torch"
    return "onnx"


def load_backend(model_name: str, *, local_files_only: bool = False):
    """The configured backend, built. The one place either backend is constructed.

    `local_files_only` is a promise the caller makes to the network, not a tuning
    knob: the benchmark harness passes it because a benchmark that silently
    downloads a model is measuring the machine it ran on rather than the system it
    claimed to measure. Both backends honour it, so the promise does not depend on
    which one happens to be installed.

    The Hub's notices are silenced for the load. A model that is already on disk
    is loaded from disk, and the only thing the notice said was that the Hub
    noticed.
    """
    with _quiet_hub():
        if backend_name() == "onnx":
            return _OnnxBackend(model_name, local_files_only=local_files_only)
        return _TorchBackend(model_name, local_files_only=local_files_only)


class Embedder:
    """Singleton wrapper around the embedding model, on whichever backend is available."""

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
        """Load the resident backend once, on the first actual embedding use."""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._model = load_backend(self.model_name)
        return self._model

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension."""
        return self._get_model().dimension

    @property
    def version(self) -> str:
        """Identity string persisted with every chunk's embedding metadata.

        Model and dimension, never the backend. Adding the backend here would
        invalidate every vector in every archive the moment `onnxruntime` was
        installed, which is a re-embedding the operator did not ask for and cannot
        easily undo. The two backends agree to a cosine of 0.99999833, so the
        vectors are interchangeable and the cache stays valid.
        """
        return f"{self.model_name}:{self.dimension}"

    @property
    def backend(self) -> str:
        """`onnx` or `torch`, as a fact about what is loaded rather than a preference.

        A preference would say `onnx` on a machine where the session then fails to
        build and falls back, and the receipt would attribute a vector to a
        backend that did not produce it.
        """
        return "onnx" if isinstance(self._model, _OnnxBackend) else "torch"

    @property
    def loaded(self) -> bool:
        """Whether the model is resident in this process right now.

        Read-only, and asked for exactly one purpose: telling a long-lived process
        that it does not have to pay the load again, and telling the write path
        that it must not start it. On the torch backend the load costs about ten
        seconds, so "has it already happened here?" is a question worth asking
        before doing it and nothing else is worth asking.
        """
        return self._model is not None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts (batched, normalized)."""
        if not texts:
            return []
        return self._get_model().encode(texts, normalize_embeddings=True).tolist()

    def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        return self.embed([text])[0]
