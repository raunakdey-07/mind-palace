# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Read a Mind Palace Memory Pack with nothing but the standard library.

A Memory Pack is the interchange boundary. A receiving system should be able to
understand what the corpus knows without a database, a model, a network, or any
part of this server. This module is that reader.

It imports nothing from ``api``, nothing from the SDK, and nothing outside the
standard library, so it can be copied into another project on its own. The pack
it reads is plain JSON and is self-describing: every claim names the evidence
that supports it, and every evidence row names the document version and the
character offsets inside the archived chunk.

    from memory_pack import MemoryPack

    pack = MemoryPack.from_json(open("pack.json").read())
    pack.status                  # resolved | conflicting | uncertain | no_relevant_memory
    pack.current                 # claims believed true now
    pack.conflicts               # keys whose sources disagree, both sides kept
    pack.evidence_for(claim.id)  # exact quote, path, offsets, observed time
    pack.digest()                # sha256 of the canonical bytes

``status`` is derived, not stored, because the pack already carries everything
needed to compute it. An empty list is never ambiguous here: a pack with no
relevant memory says ``no_relevant_memory`` instead of returning nothing, so a
consumer can tell "there is nothing" from "there are two answers and they
disagree".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    "MemoryPack",
    "PackClaim",
    "PackConflict",
    "PackEvidence",
    "PackSource",
    "PackChange",
    "PackState",
    "PackError",
    "RESOLVED",
    "CONFLICTING",
    "UNCERTAIN",
    "NO_RELEVANT_MEMORY",
    "EMPTY",
    "SUPPORTED_SCHEMA_VERSIONS",
]

# The contract versions this reader understands. This describes the wire format,
# not the application release. A pack from a newer producer is refused rather
# than half-understood.
SUPPORTED_SCHEMA_VERSIONS = (1,)

RESOLVED = "resolved"
CONFLICTING = "conflicting"
UNCERTAIN = "uncertain"
NO_RELEVANT_MEMORY = "no_relevant_memory"
EMPTY = "empty"

# Written into ``constraints`` by the authoritative layer when relevance found
# nothing. Absence is a stated value, not an empty list.
_ABSENCE_MARKER = "NO_RELEVANT_MEMORY"


class PackError(ValueError):
    """The bytes are not a Memory Pack this reader can trust."""

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class PackState:
    """When the pack was resolved."""

    as_of: str | None = None
    valid_at: str | None = None
    snapshot: str | None = None

    def describe(self) -> str:
        if self.snapshot:
            return f"as of snapshot {self.snapshot[:12]}"
        if self.as_of:
            return f"as of {self.as_of}"
        if self.valid_at:
            return f"state at {self.valid_at}"
        return "state at resolution time"


@dataclass(frozen=True)
class PackEvidence:
    """An exact quote from one immutable document version.

    ``start_offset``/``end_offset`` are character offsets inside the archived
    chunk named by ``chunk_id``, so the quote can be re-verified against the
    source bytes without the database.
    """

    id: str
    claim_id: str
    version_id: str
    document_id: str
    path: str
    source_hash: str
    chunk_id: str
    text: str
    start_offset: int
    end_offset: int
    observed_at: str
    heading: str | None = None

    def offset_span(self) -> int:
        return self.end_offset - self.start_offset

    def is_consistent(self) -> bool:
        """True when the offsets actually span the quote."""
        return self.offset_span() == len(self.text)


@dataclass(frozen=True)
class PackClaim:
    """One authored statement, with the validity that decides whether it holds."""

    id: str
    key: str
    claim: str
    value: Any
    status: str
    path: str
    version_id: str
    observed_at: str
    evidence_ids: tuple[str, ...] = ()
    valid_from: str | None = None
    valid_until: str | None = None
    supersedes_id: str | None = None

    def validity(self) -> str:
        if self.valid_from and self.valid_until:
            return f"{self.valid_from} to {self.valid_until}"
        if self.valid_from:
            return f"from {self.valid_from}"
        if self.valid_until:
            return f"until {self.valid_until}"
        return "no authored bound"


@dataclass(frozen=True)
class PackConflict:
    """Authored claims for one key that disagree. Never reduced to a winner."""

    key: str
    claims: tuple[PackClaim, ...]


@dataclass(frozen=True)
class PackChange:
    """One lifecycle or supersession event."""

    event: str
    path: str
    observed_at: str
    relationship: str
    version_id: str | None = None
    predecessor_id: str | None = None
    previous: tuple[PackClaim, ...] = ()
    current: tuple[PackClaim, ...] = ()


@dataclass(frozen=True)
class PackSource:
    document_id: str
    version_id: str
    path: str
    source_hash: str
    observed_at: str


def _require(data: dict, key: str, kind: type, where: str) -> Any:
    if key not in data:
        raise PackError(f"missing required field {key!r}", field=key)
    value = data[key]
    if not isinstance(value, kind):
        raise PackError(
            f"field {key!r} must be {kind.__name__}, got {type(value).__name__}",
            field=key,
        )
    return value


def _claim(data: dict) -> PackClaim:
    return PackClaim(
        id=_require(data, "id", str, "claim"),
        key=_require(data, "key", str, "claim"),
        claim=_require(data, "claim", str, "claim"),
        value=data.get("value"),
        status=_require(data, "status", str, "claim"),
        path=_require(data, "path", str, "claim"),
        version_id=_require(data, "version_id", str, "claim"),
        observed_at=_require(data, "observed_at", str, "claim"),
        evidence_ids=tuple(data.get("evidence_ids") or ()),
        valid_from=data.get("valid_from"),
        valid_until=data.get("valid_until"),
        supersedes_id=data.get("supersedes_id"),
    )


def _evidence(data: dict) -> PackEvidence:
    return PackEvidence(
        id=_require(data, "id", str, "evidence"),
        claim_id=_require(data, "claim_id", str, "evidence"),
        version_id=_require(data, "version_id", str, "evidence"),
        document_id=_require(data, "document_id", str, "evidence"),
        path=_require(data, "path", str, "evidence"),
        source_hash=_require(data, "source_hash", str, "evidence"),
        chunk_id=_require(data, "chunk_id", str, "evidence"),
        text=_require(data, "text", str, "evidence"),
        start_offset=_require(data, "start_offset", int, "evidence"),
        end_offset=_require(data, "end_offset", int, "evidence"),
        observed_at=_require(data, "observed_at", str, "evidence"),
        heading=data.get("heading"),
    )


@dataclass(frozen=True)
class MemoryPack:
    """A bounded, evidence-backed view of one corpus at one instant.

    Immutable. Two packs with the same canonical bytes are the same pack.
    """

    schema_version: int
    query: str
    corpus: str
    state: PackState
    current: tuple[PackClaim, ...] = ()
    historical: tuple[PackClaim, ...] = ()
    uncertain: tuple[PackClaim, ...] = ()
    conflicts: tuple[PackConflict, ...] = ()
    changes: tuple[PackChange, ...] = ()
    evidence: tuple[PackEvidence, ...] = ()
    sources: tuple[PackSource, ...] = ()
    constraints: tuple[str, ...] = ()
    truncated: bool = False
    budget_unit: str = "unicode_characters"
    snapshot: dict | None = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    # -- construction --------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Any) -> "MemoryPack":
        if not isinstance(data, dict):
            raise PackError(f"a Memory Pack is a JSON object, got {type(data).__name__}")

        version = data.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise PackError(
                f"unsupported schema_version {version!r}; "
                f"this reader understands {list(SUPPORTED_SCHEMA_VERSIONS)}",
                field="schema_version",
            )

        state = data.get("state") or {}
        return cls(
            schema_version=version,
            query=_require(data, "query", str, "pack"),
            corpus=_require(data, "corpus", str, "pack"),
            state=PackState(
                as_of=state.get("as_of"),
                valid_at=state.get("valid_at"),
                snapshot=state.get("snapshot"),
            ),
            current=tuple(_claim(c) for c in data.get("current_memories") or ()),
            historical=tuple(_claim(c) for c in data.get("historical_memories") or ()),
            uncertain=tuple(_claim(c) for c in data.get("uncertain_memories") or ()),
            conflicts=tuple(
                PackConflict(
                    key=_require(g, "key", str, "conflict"),
                    claims=tuple(_claim(c) for c in g.get("claims") or ()),
                )
                for g in data.get("conflicts") or ()
            ),
            changes=tuple(
                PackChange(
                    event=_require(c, "event", str, "change"),
                    path=_require(c, "path", str, "change"),
                    observed_at=_require(c, "observed_at", str, "change"),
                    relationship=_require(c, "relationship", str, "change"),
                    version_id=c.get("version_id"),
                    predecessor_id=c.get("predecessor_id"),
                    previous=tuple(_claim(x) for x in c.get("previous") or ()),
                    current=tuple(_claim(x) for x in c.get("current") or ()),
                )
                for c in data.get("changes") or ()
            ),
            evidence=tuple(_evidence(e) for e in data.get("evidence") or ()),
            sources=tuple(
                PackSource(
                    document_id=_require(s, "document_id", str, "source"),
                    version_id=_require(s, "version_id", str, "source"),
                    path=_require(s, "path", str, "source"),
                    source_hash=_require(s, "source_hash", str, "source"),
                    observed_at=_require(s, "observed_at", str, "source"),
                )
                for s in data.get("sources") or ()
            ),
            constraints=tuple(data.get("constraints") or ()),
            truncated=bool(data.get("truncated", False)),
            budget_unit=data.get("budget_unit") or "unicode_characters",
            snapshot=data.get("snapshot"),
            raw=data,
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "MemoryPack":
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PackError(f"pack is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    # -- identity ------------------------------------------------------------

    def canonical_json(self) -> str:
        """The bytes a digest is taken over, independent of key order."""
        return json.dumps(
            self.raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def digest(self) -> str:
        """SHA-256 of the canonical bytes.

        Two packs with the same digest are the same answer to the same question
        over the same authoritative state, whatever produced them.
        """
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def size(self) -> int:
        """Length of the canonical bytes, the same unit the budget counts."""
        return len(self.canonical_json())

    # -- interpretation ------------------------------------------------------

    @property
    def status(self) -> str:
        """What kind of answer this is. Never derived from emptiness alone."""
        if _ABSENCE_MARKER in self.constraints:
            return NO_RELEVANT_MEMORY
        if self.conflicts:
            return CONFLICTING
        if self.current or self.historical or self.changes:
            return UNCERTAIN if self.uncertain and not self.current else RESOLVED
        if self.evidence:
            return RESOLVED
        return EMPTY

    def all_claims(self) -> tuple[PackClaim, ...]:
        """Every claim the pack carries, wherever it appears."""
        seen: dict[str, PackClaim] = {}
        groups: Iterable[PackClaim] = self.current + self.historical + self.uncertain
        for claim in groups:
            seen[claim.id] = claim
        for group in self.conflicts:
            for claim in group.claims:
                seen[claim.id] = claim
        for change in self.changes:
            for claim in change.previous + change.current:
                seen[claim.id] = claim
        return tuple(seen[k] for k in sorted(seen))

    def evidence_for(self, claim_id: str) -> tuple[PackEvidence, ...]:
        """Exact quotes backing one claim, in a stable order."""
        return tuple(e for e in self.evidence if e.claim_id == claim_id)

    def source_for(self, version_id: str) -> PackSource | None:
        for source in self.sources:
            if source.version_id == version_id:
                return source
        return None

    def answers_for(self, key: str) -> tuple[PackClaim, ...]:
        """Every claim filed under one authored key, whatever its status."""
        return tuple(c for c in self.all_claims() if c.key == key)

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted({c.key for c in self.all_claims()}))

    def verify(self) -> list[str]:
        """Structural problems a consumer should refuse to trust.

        Returns human-readable problems rather than raising, so a caller can
        decide. An empty list means the pack is internally consistent.
        """
        problems: list[str] = []
        known = {e.id for e in self.evidence}
        sources = {s.version_id for s in self.sources}

        for claim in self.all_claims():
            if not claim.evidence_ids:
                problems.append(f"claim {claim.id} ({claim.key}) has no evidence")
            for eid in claim.evidence_ids:
                if eid not in known:
                    problems.append(f"claim {claim.id} references missing evidence {eid[:12]}")
            if claim.version_id not in sources:
                problems.append(
                    f"claim {claim.id} has no source for version {claim.version_id[:12]}"
                )

        by_id = {c.id: c for c in self.all_claims()}
        for e in self.evidence:
            if e.claim_id not in by_id:
                problems.append(f"evidence {e.id[:12]} is orphaned: no claim {e.claim_id[:12]}")
            if not e.is_consistent():
                problems.append(
                    f"evidence {e.id[:12]} offsets {e.start_offset}:{e.end_offset} "
                    f"do not span a {len(e.text)}-character quote"
                )

        if self.truncated and not self.current and not self.conflicts:
            problems.append("truncated with no authoritative content retained")

        return problems

    def summary(self) -> str:
        """One-paragraph answer for a human or an agent deciding what to read."""
        counts = (
            f"{len(self.current)} current, {len(self.historical)} historical, "
            f"{len(self.uncertain)} uncertain, {len(self.conflicts)} conflict(s), "
            f"{len(self.changes)} change(s), {len(self.evidence)} evidence"
        )
        lines = [
            f"Memory Pack schema {self.schema_version} for corpus {self.corpus!r} "
            f"{self.state.describe()}",
            f"Question: {self.query}",
            f"Status: {self.status} ({counts})",
        ]
        if self.truncated:
            lines.append(f"Truncated: yes, budget unit {self.budget_unit}")
        if self.conflicts:
            for group in self.conflicts:
                sides = ", ".join(str(c.value) for c in group.claims)
                lines.append(f"  conflict {group.key}: {sides} (unresolved)")
        for claim in self.current:
            lines.append(f"  current {claim.key} = {claim.value!r} [{claim.claim}]")
        for change in self.changes:
            detail = (
                f" {change.previous[0].value} -> {change.current[0].value}"
                if (change.previous and change.current)
                else ""
            )
            lines.append(
                f"  change {change.observed_at} {change.path} {change.relationship}{detail}"
            )
        if self.status == NO_RELEVANT_MEMORY:
            lines.append("  The corpus holds material, but nothing relevant to this question.")
        return "\n".join(lines)
