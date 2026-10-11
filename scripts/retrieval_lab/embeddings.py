"""Embedding, cached to disk so a re-run costs nothing.

Two models are used and they are both already on this machine:

* `all-MiniLM-L6-v2` -- 22.7 M parameters, the shipped embedder.
* `cross-encoder/ms-marco-MiniLM-L-6-v2` -- 22.7 M, the shipped reranker.

Both are small. That is the point of the round: the question is not "can a
bigger model do better" but "how far does a small one get, and where is the
ceiling". A result that needs a large model is reported as needing a large
model, not quietly re-run with one.

Vectors are cached by content hash, so the cache is invalidated by the text
changing rather than by a flag, and a cache written by one arm is reused by the
next. Normalisation is explicit rather than inherited from the model's defaults:
the shipped scorer computes a dot product and calls it a similarity, which is
only cosine if both sides are unit length. Making that visible here is what
makes arm C's numbers mean what they say.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".retrieval_lab_cache"

SHIPPED_EMBEDDER = "sentence-transformers/all-MiniLM-L6-v2"
SHIPPED_RERANKER = "cross-encoder/ms-marco-MiniLM-L-6-v2"
#: The "one stronger small model" arm G would use. Named here so its absence is
#: a recorded fact rather than a silent omission.
STRONGER_EMBEDDER = "BAAI/bge-small-en-v1.5"

_model_cache: dict[str, object] = {}


def load_model(name: str):
    if name not in _model_cache:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        _model_cache[name] = (
            CrossEncoder(name) if name.startswith("cross-encoder/") else SentenceTransformer(name)
        )
    return _model_cache[name]


def _key(model: str, texts: list[str]) -> Path:
    digest = hashlib.sha256(model.encode() + b"\0" + "\0".join(texts).encode()).hexdigest()[:20]
    return CACHE / f"{digest}.npy"


def embed(texts: list[str], model: str = SHIPPED_EMBEDDER) -> np.ndarray:
    """Unit-length embeddings, cached by (model, exact text)."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    path = _key(model, texts)
    if path.exists():
        return np.load(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    raw = load_model(model).encode(texts, normalize_embeddings=True, show_progress_bar=False)
    array = np.asarray(raw, dtype=np.float32)
    np.save(path, array)
    return array


def rerank(query: str, passages: list[str], model: str = SHIPPED_RERANKER) -> list[float]:
    """Cross-encoder relevance, one score per passage. Raw logits, not softmaxed.

    Softmax would make the scores of a five-passage batch sum to one, which
    couples every score to how many candidates were passed in and makes scores
    from different queries incomparable. The gate needs a comparable number.
    """
    if not passages:
        return []
    encoder = load_model(model)
    return [float(s) for s in encoder.predict([(query, p) for p in passages])]


def model_available(name: str) -> tuple[bool, str]:
    """Can this model actually be loaded here? Returns (ok, why not)."""
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"
    folder = "models--" + name.replace("/", "--")
    if not (cache / folder).exists():
        return False, f"not in the local HuggingFace cache ({folder}); HF_HUB_OFFLINE=1"
    try:
        load_model(name)
    except Exception as exc:  # noqa: BLE001 - the reason is the message
        return False, f"{type(exc).__name__}: {exc}"[:160]
    return True, "available"


def describe() -> dict:
    out = {}
    for name in (SHIPPED_EMBEDDER, SHIPPED_RERANKER, STRONGER_EMBEDDER):
        ok, why = model_available(name)
        out[name] = {"available": ok, "note": why}
    return out


def fingerprint() -> str:
    """Identifies the embedding setup a result was produced with."""
    payload = {
        SHIPPED_EMBEDDER: _fingerprint(SHIPPED_EMBEDDER),
        SHIPPED_RERANKER: _fingerprint(SHIPPED_RERANKER),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _fingerprint(name: str) -> str:
    folder = (
        Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface"))
        / "hub"
        / ("models--" + name.replace("/", "--"))
    )
    snapshots = sorted(folder.glob("snapshots/*"))
    if not snapshots:
        return "absent"
    digest = hashlib.sha256()
    for path in sorted(snapshots[0].rglob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()[:16]
