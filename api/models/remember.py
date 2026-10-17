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
    supersedes_claim_id: str | None = Field(
        default=None,
        min_length=4,
        max_length=200,
        description=(
            "A claim in this corpus that this fact replaces, in any document. Either "
            "the full id or any unique prefix of one, or a phrase taken from the "
            "claim's own text -- the archive matches on content words and refuses "
            "unless exactly one active claim matches. A 64-character hex id is used "
            "as written; a shorter hex string is a prefix; anything else is a phrase."
        ),
    )
    topic_key: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Retire whatever is currently active under this key, even from another "
            "document. Must be a key this write authors."
        ),
    )

    @field_validator("statement", "key", "topic_key", mode="before")
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


class RelatedClaim(BaseModel):
    """A live claim that says roughly what the new one says."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    key: str
    claim: str
    version_id: str
    score: float
    hint: str
    channel: Literal["vector", "lexical"] = Field(
        description=(
            "Which half of the hint produced this row, because the two do not mean "
            "the same thing. `vector` is a cosine over cached claim vectors searched "
            "across the whole corpus; it finds a contradiction phrased differently. "
            "`lexical` is token overlap, used only when no vector was available for "
            "anything in this corpus, and it is the noisier of the two."
        ),
    )


class RememberResult(BaseModel):
    """What one write recorded. ``changed`` is the idempotent outcome, not an error."""

    model_config = ConfigDict(extra="forbid")

    corpus: str
    version_id: str | None = None
    event: Literal["NEW", "MODIFIED", "RESTORED", "UNCHANGED"]
    path: str
    key: str | None = None
    changed: bool = Field(description="False when the same memory was already recorded.")
    # Present only when something already in the corpus reads like this write.
    # The archive cannot decide whether that is a supersession or a contradiction,
    # so it names them and lets the caller link or retire them in the same turn.
    related_active_claims: list[RelatedClaim] = Field(default_factory=list)
    # Handle for `mindpalace feedback <query_id> --verdict ...`. Present only when
    # the query log is on, which it is by default on a local connection and off on
    # a scoped one. Grading real use needs a way to point at a call, and the
    # response is the only place both sides of the conversation already agree.
    query_id: str | None = None
