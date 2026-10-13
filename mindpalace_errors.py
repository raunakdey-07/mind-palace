# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The error taxonomy: one vocabulary for every failure Mind Palace produces.

A memory subsystem underneath somebody else's product has one job in the failure
path that matters more than in the success path: let the integrator tell, in one
line, whether the problem is theirs to fix, ours, or a provider's. Before this
module, a caller saw `HTTPException`, `ValueError`, or a provider's `RuntimeError`
depending on where the failure happened, and telling them apart meant reading this
repository.

The taxonomy answers three questions with three fields:

* **component** -- *where* it broke: `memory`, `auth`, `database`, `transport`,
  `provider`, or `mindpalace`.
* **code** -- *what* broke, stably and machine-readably.
* **retryable** -- whether retrying the identical request could plausibly work.

Everything else in the envelope is for the human.

Three properties this deliberately does not have:

* **No secrets, no memory content, no SQL.** `message` is written for a log line
  a stranger may hold for a year. The original exception is attached as
  ``__cause__`` for debugging and never rendered.
* **No provider detail passed through.** A provider's own error text can contain
  the prompt, the key, or the request body. It becomes a *category*, and its
  request id is kept because that is what a provider's support needs.
* **No flat hierarchy.** `MemoryClientError` from the existing SDK stays exactly
  where it is in the tree, so `except MemoryClientError` in somebody's code keeps
  working.

This module has no dependencies beyond the standard library, because the SDK, the
CLI and the MCP server all import it and none of them may require the server.
"""

from __future__ import annotations

import uuid
from typing import Any

__all__ = [
    "COMPONENTS",
    "AuthenticationError",
    "ConflictError",
    "DatabaseSchemaError",
    "DatabaseUnavailableError",
    "InternalError",
    "MindPalaceError",
    "NotFoundError",
    "ProviderAuthenticationError",
    "ProviderError",
    "ProviderIncompatibleModelError",
    "ProviderMalformedResponseError",
    "ProviderRateLimitError",
    "ProviderRefusedError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RequestId",
    "TransportError",
    "UnauthorizedError",
    "ValidationError",
    "classify_provider_error",
    "envelope",
    "new_request_id",
]


#: The components a failure can come from. Deliberately small and closed: a
#: component list that grows with every new dependency is a list nobody reads.
#:
#: ``provider`` covers the internal embedding and reranking models *and* any
#: external generation model. Both are "a model provider we called". Keeping them
#: under one component is honest -- Mind Palace cannot always tell which is which,
#: and pretending otherwise would only move the ambiguity.
COMPONENTS = ("memory", "auth", "database", "transport", "provider", "mindpalace")


def new_request_id() -> str:
    """A fresh correlation id, when none arrived with the request."""
    return uuid.uuid4().hex[:16]


class _RequestId:
    """The request id for the current unit of work, however it is being carried.

    A `ContextVar` rather than a global, so concurrent requests in one process do
    not overwrite each other's id -- which is the entire point of having one. Set
    by the HTTP middleware, by the runtime socket, and by the CLI.
    """

    def __init__(self) -> None:
        import contextvars

        self._var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "mindpalace_request_id", default=None
        )

    def get(self) -> str | None:
        return self._var.get()

    def set(self, value: str) -> str:
        self._var.set(value)
        return value

    def ensure(self) -> str:
        """Return the current id, inventing one if nothing set it."""
        return self._var.get() or self.set(new_request_id())

    def clear(self) -> None:
        self._var.set(None)


#: The process-wide handle. A singleton rather than a module global so that
#: importing this module twice cannot produce two disagreeing registries.
RequestId = _RequestId()


class MindPalaceError(Exception):
    """Base class for every failure Mind Palace raises on purpose.

    Subclasses set `default_code` and `default_component`; instances may override
    either, because "the same shape of problem in a different component" is common
    and a caller switching on `code` should not need a class per context.

    ``retryable`` defaults to ``False`` and is only ever set ``True`` where a
    retry of the identical request can succeed: a timeout, a rate limit, a
    provider that is temporarily down, a database that was briefly unreachable.
    A validation error is never retryable, and neither is an internal defect --
    retrying a bug just produces the same bug.
    """

    default_code = "mp_error"
    default_component = "mindpalace"
    default_retryable = False
    #: The HTTP status this maps to when the failure reaches the API. Chosen so a
    #: proxy or a load balancer makes the same decision Mind Palace does.
    default_status = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        component: str | None = None,
        operation: str | None = None,
        retryable: bool | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.component = component or self.default_component
        self.operation = operation
        self.retryable = self.default_retryable if retryable is None else retryable
        self.request_id = request_id or RequestId.ensure()
        self.details = _safe_details(details)
        self.status_code = status_code or self.default_status

    def to_dict(self) -> dict[str, Any]:
        """The wire envelope. Every public surface serializes exactly this."""
        body: dict[str, Any] = {
            "code": self.code,
            "component": self.component,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
        }
        if self.operation:
            body["operation"] = self.operation
        if self.details:
            body["details"] = self.details
        return body

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, component={self.component!r}, "
            f"operation={self.operation!r})"
        )


# ---------------------------------------------------------------------------
# memory -- the caller's request or Mind Palace's semantics
# ---------------------------------------------------------------------------


class ValidationError(MindPalaceError):
    """The request does not satisfy the contract. The caller's to fix."""

    default_code = "mp_invalid_request"
    default_component = "memory"
    default_status = 422


class NotFoundError(MindPalaceError):
    """The resource is not visible to this caller.

    Also the answer for "exists but is not yours". A caller must not be able to
    distinguish the two by timing or by status, so nothing in this path may add
    detail that the caller did not already supply.
    """

    default_code = "mp_not_found"
    default_component = "memory"
    default_status = 404


class ConflictError(MindPalaceError):
    """The request contradicts current state: already exists, stale version."""

    default_code = "mp_conflict"
    default_component = "memory"
    default_status = 409


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


class AuthenticationError(MindPalaceError):
    """No credential, or one that cannot be resolved.

    Never says which part of the credential was wrong. Doing so tells an attacker
    whether a name exists.
    """

    default_code = "mp_unauthenticated"
    default_component = "auth"
    default_status = 401


class UnauthorizedError(MindPalaceError):
    """Authenticated, and not allowed to do this.

    Raised only for a resource the caller has already proved it may name. A
    corpus outside the caller's scope is `NotFoundError` instead, so a scoped-out
    resource never becomes visible as a distinguishable status.
    """

    default_code = "mp_forbidden"
    default_component = "auth"
    default_status = 403


# ---------------------------------------------------------------------------
# database -- PostgreSQL specifically, never conflated with a provider
# ---------------------------------------------------------------------------


class DatabaseUnavailableError(MindPalaceError):
    """PostgreSQL could not be reached, or a connection failed.

    Retryable: the same request against a recovered database is a reasonable
    thing for a caller to do.
    """

    default_code = "mp_database_unavailable"
    default_component = "database"
    default_retryable = True
    default_status = 503


class DatabaseSchemaError(MindPalaceError):
    """PostgreSQL answered, and the schema is not what this version expects.

    Not retryable. A migration either has run or has not, and retrying produces
    the same answer until someone runs it.
    """

    default_code = "mp_database_schema"
    default_component = "database"
    default_status = 503


# ---------------------------------------------------------------------------
# transport -- the local runtime socket and MCP, not the network
# ---------------------------------------------------------------------------


class TransportError(MindPalaceError):
    """The local runtime or MCP transport failed.

    Named `transport` rather than folded into `provider`, because it means "the
    thing that would carry the request is not there", which is a different
    operational problem from "a model answered badly".
    """

    default_code = "mp_transport_error"
    default_component = "transport"
    default_retryable = True
    default_status = 503


# ---------------------------------------------------------------------------
# provider -- embedding, reranking, and external generation models
# ---------------------------------------------------------------------------


class ProviderError(MindPalaceError):
    """A model provider failed.

    Carries the provider's own request id when it gave one, because that is what
    its support desk asks for, and deliberately does not carry its message: a
    provider error body routinely echoes the prompt, the key, or the request.
    """

    default_code = "mp_provider_error"
    default_component = "provider"
    default_status = 503

    def __init__(self, message: str, *, provider_request_id: str | None = None, **kwargs: Any):
        details = dict(kwargs.pop("details", None) or {})
        if provider_request_id:
            details["provider_request_id"] = provider_request_id
        super().__init__(message, details=details or None, **kwargs)


class ProviderAuthenticationError(ProviderError):
    """The provider rejected our credentials or configuration.

    Not retryable: retrying with the same key produces the same refusal, and a
    caller who treats it as transient will hammer a provider that is correctly
    refusing them.
    """

    default_code = "mp_provider_auth"
    default_status = 502


class ProviderTimeoutError(ProviderError):
    """The provider did not answer in time. Retryable, with the caller's own backoff."""

    default_code = "mp_provider_timeout"
    default_retryable = True
    default_status = 504


class ProviderRateLimitError(ProviderError):
    """The provider is rate-limiting us. Retryable, after a delay it usually names."""

    default_code = "mp_provider_rate_limited"
    default_retryable = True
    default_status = 429


class ProviderUnavailableError(ProviderError):
    """The provider is temporarily down. Retryable."""

    default_code = "mp_provider_unavailable"
    default_retryable = True
    default_status = 503


class ProviderMalformedResponseError(ProviderError):
    """The provider answered with something that is not what it promised.

    Not retryable in the ordinary sense: the same request to the same provider
    usually produces the same malformed answer. It is flagged separately so a bug
    here is never mistaken for an outage, and never reported as "no results" --
    returning empty memory because an embedding call failed would be a lie about
    what the store contains.
    """

    default_code = "mp_provider_malformed_response"
    default_status = 502


class ProviderIncompatibleModelError(ProviderError):
    """The provider's model identity or dimensionality does not match the index.

    Not retryable, and never silently tolerated. Mixing vectors from two models in
    one index returns confidently wrong results, which is the worst failure this
    system has, so it is refused rather than degraded.
    """

    default_code = "mp_provider_incompatible_model"
    default_status = 503


class ProviderRefusedError(ProviderError):
    """The provider understood the request and declined it."""

    default_code = "mp_provider_refused"
    default_status = 502


# ---------------------------------------------------------------------------
# mindpalace -- our own defects
# ---------------------------------------------------------------------------


class InternalError(MindPalaceError):
    """A defect in Mind Palace.

    Not retryable, and deliberately not dressed as a provider outage. A caller
    needs to know that retrying will not help and that escalating is the right
    move.
    """

    default_code = "mp_internal_error"
    default_status = 500


#: Status code -> class, so a response can be turned back into the type the SDK
#: raises. The inverse of the mapping in `api/errors.py`.
BY_STATUS: dict[int, type[MindPalaceError]] = {
    401: AuthenticationError,
    403: UnauthorizedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: ProviderRateLimitError,
    500: InternalError,
    502: ProviderAuthenticationError,
    503: DatabaseUnavailableError,
    504: ProviderTimeoutError,
}


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Keep diagnostic details, drop anything unbounded or sensitive.

    Two rules, both because this dict ends up in a log and sometimes in an HTTP
    body: a value must be a scalar or a short list of scalars, and it must be
    short. Anything else is dropped rather than truncated, because half a number
    is worse than no number.
    """
    if not details:
        return {}
    kept: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            kept[str(key)] = value if not isinstance(value, str) or len(value) <= 200 else None
        elif isinstance(value, (list, tuple)) and len(value) <= 10:
            if all(isinstance(item, (str, int, float, bool)) for item in value):
                kept[str(key)] = [
                    item if not isinstance(item, str) or len(item) <= 100 else None
                    for item in value
                ]
    return kept


def classify_provider_error(
    exc: BaseException,
    *,
    operation: str | None = None,
    provider_request_id: str | None = None,
) -> ProviderError:
    """Turn any provider exception into a typed, **redacted** provider error.

    This is the function that makes "do not forward the provider's message"
    structural rather than a rule everyone has to remember. The message it
    produces is written here and depends only on the *category*, never on
    `str(exc)` -- a provider's error body routinely echoes the prompt, the API
    key, or the whole request, and passing it through is how that ends up in a
    log aggregator.

    The provider's own request id is kept, because that is what its support desk
    asks for and it identifies a request rather than revealing one.

    Categories are chosen so retry policy is decidable without reading a
    traceback: timeouts, rate limits and outages are retryable; a refused
    credential, a malformed answer and a shape mismatch are not.
    """
    detail = ""
    text = str(exc).lower()
    # Availability first. "Connection refused" is a TCP-level phrase and contains
    # "refus", so a naive substring rule for a provider *refusal* classifies every
    # unreachable provider as a deliberate decline -- which would mark a retryable
    # outage as non-retryable. Match the refusal vocabulary precisely instead.
    if isinstance(exc, (OSError, ConnectionError)) and "refus" in text:
        kind: type[ProviderError] = ProviderUnavailableError
        detail = "the provider could not be reached"
    elif "rate limit" in text or "429" in text or "too many requests" in text:
        kind = ProviderRateLimitError
        detail = "the provider rate-limited this request"
    elif "401" in text or "403" in text or "unauthorized" in text or "api key" in text:
        kind = ProviderAuthenticationError
        detail = "the provider rejected this deployment's credentials or configuration"
    elif "dimension" in text:
        kind = ProviderIncompatibleModelError
        detail = "the provider's model metadata does not match this index"
    elif "malformed" in text or "invalid json" in text or "decode" in text:
        kind = ProviderMalformedResponseError
        detail = "the provider returned a response this service could not use"
    elif (
        "content filter" in text
        or "safety" in text
        or "declined" in text
        or "policy violation" in text
    ):
        kind = ProviderRefusedError
        detail = "the provider declined this request"
    elif isinstance(exc, (TimeoutError, asyncio_timeout_types())) or "timeout" in text:
        kind = ProviderTimeoutError
        detail = "the provider did not answer in time"
    elif isinstance(exc, (OSError, ConnectionError)):
        kind = ProviderUnavailableError
        detail = "the provider could not be reached"
    else:
        kind = ProviderUnavailableError
        detail = "the provider could not complete this request"

    return kind(
        detail,
        operation=operation,
        provider_request_id=provider_request_id,
        details={"exception": type(exc).__name__} if isinstance(exc, ProviderError) else None,
    )


def asyncio_timeout_types() -> tuple[type[BaseException], ...]:
    """`TimeoutError` from whichever async runtime is present, without importing it."""
    import asyncio

    return (asyncio.TimeoutError,)


def envelope(exc: BaseException, *, request_id: str | None = None) -> dict[str, Any]:
    """Build the wire envelope for any exception, known or not.

    An exception this module does not recognise is reported as an internal error
    with its type name and nothing else. Its message may contain anything --
    including a row, a prompt, or a connection string -- so it is not forwarded.
    That is the difference between this and `str(exc)`, which is what the code
    used to do.
    """
    if isinstance(exc, MindPalaceError):
        body = exc.to_dict()
        if request_id:
            body["request_id"] = request_id
        return body
    return {
        "code": "mp_internal_error",
        "component": "mindpalace",
        "message": "an internal error occurred",
        "retryable": False,
        "request_id": request_id or RequestId.ensure(),
        "details": {"exception": type(exc).__name__},
    }
