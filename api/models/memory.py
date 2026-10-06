"""Version 1 public memory contract; no persistence models exposed."""

import json
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryRequest(Contract):
    corpus: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    query: str = Field(default="", max_length=2000)
    as_of: AwareDatetime | None = None
    valid_at: AwareDatetime | None = None
    path: str | None = Field(default=None, min_length=1, max_length=2048)
    claim_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    snapshot_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    budget: int = Field(default=8000, ge=512, le=128000, strict=True)
    intent: Literal[
        "auto", "current", "historical", "temporal", "change", "conflict", "provenance"
    ] = "auto"
    # Opt-in. Absent by default so an existing client receives byte-identical
    # output; a receipt is evidence about the answer, not part of the answer.
    include_receipt: bool = False

    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        if value and not value.strip():
            raise ValueError("query must be empty (all memories) or non-whitespace text")
        return value.strip()


class Evidence(Contract):
    id: str
    claim_id: str
    version_id: str
    document_id: str
    path: str
    source_hash: str
    chunk_id: str
    heading: str | None
    text: str
    start_offset: int
    end_offset: int
    observed_at: AwareDatetime


class Claim(Contract):
    id: str
    key: str
    value: Any
    claim: str
    status: Literal["CURRENT", "SUPERSEDED", "CONFLICTING", "UNCERTAIN"]
    version_id: str
    path: str
    observed_at: AwareDatetime
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    supersedes_id: str | None = None
    evidence_ids: list[str]


class Source(Contract):
    document_id: str
    version_id: str
    path: str
    source_hash: str
    observed_at: AwareDatetime


class Conflict(Contract):
    id: str
    key: str
    claims: list[Claim]


class Change(Contract):
    version_id: str
    predecessor_id: str | None
    event: str
    path: str
    observed_at: AwareDatetime
    memory_changed: bool
    relationship: Literal["SUPERSEDES", "DOCUMENT_MODIFIED", "LIFECYCLE"]
    previous: list[Claim]
    current: list[Claim]


class State(Contract):
    as_of: AwareDatetime | None = None
    valid_at: AwareDatetime | None = None
    snapshot: str | None = None


class Snapshot(Contract):
    id: str
    as_of: AwareDatetime
    observed_at: AwareDatetime
    version_ids: list[str]


class FeedItem(Contract):
    version_id: str
    document_id: str
    corpus: str
    path: str
    version_number: int
    status: Literal["NEW", "MODIFIED", "DELETED", "RESTORED"]
    observed_at: AwareDatetime
    predecessor_id: str | None = None


class FeedResponse(Contract):
    schema_version: Literal[1] = 1
    corpus: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    items: list[FeedItem]
    has_more: bool
    next_cursor: str | None = None
    page_size: int = Field(ge=1, le=500, strict=True)

    def canonical_json(self) -> str:
        """Return the stable compact JSON representation used by the CLI."""
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class MemoryResponse(Contract):
    schema_version: int = 1
    query: str
    corpus: str
    state: State = Field(default_factory=State)
    current_memories: list[Claim] = Field(default_factory=list)
    historical_memories: list[Claim] = Field(default_factory=list)
    uncertain_memories: list[Claim] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    snapshot: Snapshot | None = None
    truncated: bool = False
    budget_unit: Literal["unicode_characters"] = "unicode_characters"
    # The canonical receipt bundle, present only when the request asked for it.
    # EXCLUDED from canonical_json and from the budget: a receipt describes the
    # answer, it is not part of it. Including it would change the authoritative
    # digest and the pack identity the whole project is gated on.
    receipt: dict[str, Any] | None = Field(default=None, exclude_if=lambda v: v is None)

    def canonical_json(self) -> str:
        """The budget applies to this complete compact JSON, including escaped content.

        Deliberately omits ``receipt``. The receipt is derived FROM this JSON and
        verifies it, so folding it in would make the digest cover itself.
        """
        payload = self.model_dump(mode="json")
        payload.pop("receipt", None)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
