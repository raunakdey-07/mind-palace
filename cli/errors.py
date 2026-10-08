# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Human-facing failure text for the Mind Palace CLI.

An error should answer three questions: what happened, why, and what to run next.
The repository already produces stable machine codes (`memory_unavailable`,
`database_unavailable`, `corpus_not_found`, ...), so this maps those codes to
guidance and keeps the code, status and underlying exception available for
`--verbose`. Nothing is discarded; it is reordered so the useful part comes
first.
"""

from __future__ import annotations

from typing import NamedTuple


class Advice(NamedTuple):
    """One failure, explained. ``code`` is what a script should match on."""

    code: str
    what: str
    why: str
    do: str
    status: int = 1


#: Where Mind Palace believes the database is. One function, because the guidance
#: for a dead database must not be able to drift between two surfaces.
def database_hint() -> str:
    """The host and port this process would actually connect to."""
    from api.errors import database_hint as _hint

    return _hint()


_START_DATABASE = "Start it with: docker compose up -d postgresql"
_APPLY_SCHEMA = (
    "Apply the schema with:\n" "    python -m alembic -c migrations/alembic.ini upgrade head"
)


def explain(exc: Exception) -> Advice:
    """Turn any failure the product surfaces into actionable text."""
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        return _validation_advice(exc)
    if isinstance(exc, OSError) and getattr(exc, "filename", None) is None and ":" in str(exc):
        return _unreadable_advice(exc)

    code = getattr(exc, "code", None)
    status = int(getattr(exc, "status_code", 1) or 1)
    detail = str(getattr(exc, "message", None) or exc)

    if code in {"database_unavailable", "memory_unavailable", "database_unreachable"}:
        schema = code == "memory_unavailable"
        return Advice(
            code,
            "The memory database is unavailable.",
            (
                f"Mind Palace could not connect to PostgreSQL at {database_hint()}."
                if not schema
                else "Mind Palace connected, but the memory schema is not applied."
            ),
            (_START_DATABASE if not schema else _APPLY_SCHEMA),
            503,
        )
    if code == "transport_error":
        return Advice(
            code,
            "Mind Palace could not reach the memory service.",
            f"Nothing is listening at {getattr(exc, 'base_url', None) or 'the configured API'}.",
            "Check that the service is running, or point --base-url at the right host.",
            503,
        )
    if code == "timeout":
        return Advice(
            code,
            "The memory request timed out.",
            "Mind Palace waited longer than the configured timeout.",
            "Retry, or raise it with --timeout <seconds>.",
            504,
        )
    if code == "corpus_not_found":
        return Advice(
            code,
            "That corpus does not exist.",
            f"No memory is stored under the name {getattr(exc, 'corpus', None) or 'given'!r}.",
            "List the corpora you have with: mindpalace corpora",
            404,
        )
    if code in {"claim_not_found", "document_not_found", "snapshot_not_found"}:
        return Advice(
            code,
            "That memory does not exist here.",
            detail,
            'Recall what you can see with: mindpalace recall "<question>"',
            404,
        )
    if code == "invalid_query":
        return Advice(
            code,
            "That is not a question I can search with.",
            detail,
            'Ask a question with content, e.g. mindpalace recall "what is the datastore"',
            422,
        )
    if code in {"invalid_request", "invalid_timestamp", "invalid_budget"}:
        return Advice(
            code,
            "That request was not valid.",
            detail,
            "Check the option you passed; timestamps are ISO-8601 with a timezone, "
            "for example --as-of 2025-02-01T00:00:00Z.",
            422,
        )
    if code == "invalid_response":
        return Advice(
            code,
            "The memory service returned something unexpected.",
            detail,
            "This usually means a version mismatch between client and server.",
            502,
        )
    return Advice(code or "error", "Mind Palace could not complete that.", detail, "", status)


def _validation_advice(exc: Exception) -> Advice:
    """Turn a pydantic validation error into something a person can act on.

    Without this a bad `--as-of` reaches the user as a raw pydantic message with a
    field path and a type, which is precise and useless: it does not say which option
    was wrong or what a correct value looks like.
    """
    fields = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "__root__")
        fields.append(f"{location or 'the request'}: {error.get('msg', 'is not valid')}")
    return Advice(
        "invalid_request",
        "That option was not valid.",
        "; ".join(fields[:4]) or str(exc),
        "Instants are ISO-8601 with a timezone, for example "
        "--as-of 2025-02-01T00:00:00Z. Run with --help for the expected format.",
        422,
    )


def _unreadable_advice(exc: Exception) -> Advice:
    """A file that could not be read, said as something the user can do about."""
    return Advice(
        "invalid_request",
        "That file could not be read.",
        str(exc).split(": ", 1)[-1],
        "Check the path. A receipt is written by: mindpalace receipt "
        '"<question>" -o receipt.json',
        2,
    )


def render(advice: Advice, exc: Exception, *, verbose: bool = False) -> str:
    """Render one failure. ``verbose`` appends the technical detail for debugging."""
    lines = [advice.what, f"\nWhy: {advice.why}"]
    if advice.do:
        lines.append(f"\nWhat to do:\n  {advice.do}")
    lines.append(f"\n({advice.code})")
    if verbose:
        lines.append(f"\n{type(exc).__name__}: {exc}")
    return "\n".join(lines)
