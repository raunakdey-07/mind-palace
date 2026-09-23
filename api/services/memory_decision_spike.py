"""Offline decision-layer boundary for the M007 research spike.

Decision results are routing observations only. They never carry or infer
memory authority, validity, supersession, conflict, or evidence semantics.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class DecisionResult:
    value: Any
    confidence: float
    abstained: bool = False

    def __post_init__(self):
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Decision confidence must be finite and in [0,1]")


class DecisionProvider(Protocol):
    """Typed decision boundary; implementations must not mutate memory."""

    def choice(self, question: str, options: Sequence[str], state: dict) -> DecisionResult: ...

    def score(self, question: str, candidate: dict, state: dict) -> DecisionResult: ...

    def probability(self, question: str, state: dict) -> DecisionResult: ...


class DecisionProviderError(RuntimeError):
    """An external provider failed without producing a decision."""


def _result(value: Any) -> DecisionResult:
    if isinstance(value, DecisionResult):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("Provider response must be a mapping or DecisionResult")
    if "confidence" not in value:
        raise ValueError("Provider response is missing confidence")
    decision = value.get("value", value.get("decision"))
    return DecisionResult(
        decision,
        float(value["confidence"]),
        abstained=bool(value.get("abstained", value.get("decision") == "abstain")),
    )


class DecisionTransport(Protocol):
    def request(self, payload: dict) -> Mapping[str, Any]: ...


def canonical_request_key(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class ReplayDecisionTransport:
    """Offline transport keyed by a canonical request fingerprint."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]]):
        self.responses = dict(responses)
        self.requests: list[dict] = []

    def request(self, payload: dict) -> Mapping[str, Any]:
        self.requests.append(payload)
        key = canonical_request_key(payload)
        try:
            return self.responses[key]
        except KeyError as exc:
            raise DecisionProviderError("No replay response for provider request") from exc


class DeterministicDecisionProvider:
    """Offline provider for repeatable routing experiments.

    It uses only numeric features supplied by the caller. Corpus text remains
    data: it is never interpreted as instructions.
    """

    def __init__(
        self,
        *,
        abstention_threshold: float = 0.30,
    ):
        if not 0 <= abstention_threshold <= 1:
            raise ValueError("Invalid abstention threshold")
        self.abstention_threshold = abstention_threshold

    def choice(self, question, options, state):
        if not options:
            return DecisionResult(None, 1.0, abstained=True)
        ranked = sorted(
            (
                (
                    float(state.get("scores", {}).get(option, 0)),
                    float(state.get("overlap", {}).get(option, 0)),
                    option,
                )
                for option in options
            ),
            reverse=True,
        )
        best = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        confidence = max(0.0, min(1.0, best[0] * 0.7 + (best[0] - second) * 0.3))
        return DecisionResult(
            best[2],
            confidence,
            abstained=best[0] < self.abstention_threshold,
        )

    def score(self, question, candidate, state):
        relevance = float(candidate.get("relevance", 0))
        overlap = float(candidate.get("lexical_overlap", 0))
        score = max(0.0, min(1.0, 0.7 * relevance + 0.3 * overlap))
        return DecisionResult(score, score, abstained=score < self.abstention_threshold)

    def probability(self, question, state):
        score = float(state.get("top_score", 0))
        margin = float(state.get("margin", 0))
        probability = max(0.0, min(1.0, 0.7 * score + 0.3 * margin))
        return DecisionResult(
            probability,
            probability,
            abstained=probability < self.abstention_threshold,
        )


class JevDecisionProvider:
    """Optional adapter around an injected Jev transport, without a dependency.

    ``client`` is retained for compatibility with the original spike. A
    transport is preferred because it makes request construction and replay
    explicit without inventing a live Jev SDK schema.
    """

    def __init__(
        self,
        client=None,
        *,
        transport: DecisionTransport | None = None,
        provider_version: str = "unverified",
        request_schema_version: str = "jev-decision-v1",
    ):
        if client is None and transport is None:
            raise ValueError("A client or decision transport is required")
        if client is not None and transport is not None:
            raise ValueError("Provide either client or transport, not both")
        self.client = client
        self.transport = transport
        self.provider_version = provider_version
        self.request_schema_version = request_schema_version

    def _call(self, operation, **kwargs):
        if self.client is not None:
            return _result(getattr(self.client, operation)(**kwargs))
        payload = {
            "schema_version": self.request_schema_version,
            "operation": operation,
            **kwargs,
        }
        try:
            return _result(self.transport.request(payload))
        except (TimeoutError, OSError) as exc:
            raise DecisionProviderError("Decision provider transport failed") from exc

    def choice(self, question, options, state):
        return self._call("choice", question=question, options=list(options), state=state)

    def score(self, question, candidate, state):
        return self._call("score", question=question, candidate=candidate, state=state)

    def probability(self, question, state):
        return self._call("probability", question=question, state=state)


@dataclass(frozen=True)
class DecisionTrace:
    """Experimental decision provenance, separate from memory provenance."""

    question: str
    candidate_id: str | None
    decision: str
    confidence: float
    provider: str
    provider_version: str
    request_schema_version: str
    policy_version: str
    candidate_features: dict
    authority_resolution: dict | None = None
    evidence: tuple[str, ...] = ()
    timestamp: float | None = None

    def __post_init__(self):
        if not 0 <= self.confidence <= 1 or not math.isfinite(self.confidence):
            raise ValueError("Trace confidence must be finite and in [0,1]")

    def as_dict(self, *, include_timestamp: bool = False) -> dict:
        result = {
            "question": self.question,
            "candidate": self.candidate_id,
            "decision": self.decision,
            "confidence": self.confidence,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "request_schema_version": self.request_schema_version,
            "policy_version": self.policy_version,
            "candidate_features": self.candidate_features,
            "authority_resolution": self.authority_resolution,
            "evidence": list(self.evidence),
        }
        if include_timestamp:
            result["timestamp"] = self.timestamp
        return result


def timed_decision(provider: DecisionProvider, question, candidates, *, state=None):
    """Return a provider result and wall-clock latency for experiments."""

    started = time.perf_counter()
    trace = bounded_decision(provider, question, candidates, state=state)
    return trace, (time.perf_counter() - started) * 1000


def bounded_decision(
    provider: DecisionProvider,
    question: str,
    candidates: Sequence[dict],
    *,
    state: dict | None = None,
) -> dict:
    """Record a routing decision over a bounded candidate set.

    The return value is a trace, not a Memory claim and must not be persisted as
    authored memory. Candidate IDs are caller-owned opaque identifiers.
    """

    state = dict(state or {})
    options = [str(candidate["id"]) for candidate in candidates]
    scores = {
        option: float(candidate.get("relevance", 0))
        for option, candidate in zip(options, candidates)
    }
    overlaps = {
        option: float(candidate.get("lexical_overlap", 0))
        for option, candidate in zip(options, candidates)
    }
    state.update(scores=scores, overlap=overlaps)
    choice = provider.choice(question, options, state)
    abstention = provider.probability(
        "Is there enough evidence to retrieve a candidate?",
        {
            "top_score": max(scores.values(), default=0),
            "margin": (
                max(scores.values()) - sorted(scores.values())[-2]
                if len(scores) > 1
                else max(scores.values(), default=0)
            ),
        },
    )
    return {
        "question": question,
        "candidate_ids": options,
        "selected_id": None if choice.abstained else choice.value,
        "choice_confidence": choice.confidence,
        "abstain_probability": abstention.value,
        "abstained": choice.abstained or abstention.abstained,
        "decision_count": 2,
    }
