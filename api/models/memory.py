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

    def canonical_json(self) -> str:
        """The budget applies to this complete compact JSON, including escaped content."""
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
