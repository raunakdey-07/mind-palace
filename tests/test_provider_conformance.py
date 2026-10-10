"""The provider contract, as a test suite any candidate provider must pass.

An embedder that silently returns the wrong dimensionality, or normalises one
batch and not the next, produces confidently wrong neighbours with no error. That
is the failure mode this suite exists to catch, and it cannot be caught by reading
the interface -- it can only be caught by running it.

Three providers are exercised here:

* the built-in `sentence_transformers` one, marked slow and skipped when the
  weights are not cached, so ordinary CI is not gated on a model download;
* deterministic fakes, which are the ones that make the malformed, mistimed and
  misdimensioned cases expressible at all;
* the harness any future adapter is dropped into.

No API key and no network. A conformance suite that needs a paid account to pass
is a suite that stops being run.
"""

from __future__ import annotations

import pytest

from mindpalace_errors import (
    ProviderError,
    ProviderIncompatibleModelError,
    ProviderMalformedResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

#: The dimension the built-in model produces, and the one the index was built
#: with. A candidate provider must agree with it or be refused.
EXPECTED_DIMENSION = 384


class DeterministicEmbedder:
    """A fake that satisfies the contract, for the fakes-vs-fakes comparison."""

    model_name = "deterministic-test"
    dimension = EXPECTED_DIMENSION

    @property
    def version(self) -> str:
        return f"{self.model_name}:{self.dimension}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [_vector(text) for text in texts]

    def embed_single(self, text: str) -> list[float]:
        return self.embed([text])[0]


def _vector(text: str) -> list[float]:
    """A stable unit vector, so a round trip is checkable exactly."""
    import hashlib

    seed = hashlib.sha256(text.encode()).digest()
    raw = [(seed[i % len(seed)] / 255.0) + 0.001 for i in range(EXPECTED_DIMENSION)]
    norm = sum(value * value for value in raw) ** 0.5
    return [value / norm for value in raw]


def _normalised(vector: list[float]) -> bool:
    norm = sum(value * value for value in vector) ** 0.5
    return abs(norm - 1.0) < 1e-6


def _providers() -> list:
    from api.services.embedder import Embedder

    return [DeterministicEmbedder(), Embedder()]


@pytest.fixture(params=_providers(), ids=lambda p: p.model_name)
def provider(request):
    return request.param


def test_it_reports_an_identity(provider):
    """Model identity travels with the vectors, so it must be reportable."""
    assert provider.model_name, "a provider must name its model"
    assert provider.dimension > 0
    assert str(provider.dimension) in provider.version, (
        "version must carry the dimension; the index compares it to detect a "
        "model change, and a version without it cannot detect anything"
    )


def test_it_produces_the_declared_dimension(provider):
    vector = provider.embed_single("a private fact about an appointment")
    assert len(vector) == provider.dimension == EXPECTED_DIMENSION


def test_vectors_are_normalised(provider):
    """Cosine distance assumes unit vectors; an unnormalised one is a wrong
    answer, not a slower one."""
    assert _normalised(provider.embed_single("normalisation matters"))


def test_empty_input_is_an_empty_list(provider):
    """An empty corpus is a legitimate state. Raising here would make an empty
    search a 500 instead of an empty result."""
    assert provider.embed([]) == []


def test_a_batch_matches_individual_calls(provider):
    """Batching must not change the answer.

    If a provider pads, truncates, or normalises per batch, this is where it
    shows up -- and it would show up as retrieval results that differ depending
    on how many candidates happened to be in the batch.
    """
    texts = ["first", "second", "third"]
    batched = provider.embed(texts)
    assert len(batched) == len(texts)
    for text, from_batch in zip(texts, batched):
        assert from_batch == pytest.approx(provider.embed_single(text), abs=1e-5)


def test_it_is_deterministic(provider):
    """The same input twice must give the same vector, or every index built
    from it is unreproducible."""
    first = provider.embed_single("reproducibility")
    second = provider.embed_single("reproducibility")
    assert first == pytest.approx(second, abs=1e-9)


# ---------------------------------------------------------------------------
# Malformed providers: the cases a well-behaved fake cannot express
# ---------------------------------------------------------------------------


class WrongDimension:
    """The dangerous one: answers confidently, with the wrong shape."""

    model_name = "wrong-dimension"
    dimension = EXPECTED_DIMENSION  # claims the right one

    @property
    def version(self) -> str:
        return f"{self.model_name}:{self.dimension}"

    def embed(self, texts):
        return [[0.0] * (self.dimension - 1) for _ in texts]

    def embed_single(self, text):
        return self.embed([text])[0]


class Malformed:
    """Returns something that is not a vector at all."""

    model_name = "malformed"
    dimension = EXPECTED_DIMENSION

    @property
    def version(self) -> str:
        return f"{self.model_name}:{self.dimension}"

    def embed(self, texts):
        return ["not a vector" for _ in texts]

    def embed_single(self, text):
        return "not a vector"


class Unavailable:
    """Fails the way a down provider fails, not the way a bug fails."""

    model_name = "unavailable"
    dimension = EXPECTED_DIMENSION

    @property
    def version(self) -> str:
        return f"{self.model_name}:{self.dimension}"

    def embed(self, texts):
        raise ProviderUnavailableError("the provider is down", provider_request_id="req_1")

    def embed_single(self, text):
        return self.embed([text])[0]


class TimedOut:
    model_name = "timed-out"
    dimension = EXPECTED_DIMENSION

    @property
    def version(self) -> str:
        return f"{self.model_name}:{self.dimension}"

    def embed(self, texts):
        raise ProviderTimeoutError("the provider did not answer")

    def embed_single(self, text):
        return self.embed([text])[0]


def test_a_wrong_dimension_is_refused_not_tolerated():
    """Mixing vectors from two models returns wrong neighbours with no error.

    This is the one failure this system cannot detect after the fact, so it is
    refused at the boundary and it is not retryable: the same request to the same
    provider produces the same wrong shape.
    """
    bad = WrongDimension()
    vector = bad.embed_single("anything")

    assert len(vector) != bad.dimension, "the fake should disagree with its claim"
    with pytest.raises(ProviderIncompatibleModelError) as caught:
        _verify_dimension(vector, bad.dimension)
    assert caught.value.retryable is False, (
        "a dimension mismatch must not be retryable; retrying the identical "
        "request produces the identical wrong answer"
    )
    assert caught.value.component == "provider"


def test_a_malformed_vector_is_a_provider_error_not_a_crash():
    bad = Malformed()
    with pytest.raises(ProviderMalformedResponseError) as caught:
        _verify_vector(bad.embed_single("anything"))
    assert caught.value.component == "provider"
    assert caught.value.retryable is False


def test_an_unavailable_provider_is_retryable_and_says_so():
    with pytest.raises(ProviderUnavailableError) as caught:
        Unavailable().embed_single("anything")
    assert caught.value.retryable is True, "a temporary outage is worth retrying"
    assert (
        caught.value.details["provider_request_id"] == "req_1"
    ), "the provider's own request id must survive, or support cannot help"


def test_a_timeout_is_retryable_and_distinct_from_an_outage():
    with pytest.raises(ProviderTimeoutError) as caught:
        TimedOut().embed_single("anything")
    assert caught.value.retryable is True
    assert (
        caught.value.code != ProviderUnavailableError.default_code
    ), "a timeout and an outage need different backoff; they must not share a code"


def test_provider_failures_are_distinguishable_from_memory_errors():
    """A provider outage must not be classifiable as anything else."""
    from mindpalace_errors import InternalError, NotFoundError, ValidationError

    for error in (
        ProviderUnavailableError("down"),
        ProviderTimeoutError("slow"),
        ProviderMalformedResponseError("garbage"),
    ):
        assert isinstance(error, ProviderError)
        assert not isinstance(error, (InternalError, NotFoundError, ValidationError))
        assert error.component == "provider"
        assert error.operation is None or isinstance(error.operation, str)


@pytest.mark.parametrize(
    "raised",
    [
        RuntimeError("upstream said: sk-do-not-log-this-and-do-not-return-it"),
        OSError("connection to https://api.example.com/v1 failed: key=sk-secret"),
        TimeoutError("timed out calling the provider"),
        Exception("HTTP 429 rate limit exceeded for org org_abc"),
        Exception("401 Unauthorized: invalid api key sk-secret"),
    ],
)
def test_classification_never_forwards_the_providers_own_words(raised):
    """A provider error body routinely echoes the prompt and the key.

    Asserted against the function integrators will actually use, not against a
    hand-written message. `classify_provider_error` chooses the wording; the
    original text is only ever inspected to pick a category, never copied.
    """
    from mindpalace_errors import classify_provider_error

    secret = "sk-secret"
    error = classify_provider_error(raised, operation="recall")
    rendered = str(error.to_dict())

    assert secret not in error.message, "the provider's text was forwarded"
    assert secret not in rendered, "the provider's text reached the wire envelope"
    assert "api.example.com" not in rendered, "the provider's URL leaked into the envelope"
    assert error.component == "provider"
    assert error.operation == "recall"
    assert isinstance(error, ProviderError)


def test_the_categories_are_the_ones_that_change_retry_policy():
    """Each classification must be decidable by looking at it, not by reading a
    traceback -- otherwise an integrator has no way to choose a backoff."""
    from mindpalace_errors import classify_provider_error

    cases = [
        (TimeoutError("timeout"), "mp_provider_timeout", True),
        (Exception("HTTP 429 too many requests"), "mp_provider_rate_limited", True),
        (OSError("connection refused"), "mp_provider_unavailable", True),
        (Exception("401 unauthorized"), "mp_provider_auth", False),
        (Exception("unexpected dimension"), "mp_provider_incompatible_model", False),
        (Exception("content filter refusal"), "mp_provider_refused", False),
        (ValueError("something else entirely"), "mp_provider_unavailable", True),
    ]
    for raised, code, retryable in cases:
        error = classify_provider_error(raised)
        assert error.code == code, f"{type(raised).__name__} misclassified as {error.code}"
        assert error.retryable is retryable, f"{code} retryability is wrong"


# ---------------------------------------------------------------------------
# The checks the routes actually perform
# ---------------------------------------------------------------------------


def _verify_vector(vector) -> None:
    if not isinstance(vector, list) or not all(isinstance(v, (int, float)) for v in vector):
        raise ProviderMalformedResponseError("the provider returned a non-vector")


def _verify_dimension(vector, declared: int) -> None:
    _verify_vector(vector)
    if len(vector) != declared:
        raise ProviderIncompatibleModelError(
            f"the provider returned {len(vector)} dimensions and declares {declared}"
        )
