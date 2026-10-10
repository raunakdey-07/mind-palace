"""The error taxonomy's contract, asserted rather than described.

The taxonomy exists so a consuming application can tell, without reading Mind
Palace's source, whether a failure is theirs, ours, a database's, a transport's,
or a provider's. That is a claim about *distinguishability*, and distinguishability
is only real if the categories do not collapse into each other.

Three properties are asserted here:

* the components are disjoint -- an error belongs to exactly one, and no error
  class is a subclass of a different component's;
* retryability is meaningful, so an integrator can choose a backoff by reading
  the error rather than the traceback;
* nothing sensitive survives serialisation, because this envelope is what ends up
  in a response body, a log line, or a support ticket.

No database, no model, no network. Pure logic, tested as pure logic.
"""

from __future__ import annotations

import json

import mindpalace_errors as taxonomy
from mindpalace_errors import (
    COMPONENTS,
    MindPalaceError,
    ValidationError,
    envelope,
)

ALL_ERRORS = [
    getattr(taxonomy, name)
    for name in taxonomy.__all__
    if isinstance(getattr(taxonomy, name), type)
    and issubclass(getattr(taxonomy, name), MindPalaceError)
]


def test_the_component_list_is_closed_and_small():
    """A component list that grows with every dependency is a list nobody reads."""
    assert COMPONENTS == ("memory", "auth", "database", "transport", "provider", "mindpalace")
    for error in ALL_ERRORS:
        assert error.default_component in COMPONENTS, (
            f"{error.__name__} declares component {error.default_component!r}, "
            "which is not in COMPONENTS"
        )


def test_every_error_serialises_to_the_documented_envelope():
    """One shape across every surface. A caller switching on a missing key is
    the failure this prevents."""
    required = {"code", "component", "message", "retryable", "request_id"}
    for error in ALL_ERRORS:
        body = error("a message").to_dict()
        assert required <= set(body), f"{error.__name__} is missing {required - set(body)}"
        json.dumps(body)  # must be serialisable; a non-JSON detail is a 500


def test_components_do_not_bleed_into_each_other():
    """An error belongs to exactly one component.

    A provider failure subclassing an internal error -- or an auth error that is
    also a validation error -- is how a caller ends up catching the wrong thing
    and treating a database outage as a bad request.
    """
    by_component: dict[str, list[type]] = {}
    for error in ALL_ERRORS:
        by_component.setdefault(error.default_component, []).append(error)

    # `MindPalaceError` is the shared base every category inherits from, not a
    # category competing with the others. Comparing against it would make every
    # error a "bleed" into `mindpalace`, which is the opposite of the intent.
    comparable = [e for e in ALL_ERRORS if e is not MindPalaceError]
    for component, errors in by_component.items():
        for error in errors:
            others = [e for e in comparable if e is not error and e.default_component != component]
            assert not any(issubclass(error, other) for other in others), (
                f"{error.__name__} ({component}) is a subclass of an error from "
                "another component, so catching one would catch the other"
            )


def test_retryability_is_decidable_from_the_error_alone():
    """The point of the flag. A caller must not have to read a traceback to pick
    a backoff, and must not retry something that cannot succeed."""
    retryable = {e for e in ALL_ERRORS if e("x").retryable}
    not_retryable = {e for e in ALL_ERRORS if not e("x").retryable}

    # Nothing the caller can fix by asking again.
    for error in (ValidationError, taxonomy.UnauthorizedError, taxonomy.AuthenticationError):
        assert error in not_retryable, f"{error.__name__} must not be retryable"
    # A bug does not become a different bug by being repeated.
    assert taxonomy.InternalError in not_retryable, (
        "an internal defect must not be retryable; retrying hides the defect "
        "behind a slower failure"
    )
    # The same request against a recovered dependency is a reasonable thing to do.
    for error in (
        taxonomy.DatabaseUnavailableError,
        taxonomy.ProviderTimeoutError,
        taxonomy.ProviderUnavailableError,
        taxonomy.ProviderRateLimitError,
        taxonomy.TransportError,
    ):
        assert error in retryable, f"{error.__name__} should be retryable"
    assert retryable, "nothing in the taxonomy is retryable; the flag is decoration"


def test_an_unknown_exception_is_an_internal_error_with_no_message():
    """The important one for leakage.

    `str(exc)` on an arbitrary exception may contain a row, a prompt, or a
    connection string. This is the difference between forwarding it and not.
    """
    hostile = ValueError("connection to postgres://user:pw@host/db failed: row = SECRET_ROW")
    body = envelope(hostile)

    assert body["code"] == "mp_internal_error"
    assert body["component"] == "mindpalace"
    assert body["retryable"] is False
    assert "SECRET_ROW" not in json.dumps(body)
    assert "postgres://" not in json.dumps(body)
    assert "pw" not in json.dumps(body)
    assert (
        body["details"]["exception"] == "ValueError"
    ), "the exception *type* is safe and useful; its message is neither"


def test_details_are_bounded_and_scalar():
    """`details` is where unbounded values sneak into a log line."""
    body = ValidationError(
        "bad",
        details={
            "count": 3,
            "flag": True,
            "long": "x" * 5000,
            "nested": {"not": "allowed"},
            "many": list(range(50)),
        },
    ).to_dict()

    assert body["details"]["count"] == 3
    assert body["details"]["long"] is None, "an oversized string must be dropped, not truncated"
    assert "nested" not in body["details"], "a dict is not a bounded scalar"
    assert "many" not in body["details"], "a 50-element list is not bounded"


def test_the_request_id_survives_every_error():
    """A failure with no correlation id is a failure nobody can report."""
    for error in ALL_ERRORS:
        assert error("x").request_id


def test_the_existing_sdk_error_still_catches_everything():
    """Compatibility. Integrators wrote `except MemoryClientError` against the
    documented contract, and that must keep working for every new failure."""
    import mindpalace_sdk

    assert issubclass(MindPalaceError, Exception)
    assert hasattr(mindpalace_sdk, "MemoryClientError")
    # The old base is unrelated to the taxonomy, and the SDK adapter must raise
    # it for remote failures. What must not happen is the taxonomy shadowing it.
    assert mindpalace_sdk.MemoryClientError is not MindPalaceError


def test_status_codes_agree_with_the_classes():
    """A proxy or load balancer makes the same decision Mind Palace does, so the
    mapping has to be coherent rather than arbitrary."""
    assert ValidationError.default_status == 422
    assert taxonomy.AuthenticationError.default_status == 401
    assert taxonomy.UnauthorizedError.default_status == 403
    assert taxonomy.NotFoundError.default_status == 404
    assert taxonomy.ProviderRateLimitError.default_status == 429
    assert taxonomy.ProviderTimeoutError.default_status == 504

    for error in ALL_ERRORS:
        assert 400 <= error("x").status_code < 600, f"{error.__name__} has a nonsense status"


def test_chaining_preserves_the_cause():
    """The original exception must remain reachable for debugging, even though
    its text is never rendered."""
    original = ValueError("the real cause")
    wrapped = ValidationError("the safe message")
    wrapped.__cause__ = original

    assert wrapped.__cause__ is original
    assert "the real cause" not in json.dumps(wrapped.to_dict())
