"""Version 1 public memory contract; no persistence models exposed."""

import json
from typing import Any, ClassVar, Literal

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
    info_state: str = "approved"


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


#: Whether the ranking can be acted on as-is.
#:
#: `confident`  -- rank 1 clears the abstention floor and beats rank 2 by the margin.
#: `ambiguous`  -- it does not, so the caller must adjudicate `candidates` itself.
#: `abstained`  -- nothing cleared the floor. This is a stated answer, not an empty one.
MatchStatus = Literal["confident", "ambiguous", "abstained"]


class Candidate(Contract):
    """One ranked claim the caller can adjudicate against, with its receipts.

    A ranking that returns only rank 1 makes a near-tie indistinguishable from a
    confident hit: on the 175 answerable dev questions, rank 1 is correct for 117
    and the median `score_1 - score_2` of a wrong rank 1 is 0.047, against 0.096
    for a right one. Nothing in a single-score response distinguishes the two.

    Each candidate carries the exact quote that backs it and the version it came
    from, so the caller can check the source instead of guessing between two
    plausible sentences.
    """

    key: str
    score: float
    status: Literal["CURRENT", "SUPERSEDED", "CONFLICTING", "UNCERTAIN"]
    value: Any
    claim_ids: list[str]
    version_ids: list[str]
    path: str
    quotes: list[str]


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
    # How far the ranking can be trusted, and the near-misses behind it.
    #
    # Excluded from `model_dump`, and therefore from `canonical_json`, the
    # receipt digest and the budget, while still carried on the object. Callers
    # read them through `diagnostics()`, which REST, MCP and the SDK merge into
    # what they return.
    #
    # `exclude=True` rather than `exclude_if`, because `exclude_if` removes a
    # field only while it holds its default. That would put `candidates` into the
    # digest exactly when the ranking was ambiguous and leave it out when it was
    # not, which makes the authoritative hash depend on retrieval confidence --
    # so two identical memories would attest differently depending on what beat
    # each other. `exclude=True` removes them unconditionally, which is the only
    # behaviour a digest can have.
    #
    # They are derived from claims the digest already contains. `match_status` is
    # a function of the ranking; `candidates` is a reordering of claims already
    # there. That is why they are not part of what was answered.
    match_status: MatchStatus = Field(default="abstained", exclude=True)
    match_margin: float | None = Field(default=None, exclude=True)
    candidates: list[Candidate] = Field(default_factory=list, exclude=True)
    #: Sub-parts of a compound query that had no supporting evidence.
    #: Derived from the ranking, like the other diagnostics; excluded from the
    #: digest for the same reason.
    unmatched_subparts: list[str] = Field(default_factory=list, exclude=True)

    #: The fields `model_dump` leaves out because the digest must not move.
    DIAGNOSTICS: ClassVar[tuple[str, ...]] = (
        "match_status",
        "match_margin",
        "candidates",
        "unmatched_subparts",
    )

    def canonical_json(self) -> str:
        """The budget applies to this complete compact JSON, including escaped content.

        Omits ``receipt``, which is derived FROM this JSON and verifies it, so
        folding it in would make the digest cover itself. The three retrieval
        diagnostics are also omitted, by name: `exclude=True` already removes them
        from a `MemoryResponse`, and popping by name as well means a `MemoryWire`
        -- which re-declares them so REST can serialize them -- still hashes
        identically to the response it came from. Two places to keep in step, and
        one of them is a digest.
        """
        payload = self.model_dump(mode="json")
        payload.pop("receipt", None)
        for name in self.DIAGNOSTICS:
            payload.pop(name, None)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def diagnostics(self) -> dict[str, Any]:
        """The retrieval diagnostics, for a caller deciding how to act.

        Kept out of `canonical_json` so the receipt digest and the budget are a
        property of the answer rather than of the ranking that produced it. This
        is where a caller gets them anyway, and it is the same place `receipt`
        is surfaced.
        """
        return {
            "match_status": self.match_status,
            "match_margin": self.match_margin,
            "candidates": [c.model_dump(mode="json") for c in self.candidates],
            "unmatched_subparts": list(self.unmatched_subparts),
        }

    def public_dict(self) -> dict[str, Any]:
        """What MCP and the SDK return: the answer plus its diagnostics.

        `model_dump` alone, because that is the digest and the receipt verifies it.
        REST goes through `MemoryWire`, which the router declares as its
        `response_model` for the same reason.
        """
        return {**self.model_dump(mode="json"), **self.diagnostics()}


class MemoryWire(MemoryResponse):
    """The REST response body: the answer, plus the retrieval diagnostics.

    Same fields, re-declared without `exclude`. `MemoryResponse.model_dump` is the
    authoritative digest and omits the diagnostics on purpose, so a route that
    returns `MemoryResponse` directly loses them -- and FastAPI serializes a
    `response_model` through `model_dump`.

    Re-declaring rather than adding a field is what makes this a wire shape
    instead of a second contract: the field list, the validation and the OpenAPI
    schema are the same, and only the exclusion differs.
    """

    match_status: MatchStatus = "abstained"
    match_margin: float | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    unmatched_subparts: list[str] = Field(default_factory=list)

    def canonical_json(self) -> str:
        """The budget applies to this complete compact JSON, including escaped content.

        Omits ``receipt``, which is derived FROM this JSON and verifies it, so
        folding it in would make the digest cover itself.

        Also omits the three retrieval diagnostics, by name. `exclude=True` already
        removes them from a `MemoryResponse`; popping by name as well means a
        `MemoryWire` -- which re-declares them without the exclusion so REST can
        serialize them -- still hashes identically to the response it came from.
        Two places to keep in step, and one of them is a digest.
        """
        payload = self.model_dump(mode="json")
        payload.pop("receipt", None)
        for name in self.DIAGNOSTICS:
            payload.pop(name, None)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
