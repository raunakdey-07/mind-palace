# SPDX-License-Identifier: Apache-2.0
"""Append-only, explicitly authored memory; independent of live retrieval/RRF.

Use an AsyncSession in a caller-owned READ COMMITTED transaction. Acquire
lock_corpus BEFORE any live ingestion/deletion work and keep that transaction
open through record_version/record_deletion. These functions defensively acquire
the same reentrant transaction lock, but never commit or roll back. Let validation
errors escape the transaction context so live writes roll back as well.

Pass the full raw source as content, parsed YAML metadata, and the final chunks.
JSON-compatible YAML metadata is archived, with dates/timestamps normalized to
ISO strings. Unsupported YAML objects, non-string mapping keys, and non-finite
numbers are rejected rather than silently lost. Each version permits one authored
claim per key; evidence is a nonempty exact quote string or a nonempty list of
distinct quote strings. Duplicate quotes are rejected. List order is preserved
in metadata (and thus the fingerprint); references are resolved in sorted quote order,
using the first occurrence in the lowest-order matching archived chunk. Every
supporting chunk must also contain the claim text. All references are authored
atomically with the version; there is no service for appending evidence later.

as_of is an inclusive OBSERVATION cutoff, not a truth/validity date. NULL validity
means unknown. Current claims are those in the latest non-deleted version of each
path at that cutoff, inside any authored validity window. Historical claims include
all noncurrent claims; superseded_memories is their explicitly linked subset.
Conflicts mean differing canonical JSON values for the same exact key across
active documents, not semantic contradiction. Query matching is lexical only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, time, timezone
from itertools import combinations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ClaimValidationError(ValueError):
    """Validation failure with identifiers, without exposing source content in logs."""

    def __init__(self, path: str, version_id: str, reason: str):
        self.path = path
        self.version_id = version_id
        self.reason = reason
        super().__init__(f"document={path} version={version_id}: {reason}")


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError("metadata must contain JSON-compatible YAML values (or dates)")


def _canonical(value) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(*parts) -> str:
    return hashlib.sha256(_canonical(list(parts)).encode("utf-8")).hexdigest()


def memory_document_id(corpus_id: str, path: str) -> str:
    """Stable SHA-256(corpus_id + ':' + path), including across deletion/restore."""
    return hashlib.sha256(f"{corpus_id}:{path}".encode("utf-8")).hexdigest()


def _timestamp(value, *, validity: bool = False) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            if validity and len(value) == 10:
                value = date.fromisoformat(value)
            else:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expected an ISO date or timezone-aware timestamp") from exc
    if validity and isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min, tzinfo=timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("expected a timezone-aware timestamp (validity also accepts dates)")
    return value.astimezone(timezone.utc)


def _prepare(metadata: dict, chunks: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    if not isinstance(metadata, dict) or not isinstance(chunks, list):
        raise ValueError("metadata must be a mapping and chunks a list")
    metadata = _json_value(metadata)
    archived = []
    orders = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("each chunk must be a mapping")
        order = chunk.get("order_index")
        heading = chunk.get("heading_path")
        if (
            type(order) is not int
            or order < 0
            or order in orders
            or not isinstance(chunk.get("text"), str)
            or (heading is not None and not isinstance(heading, str))
        ):
            raise ValueError(
                "chunks require text, unique nonnegative order_index, and text heading_path"
            )
        orders.add(order)
        archived.append({"text": chunk["text"], "heading_path": heading, "order_index": order})
    archived.sort(key=lambda chunk: chunk["order_index"])
    authored = metadata.get("claims", [])
    if not isinstance(authored, list):
        raise ValueError("metadata claims must be a list")
    claims = []
    keys = set()
    for index, claim in enumerate(authored):
        if isinstance(claim, str):
            claim = {
                "key": "text:" + _hash(claim),
                "value": claim,
                "claim": claim,
                "evidence": claim,
            }
        if not isinstance(claim, dict) or "value" not in claim:
            raise ValueError("claims require key, value, claim, and evidence")
        if any(not isinstance(claim.get(k), str) or not claim[k].strip() for k in ("key", "claim")):
            raise ValueError("claim key and claim must be nonempty strings")
        evidence = claim.get("evidence")
        quotes = [evidence] if isinstance(evidence, str) else evidence
        if (
            not isinstance(quotes, list)
            or not quotes
            or any(not isinstance(quote, str) or not quote.strip() for quote in quotes)
        ):
            raise ValueError("claim evidence must be a nonempty string or nonempty list of strings")
        if len(set(quotes)) != len(quotes):
            raise ValueError("claim evidence must contain distinct quotes")
        if claim["key"] in keys:
            raise ValueError("each version may author only one claim per key")
        keys.add(claim["key"])
        references = []
        for quote in sorted(quotes):
            matching = next((c for c in archived if quote in c["text"]), None)
            if matching is None:
                raise ValueError(
                    f"claim[{index}] evidence must be an exact substring of an archived chunk"
                )
            if claim["claim"] not in matching["text"]:
                raise ValueError(
                    f"claim[{index}] text must appear in the supporting chunk; "
                    "semantic inference is not supported"
                )
            start = matching["text"].index(quote)
            references.append(
                {
                    "quote": quote,
                    "order_index": matching["order_index"],
                    "start_offset": start,
                    "end_offset": start + len(quote),
                }
            )
        valid_from = _timestamp(claim.get("valid_from"), validity=True)
        valid_until = _timestamp(claim.get("valid_until"), validity=True)
        if valid_from is not None and valid_until is not None and valid_until < valid_from:
            raise ValueError("valid_until must not precede valid_from")
        claims.append(
            {
                "key": claim["key"],
                "value": claim["value"],
                "claim": claim["claim"],
                "valid_from": valid_from,
                "valid_until": valid_until,
                **(references[0] if isinstance(evidence, str) else {"evidence": references}),
            }
        )
    return metadata, archived, sorted(claims, key=lambda claim: claim["key"])


def validate_source(path: str, content: str, metadata: dict, chunks: list[dict]) -> tuple:
    """Validate authored evidence before any write; version fingerprint identifies rejection."""
    try:
        return _prepare(metadata, chunks)
    except ValueError as exc:
        raise ClaimValidationError(
            path, hashlib.sha256(content.encode()).hexdigest(), str(exc)
        ) from exc


async def enabled(db: AsyncSession, corpus_id: str) -> bool:
    """Archive presence permanently enables history for subsequent service writes."""
    return bool(
        (
            await db.execute(
                text("SELECT 1 FROM memory_versions WHERE corpus_id = :c LIMIT 1"), {"c": corpus_id}
            )
        ).first()
    )


async def lock_corpus(db: AsyncSession, corpus_id: str) -> None:
    """Acquire the transaction-scoped memory/ingestion lock; never commit.

    All writers and snapshot creators must use this function, in the same
    transaction as their live writes. Use READ COMMITTED isolation.
    """
    lock_id = int.from_bytes(
        hashlib.sha256(f"memory:{corpus_id}".encode()).digest()[:8], "big", signed=True
    )
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    exists = await db.execute(text("SELECT 1 FROM corpora WHERE id = :c"), {"c": corpus_id})
    if exists.scalar_one_or_none() is None:
        raise ValueError("unknown corpus")


async def _latest(db, corpus_id, identity):
    result = await db.execute(
        text("""
        SELECT * FROM memory_versions WHERE corpus_id = :c AND memory_document_id = :d
        ORDER BY version_number DESC LIMIT 1
    """),
        {"c": corpus_id, "d": identity},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _insert_version(
    db,
    corpus_id,
    identity,
    previous,
    document_id,
    event,
    content=None,
    metadata=None,
    fingerprint=None,
):
    predecessor = previous["id"] if previous else None
    version_id = _hash("version", identity, predecessor, event, fingerprint)
    result = await db.execute(
        text("""
        INSERT INTO memory_versions (
            corpus_id, memory_document_id, id, predecessor_id, version_number,
            document_id, event, content, metadata, fingerprint, observed_at
        ) VALUES (:c, :d, :id, :predecessor, :number, :live, :event, :content,
                  CAST(:metadata AS jsonb), :fingerprint, clock_timestamp()) RETURNING *
    """),
        {
            "c": corpus_id,
            "d": identity,
            "id": version_id,
            "predecessor": predecessor,
            "number": previous["version_number"] + 1 if previous else 1,
            "live": document_id,
            "event": event,
            "content": content,
            "metadata": _canonical(metadata) if metadata is not None else None,
            "fingerprint": fingerprint,
        },
    )
    return dict(result.mappings().one())


async def record_version(
    db: AsyncSession,
    corpus_id: str,
    path: str,
    document_id: str,
    content: str,
    metadata: dict,
    chunks: list[dict],
) -> dict:
    """Archive NEW/MODIFIED/RESTORED or return the existing version with skipped=True.

    Change detection includes raw content and all normalized metadata, not live
    IDs or rechunking. IDs derive from identity + predecessor + event + payload.
    Evidence is validated even on an unchanged call. No commit; propagate errors.
    """
    if not isinstance(path, str) or not path or not isinstance(content, str):
        raise ValueError("path must be nonempty and content must be raw text")
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a nonempty live identifier")
    metadata, archived, claims = validate_source(path, content, metadata, chunks)
    await lock_corpus(db, corpus_id)
    identity = memory_document_id(corpus_id, path)
    previous = await _latest(db, corpus_id, identity)
    fingerprint = _hash(content, metadata)
    if previous and previous["event"] != "DELETED" and previous["fingerprint"] == fingerprint:
        return {**previous, "skipped": True}
    await db.execute(
        text("""
        INSERT INTO memory_documents (corpus_id, id, path) VALUES (:c, :d, :p)
        ON CONFLICT (corpus_id, id) DO NOTHING
    """),
        {"c": corpus_id, "d": identity, "p": path},
    )
    event = "NEW" if not previous else "RESTORED" if previous["event"] == "DELETED" else "MODIFIED"
    version = await _insert_version(
        db, corpus_id, identity, previous, document_id, event, content, metadata, fingerprint
    )
    params = {"c": corpus_id, "v": version["id"], "d": identity}
    chunk_ids = {chunk["order_index"]: _hash("chunk", version["id"], chunk) for chunk in archived}
    if archived:
        await db.execute(
            text("""
            INSERT INTO memory_chunks (corpus_id, version_id, id, text, heading_path, order_index)
            VALUES (:c, :v, :id, :text, :heading_path, :order_index)
        """),
            [{**params, **chunk, "id": chunk_ids[chunk["order_index"]]} for chunk in archived],
        )
    previous_claims = {}
    single_replacement = False
    if previous:
        result = await db.execute(
            text("""
            SELECT key, id FROM memory_claims WHERE corpus_id = :c AND version_id = :v
        """),
            {"c": corpus_id, "v": previous["id"]},
        )
        previous_claims = dict(result.all())
        single_replacement = (
            len(previous_claims) == len(claims) == 1
            and all(k.startswith("text:") for k in previous_claims)
            and claims[0]["key"].startswith("text:")
        )
    for claim in claims:
        supersedes = previous_claims.get(claim["key"])
        basis = "same_document_key" if supersedes else None
        if single_replacement:
            supersedes = next(iter(previous_claims.values()))
            basis = "single_claim_replacement"
        claim_id = _hash("claim", version["id"], claim["key"])
        await db.execute(
            text("""
            INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id,
                key, value, claim, valid_from, valid_until, supersedes_id, supersession_basis)
            VALUES (:c, :d, :v, :id, :key, CAST(:value AS jsonb), :claim,
                    :valid_from, :valid_until, :supersedes, :basis)
        """),
            {
                **params,
                **claim,
                "id": claim_id,
                "value": _canonical(claim["value"]),
                "supersedes": supersedes,
                "basis": basis,
            },
        )
        for reference in claim.get("evidence", [claim]):
            chunk_id = chunk_ids[reference["order_index"]]
            evidence_id = (
                _hash(
                    "evidence",
                    claim_id,
                    chunk_id,
                    reference["start_offset"],
                    reference["end_offset"],
                )
                if "evidence" in claim
                else _hash("evidence", claim_id)
            )
            await db.execute(
                text("""
                INSERT INTO memory_evidence (corpus_id, version_id, id, claim_id, chunk_id,
                                             quote, start_offset, end_offset)
                VALUES (:c, :v, :id, :claim_id, :chunk_id, :quote, :start_offset, :end_offset)
            """),
                {
                    **params,
                    **reference,
                    "id": evidence_id,
                    "claim_id": claim_id,
                    "chunk_id": chunk_id,
                },
            )
    return {**version, "skipped": False}


async def record_deletion(db: AsyncSession, corpus_id: str, path: str) -> bool:
    """Append a tombstone; False for unknown/already deleted paths. Never touch live rows."""
    await lock_corpus(db, corpus_id)
    identity = memory_document_id(corpus_id, path)
    previous = await _latest(db, corpus_id, identity)
    if not previous or previous["event"] == "DELETED":
        return False
    await _insert_version(db, corpus_id, identity, previous, previous["document_id"], "DELETED")
    return True


async def _load(db, corpus_id, path=None, as_of=None):
    # One statement gives every read a coherent MVCC view, even without a read lock.
    result = await db.execute(
        text("""
        SELECT v.*, d.path,
            COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.order_index)
                FROM memory_chunks c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id),
                '[]'::jsonb) AS chunks,
            COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.key, c.id)
                FROM memory_claims c WHERE c.corpus_id = v.corpus_id AND c.version_id = v.id),
                '[]'::jsonb) AS claims,
            COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)
                FROM memory_evidence e WHERE e.corpus_id = v.corpus_id AND e.version_id = v.id),
                '[]'::jsonb) AS evidence
        FROM memory_versions v JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        WHERE v.corpus_id = :c AND (CAST(:path AS text) IS NULL OR d.path = :path)
          AND (CAST(:at AS timestamptz) IS NULL OR v.observed_at <= :at)
        ORDER BY v.observed_at, d.path, v.version_number
    """),
        {"c": corpus_id, "path": path, "at": _timestamp(as_of)},
    )
    return [dict(row) for row in result.mappings()]


async def history(db: AsyncSession, corpus_id: str, path: str | None = None) -> list[dict]:
    """Chronological versions/events with raw content, metadata, chunks, claims, evidence."""
    return await _load(db, corpus_id, path)


async def changes(db: AsyncSession, corpus_id: str, path: str | None = None) -> list[dict]:
    """Version events with explicit before/after assertions and evidence."""
    versions = await history(db, corpus_id, path)
    by_id = {v["id"]: v for v in versions}
    return [
        {
            "event": v["event"],
            "version_id": v["id"],
            "path": v["path"],
            "observed_at": v["observed_at"],
            "predecessor_id": v["predecessor_id"],
            "previous_claims": by_id.get(v["predecessor_id"], {}).get("claims", []),
            "new_claims": v["claims"],
            "evidence": v["evidence"],
            "previous_evidence": by_id.get(v["predecessor_id"], {}).get("evidence", []),
        }
        for v in versions
    ]


def _query_result(versions, query_text, valid_at=None):
    latest = {}
    for version in versions:
        identity = version["memory_document_id"]
        if identity not in latest or latest[identity]["version_number"] < version["version_number"]:
            latest[identity] = version
    current_ids = {v["id"] for v in latest.values() if v["event"] != "DELETED"}
    claims, evidence_rows = [], []
    for version in versions:
        claims.extend(
            {**claim, "path": version["path"], "observed_at": version["observed_at"]}
            for claim in version["claims"]
        )
        chunk_map = {chunk["id"]: chunk for chunk in version["chunks"]}
        evidence_rows.extend(
            {
                **item,
                "path": version["path"],
                "chunk": chunk_map[item["chunk_id"]],
                "document_id": version["document_id"],
                "fingerprint": version["fingerprint"],
                "observed_at": version["observed_at"],
            }
            for item in version["evidence"]
        )
    tokens = set(re.findall(r"\w+", query_text.casefold()))

    def matches(claim):
        haystack = set(
            re.findall(
                r"\w+",
                (
                    claim["key"]
                    + " "
                    + claim["claim"]
                    + " "
                    + _canonical(claim["value"])
                    + " "
                    + claim["path"]
                ).casefold(),
            )
        )
        return not tokens or tokens <= haystack

    superseded_ids = {claim["supersedes_id"] for claim in claims if claim["supersedes_id"]}
    at = _timestamp(valid_at) if valid_at is not None else datetime.now(timezone.utc)
    for claim in claims:
        start = _timestamp(claim["valid_from"])
        end = _timestamp(claim["valid_until"])
        claim["status"] = (
            "SUPERSEDED"
            if claim["id"] in superseded_ids
            else (
                "UNCERTAIN"
                if claim["version_id"] not in current_ids
                or (start is not None and at < start)
                or (end is not None and at >= end)
                else "CURRENT"
            )
        )
        claim["validity_known"] = start is not None or end is not None
    active = [claim for claim in claims if claim["status"] == "CURRENT"]
    active_by_key: dict[str, list[dict]] = {}
    for claim in sorted(active, key=lambda c: (c["key"], c["id"])):
        active_by_key.setdefault(claim["key"], []).append(claim)
    conflicts = []
    for key_claims in active_by_key.values():
        for left, right in combinations(key_claims, 2):
            if (
                left["memory_document_id"] != right["memory_document_id"]
                and _canonical(left["value"]) != _canonical(right["value"])
                and (matches(left) or matches(right))
            ):
                left["status"] = right["status"] = "CONFLICTING"
                conflicts.append(
                    {
                        "id": _hash("conflict", left["id"], right["id"]),
                        "key": left["key"],
                        "claims": [left, right],
                    }
                )
    superseded_ids = {claim["supersedes_id"] for claim in claims if claim["supersedes_id"]}
    current = [claim for claim in active if matches(claim) and claim["status"] == "CURRENT"]
    historical = [
        claim for claim in claims if claim["version_id"] not in current_ids and matches(claim)
    ]
    uncertain = [claim for claim in claims if claim["status"] == "UNCERTAIN" and matches(claim)]
    returned_ids = {claim["id"] for claim in current + historical + uncertain}
    returned_ids.update(claim["id"] for conflict in conflicts for claim in conflict["claims"])
    return {
        "current_memories": current,
        "uncertain_memories": uncertain,
        "historical_memories": historical,
        "superseded_memories": [claim for claim in historical if claim["id"] in superseded_ids],
        "conflicts": conflicts,
        "evidence": [item for item in evidence_rows if item["claim_id"] in returned_ids],
    }


async def query(
    db: AsyncSession, corpus_id: str, query: str = "", as_of=None, valid_at=None
) -> dict:
    """Return current/historical/superseded claims, deterministic conflicts and evidence.

    Lexical AND matching on key, claim, value, and path; empty query returns all.
    See module docstring for observation-time and validity semantics.
    """
    return _query_result(await _load(db, corpus_id, as_of=as_of), query, valid_at or as_of)


async def evidence(
    db: AsyncSession, corpus_id: str, claim_id: str | None = None, version_id: str | None = None
) -> list[dict]:
    """Retrieve scoped immutable evidence, including full archived supporting chunks."""
    return await _evidence(db, corpus_id, claim_id, version_id)


async def _evidence(db, corpus_id, claim_id=None, version_id=None, evidence_id=None):
    result = await db.execute(
        text("""
        SELECT e.*, to_jsonb(c) AS chunk, d.path, v.document_id,
                       v.memory_document_id, v.fingerprint, v.observed_at
        FROM memory_evidence e
        JOIN memory_chunks c ON c.corpus_id = e.corpus_id
            AND c.version_id = e.version_id AND c.id = e.chunk_id
        JOIN memory_versions v ON v.corpus_id = e.corpus_id AND v.id = e.version_id
        JOIN memory_documents d ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        WHERE e.corpus_id = :c
          AND (CAST(:claim AS text) IS NULL OR e.claim_id = :claim)
          AND (CAST(:version AS text) IS NULL OR e.version_id = :version)
          AND (CAST(:id AS text) IS NULL OR e.id = :id)
        ORDER BY e.id
    """),
        {"c": corpus_id, "claim": claim_id, "version": version_id, "id": evidence_id},
    )
    return [dict(row) for row in result.mappings()]


async def get_evidence(db: AsyncSession, corpus_id: str, evidence_id: str) -> dict | None:
    """Retrieve one evidence record by ID; foreign-corpus IDs return None."""
    rows = await _evidence(db, corpus_id, evidence_id=evidence_id)
    return rows[0] if rows else None


async def snapshot(db: AsyncSession, corpus_id: str, as_of=None) -> dict:
    """Capture immutable version references under the writer lock, without committing.

    References include all history through the cutoff (including tombstones), so
    replay preserves historical/superseded claims as well as the current state.
    Future cutoffs are rejected: a snapshot cannot include unobserved changes.
    """
    await lock_corpus(db, corpus_id)
    observed = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    cutoff = _timestamp(as_of) if as_of is not None else observed
    if cutoff > observed:
        raise ValueError("snapshot as_of cannot be in the future")
    snapshot_id = _hash("snapshot", corpus_id, cutoff.isoformat())
    existing = await get_snapshot(db, corpus_id, snapshot_id)
    if existing:
        return existing
    await db.execute(
        text("""
        INSERT INTO memory_snapshots(corpus_id, id, observed_at, as_of)
        VALUES (:c, :id, :observed, :at)
    """),
        {"c": corpus_id, "id": snapshot_id, "observed": observed, "at": cutoff},
    )
    await db.execute(
        text("""
        INSERT INTO memory_snapshot_versions(corpus_id, snapshot_id, version_id)
        SELECT corpus_id, :id, id FROM memory_versions
        WHERE corpus_id = :c AND observed_at <= :at
    """),
        {"c": corpus_id, "id": snapshot_id, "at": cutoff},
    )
    return await get_snapshot(db, corpus_id, snapshot_id)


async def replay_snapshot(
    db: AsyncSession, corpus_id: str, snapshot_id: str, query_text: str = ""
) -> dict:
    """Resolve only the immutable references captured in this snapshot."""
    saved = await get_snapshot(db, corpus_id, snapshot_id)
    if saved is None:
        raise ValueError("snapshot not found in corpus")
    ids = set(saved["version_ids"])
    versions = [v for v in await _load(db, corpus_id, as_of=saved["as_of"]) if v["id"] in ids]
    return _query_result(versions, query_text, saved["as_of"])


async def get_snapshot(db: AsyncSession, corpus_id: str, snapshot_id: str) -> dict | None:
    """Get scoped snapshot metadata plus immutable version_ids; no live-index dependency."""
    result = await db.execute(
        text("""
        SELECT s.*, COALESCE((SELECT jsonb_agg(r.version_id ORDER BY r.version_id)
            FROM memory_snapshot_versions r WHERE r.corpus_id = s.corpus_id
            AND r.snapshot_id = s.id), '[]'::jsonb) AS version_ids
        FROM memory_snapshots s WHERE s.corpus_id = :c AND s.id = :id
    """),
        {"c": corpus_id, "id": snapshot_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None
