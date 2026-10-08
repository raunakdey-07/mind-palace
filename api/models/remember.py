# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The public write contract: one statement becomes one authoritative memory.

Additive to the version 1 memory contract. `MemoryResponse` and everything it
carries are unchanged; this is only the request and result of a write, so a client
that never writes is unaffected.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.services.remember import MAX_STATEMENT


class RememberRequest(BaseModel):
    """Record a fact.

    Deliberately statement-only. `mindpalace remember --file` and the SDK's
    `file=` read a local file, and that is a decision for a trusted operator at
    their own machine. This endpoint has no authentication, so accepting a path
    would let any caller read a file relative to the server's working directory
    and publish its contents as memory. Importing documents is `POST
    /api/ingest/file` and `sync_repo`, which take content explicitly.
    """

    model_config = ConfigDict(extra="forbid")

    statement: str | None = Field(
        default=None,
        max_length=MAX_STATEMENT,
        description="The fact to remember.",
    )
    corpus: str = Field(
        default="default",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Corpus to write to. Defaults to `default`.",
    )
    key: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Stable name for this fact. Remembering the same key again is how memory "
            "records that the fact changed; omitting it makes each statement its own "
            "memory."
        ),
    )

    @field_validator("statement", "key", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        """Treat a blank string as absent, so `key=""` is not a silent error."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("statement")
    @classmethod
    def no_null_bytes(cls, value: str | None) -> str | None:
        """A NUL cannot be stored in PostgreSQL text, so refuse it here, clearly."""
        if value is not None and "\x00" in value:
            raise ValueError("statement must not contain a null character")
        return value


class RememberResult(BaseModel):
    """What one write recorded. ``changed`` is the idempotent outcome, not an error."""

    model_config = ConfigDict(extra="forbid")

    corpus: str
    version_id: str | None = None
    event: Literal["NEW", "MODIFIED", "RESTORED", "UNCHANGED"]
    path: str
    key: str | None = None
    changed: bool = Field(description="False when the same memory was already recorded.")
