# SPDX-License-Identifier: Apache-2.0
"""Optional OpenTelemetry spans for the five product operations.

Deliberately not a dependency. `opentelemetry-api` is imported lazily and every
call here is a no-op when it is absent, so installing Mind Palace does not install
tracing, and a deployment that has not configured an exporter pays one import
attempt per operation. Nothing here is a requirement to run the product.

Three rules this module exists to enforce:

* Memory content never enters telemetry. Span attributes carry sizes, counts and
  identifiers -- never a claim, a document body, a question or a digest. A trace
  system is a place data gets copied to, and this is not where memory belongs.
* An instrumentation failure is never a product failure. A tracer that cannot
  start is treated as no tracer.
* An exception from the body reaches the span. Instrumenting the shared read entry
  point is only worth anything if a failing query is recorded as failing.

`memory.verify` is instrumented by the CLI command that drives the offline
verifier, not by `memory_receipt`. That module is standard-library-only by
contract -- it is how anyone can verify a receipt without installing anything --
and it must not acquire a dependency to emit a span.
"""

from __future__ import annotations

from contextlib import contextmanager

#: The five operations a developer actually performs.
OPERATIONS = (
    "memory.remember",
    "memory.recall",
    "memory.explain",
    "memory.receipt",
    "memory.verify",
)

#: A free-form attribute longer than this is reduced to a length, so a
#: misconfigured call site cannot copy content into a trace backend.
_MAX_STRING = 120


def _tracer():
    """The tracer, or None when OpenTelemetry is not installed."""
    try:
        from opentelemetry import trace
    except Exception:  # noqa: BLE001 - not installed, or a broken installation
        return None
    try:
        return trace.get_tracer("mindpalace")
    except Exception:  # noqa: BLE001
        return None


@contextmanager
def span(operation: str, **attributes):
    """Instrument one operation.

    Yields the active span so the caller can set attributes on it *inside* the
    block -- OpenTelemetry drops attributes set on an ended span -- or None when
    there is no tracer. An exception from the body is passed to the span and then
    re-raised unchanged.
    """
    tracer = _tracer()
    if tracer is None:
        yield None
        return

    try:
        manager = tracer.start_as_current_span(operation)
        active = manager.__enter__()
    except Exception:  # noqa: BLE001 - a tracer that cannot start is not an error
        yield None
        return

    try:
        _annotate(active, attributes)
        yield active
    except BaseException as exc:
        # Record the failure on the span, then let the caller see it unchanged.
        _record_failure(active, exc)
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def _record_failure(active, exc: BaseException) -> None:
    """Mark a span as failed, if this tracer has the vocabulary for it."""
    try:
        from opentelemetry.trace import StatusCode

        active.record_exception(exc)
        active.set_status(StatusCode.ERROR, type(exc).__name__)
    except Exception:  # noqa: BLE001 - recording must never mask the failure
        pass


def _annotate(active, attributes: dict) -> None:
    """Set the safe attributes on a span, skipping any that cannot be set."""
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            active.set_attribute(key, _summarise(key, value))
        except Exception:  # noqa: BLE001 - an attribute must never fail an operation
            continue


def _summarise(key: str, value) -> object:
    """Reduce an attribute to something safe to record.

    Counts, durations and booleans pass through. An identifier is truncated. Any
    other free text becomes a length, so the answer itself cannot reach a trace.
    """
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    text = str(value)
    if key.endswith("_id"):
        return text[:64]
    if key.endswith(("_count", "_ms", "_chars")):
        return len(text)
    return f"<{len(text)} chars>" if len(text) > _MAX_STRING else text


__all__ = ["OPERATIONS", "span"]
