"""Typed memory application boundary shared by REST, SDK, MCP and CLI.

Archive semantics remain in memory.py. This module owns request transactions,
projection and bounded selection; adapters must not implement those policies.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from api.models.memory import (
    Change,
    Claim,
    Conflict,
    Evidence,
    MemoryRequest,
    MemoryResponse,
    Snapshot,
    Source,
    State,
)
from api.services import memory
from api.services.corpora import get_corpus_by_name
from api.services.db import session_scope


class MemoryError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def translate_database_error(exc: Exception) -> MemoryError:
    """Return a sanitized public error for a database or transport failure."""
    if isinstance(exc, DBAPIError):
        code = getattr(exc.orig, "sqlstate", None)
        if code in {"42P01", "42703"}:
            return MemoryError(
                "memory_unavailable", "Memory schema unavailable; apply migrations", 503
            )
        return MemoryError("database_unavailable", "Memory database request failed", 503)
    return MemoryError("database_unavailable", "Memory database unavailable", 503)


OPERATIONS = frozenset(
    {"current", "history", "changes", "evidence", "as-of", "snapshot", "replay", "pack", "query"}
)


def _validate(operation: str, request: MemoryRequest) -> None:
    if operation not in OPERATIONS:
        raise MemoryError("invalid_operation", "Unknown memory operation", 422)
    required = {"as-of": "as_of", "evidence": "claim_id", "replay": "snapshot_id"}
    if operation in required and getattr(request, required[operation]) is None:
        raise MemoryError("invalid_request", f"{required[operation]} is required", 422)
    allowed = {"corpus", "query"}
    allowed |= {
        "current": {"path", "valid_at"},
        "as-of": {"path", "as_of", "valid_at"},
        "history": {"path", "as_of", "valid_at"},
        "changes": {"path", "as_of", "valid_at"},
        "evidence": {"claim_id", "as_of", "valid_at"},
        "snapshot": {"as_of"},
        "replay": {"snapshot_id", "path"},
        "pack": {"path", "as_of", "valid_at", "snapshot_id", "budget"},
        "query": {"path", "as_of", "valid_at", "snapshot_id", "budget", "intent"},
    }[operation]
    # SDK sends default fields too; reject non-default unsupported selectors.
    for name in type(request).model_fields:
        value = getattr(request, name)
        if (
            name not in allowed
            and value is not None
            and value != type(request).model_fields[name].default
        ):
            raise MemoryError("invalid_request", f"{name} is not supported for {operation}", 422)
    if request.snapshot_id and (request.as_of or request.valid_at):
        raise MemoryError("invalid_request", "Snapshot replay fixes both temporal cutoffs", 422)
    if operation == "snapshot" and request.query:
        raise MemoryError(
            "invalid_request", "Snapshots capture the entire corpus, not a query", 422
        )


def _claims(response: MemoryResponse) -> list[Claim]:
    return (
        response.current_memories
        + response.historical_memories
        + response.uncertain_memories
        + [c for group in response.conflicts for c in group.claims]
        + [c for change in response.changes for c in change.previous + change.current]
    )


def _attach(response: MemoryResponse, evidence: dict[str, Evidence]) -> None:
    ids = {eid for c in _claims(response) for eid in c.evidence_ids}
    response.evidence = [evidence[eid] for eid in sorted(ids)]
    sources = {
        e.version_id: Source(
            document_id=e.document_id,
            version_id=e.version_id,
            path=e.path,
            source_hash=e.source_hash,
            observed_at=e.observed_at,
        )
        for e in response.evidence
    }
    response.sources = [sources[key] for key in sorted(sources)]


def _semantic(claims: list[Claim]) -> list[str]:
    return sorted(
        memory._canonical(
            {
                "key": c.key,
                "value": c.value,
                "claim": c.claim,
                "valid_from": c.valid_from,
                "valid_until": c.valid_until,
            }
        )
        for c in claims
    )


def project(
    versions: list[dict],
    request: MemoryRequest,
    operation: str,
    state: State,
    saved: dict | None = None,
) -> MemoryResponse:
    """Project one coherent archive view without exposing database rows or raw files."""
    resolved = memory._query_result(versions, "", state.valid_at or state.as_of)
    matched = (
        resolved
        if not request.query
        else memory._query_result(versions, request.query, state.valid_at or state.as_of)
    )
    raw_claims = {
        c["id"]: c
        for key in ("current_memories", "historical_memories", "uncertain_memories")
        for c in resolved[key]
    }
    raw_claims.update({c["id"]: c for g in resolved["conflicts"] for c in g["claims"]})
    evidence = {
        e["id"]: Evidence(
            id=e["id"],
            claim_id=e["claim_id"],
            version_id=e["version_id"],
            document_id=e["document_id"],
            path=e["path"],
            source_hash=e["fingerprint"],
            chunk_id=e["chunk_id"],
            heading=e["chunk"]["heading_path"],
            text=e["quote"],
            start_offset=e["start_offset"],
            end_offset=e["end_offset"],
            observed_at=e["observed_at"],
        )
        for e in resolved["evidence"]
    }
    by_claim: dict[str, list[str]] = {}
    for item in evidence.values():
        by_claim.setdefault(item.claim_id, []).append(item.id)
    claims = {}
    for cid, c in raw_claims.items():
        if not by_claim.get(cid):
            raise MemoryError("memory_unavailable", "Archived claim has incomplete provenance", 503)
        claims[cid] = Claim(
            **{
                k: c[k]
                for k in (
                    "id",
                    "key",
                    "value",
                    "claim",
                    "status",
                    "version_id",
                    "path",
                    "observed_at",
                    "valid_from",
                    "valid_until",
                    "supersedes_id",
                )
            },
            evidence_ids=sorted(by_claim[cid]),
        )
    matched_ids = {
        c["id"]
        for key in ("current_memories", "historical_memories", "uncertain_memories")
        for c in matched[key]
    }
    matched_ids.update(c["id"] for g in matched["conflicts"] for c in g["claims"])
    if request.path:
        matched_ids = {cid for cid in matched_ids if claims[cid].path == request.path}
    if request.claim_id:
        if request.claim_id not in claims:
            raise MemoryError("claim_not_found", "Claim not found in this corpus/state", 404)
        matched_ids = {request.claim_id}
    response = MemoryResponse(query=request.query, corpus=request.corpus, state=state)
    if saved:
        response.snapshot = Snapshot(**{k: saved[k] for k in Snapshot.model_fields})
    for field in ("current_memories", "historical_memories", "uncertain_memories"):
        if operation in {"current", "as-of"} and field != "current_memories":
            continue
        if operation == "changes":
            continue
        setattr(
            response, field, [claims[c["id"]] for c in matched[field] if c["id"] in matched_ids]
        )
    if operation == "evidence":
        # A conflicting claim belongs in its conflict group, not in CURRENT.
        for field in ("current_memories", "historical_memories", "uncertain_memories"):
            setattr(
                response, field, [c for c in getattr(response, field) if c.id == request.claim_id]
            )

    if operation in {"history", "changes", "pack", "snapshot", "replay"}:
        by_version = {v["id"]: v for v in versions}
        for v in versions:
            prior = by_version.get(v["predecessor_id"], {})
            before = [claims[c["id"]] for c in prior.get("claims", [])]
            after = [claims[c["id"]] for c in v["claims"]]
            if request.path and v["path"] != request.path:
                continue
            if request.query and not any(c.id in matched_ids for c in before + after):
                continue
            changed = _semantic(before) != _semantic(after)
            linked = any(c.supersedes_id in {p.id for p in before} for c in after)
            response.changes.append(
                Change(
                    version_id=v["id"],
                    predecessor_id=v["predecessor_id"],
                    event=v["event"],
                    path=v["path"],
                    observed_at=v["observed_at"],
                    memory_changed=changed,
                    relationship=(
                        "SUPERSEDES"
                        if changed and linked
                        else "DOCUMENT_MODIFIED" if v["event"] == "MODIFIED" else "LIFECYCLE"
                    ),
                    previous=before,
                    current=after,
                )
            )
    # Select complete connected alternatives, including conflicts introduced by
    # before/after claim sets. Path/query selectors must never hide a counter-source.
    conflict_ids = matched_ids | {c.id for c in _claims(response)}
    while True:
        connected = [
            g for g in resolved["conflicts"] if any(c["id"] in conflict_ids for c in g["claims"])
        ]
        expanded = conflict_ids | {c["id"] for g in connected for c in g["claims"]}
        if expanded == conflict_ids:
            break
        conflict_ids = expanded
    response.conflicts = [
        Conflict(id=g["id"], key=g["key"], claims=[claims[c["id"]] for c in g["claims"]])
        for g in connected
    ]
    _attach(response, evidence)
    return response


def bounded_pack(full: MemoryResponse, budget: int) -> MemoryResponse:
    """Greedy stable selection; claims, evidence and conflict sides are atomic units.

    Count Unicode characters of the COMPLETE canonical JSON. Never truncate source
    quotes or return dangling evidence references. The truncated flag is reserved
    before selection, so even changing the flag cannot overflow the budget.
    """
    result = MemoryResponse(
        query=full.query,
        corpus=full.corpus,
        state=full.state,
        # Absence is a stated answer. It must survive selection, or a bounded
        # pack looks identical to an empty envelope and a consumer cannot tell
        # "nothing relevant" from "nothing sent".
        constraints=list(full.constraints),
        truncated=True,
    )
    if len(result.canonical_json()) > budget:
        raise MemoryError("invalid_budget", "Budget cannot fit the response envelope", 422)
    evidence = {e.id: e for e in full.evidence}
    dropped = False
    for field in (
        "current_memories",
        "changes",
        "historical_memories",
        "conflicts",
        "uncertain_memories",
    ):
        for item in getattr(full, field):
            # Selection treats nested items as read-only. Isolate only the lists
            # we append to; _attach replaces evidence/sources rather than mutating them.
            candidate = result.model_copy()
            setattr(candidate, field, list(getattr(result, field)))
            candidate.conflicts = list(result.conflicts)
            if item not in getattr(candidate, field):
                getattr(candidate, field).append(item)
            # A change can mention a conflicting claim before the conflicts section
            # is selected. Close over all overlapping conflicts, or drop the unit;
            # otherwise a small pack could silently expose only one alternative.
            while True:
                claim_ids = {c.id for c in _claims(candidate)}
                missing = [
                    g
                    for g in full.conflicts
                    if g not in candidate.conflicts and any(c.id in claim_ids for c in g.claims)
                ]
                if not missing:
                    break
                candidate.conflicts.extend(missing)
            selected_conflicts = {g.id for g in candidate.conflicts}
            candidate.conflicts = [g for g in full.conflicts if g.id in selected_conflicts]
            _attach(candidate, evidence)
            if len(candidate.canonical_json()) <= budget:
                result = candidate
            else:
                dropped = True
    if not dropped:
        result.truncated = False
        if len(result.canonical_json()) > budget:
            result.truncated = True
    return result


async def execute_in_session(db, operation: str, request: MemoryRequest) -> MemoryResponse:
    """Execute in caller's transaction (also used by integration tests).

    Public callers use execute(); ingestion retains its existing transaction.
    Reads load one coherent MVCC view. Snapshot creation uses the writer lock.
    """
    _validate(operation, request)
    corpus = await get_corpus_by_name(db, request.corpus)
    if corpus is None:
        raise MemoryError("corpus_not_found", "Corpus not found", 404)
    if operation == "query":
        from api.services.memory_query import interpret

        interpreted = interpret(request)
        request = request.model_copy(
            update={"as_of": interpreted.as_of, "intent": interpreted.name}
        )
    saved = None
    cutoff = request.as_of
    if operation == "snapshot":
        try:
            saved = await memory.snapshot(db, corpus["id"], cutoff)
        except ValueError as exc:
            raise MemoryError("invalid_timestamp", str(exc), 422) from exc
    elif request.snapshot_id:
        saved = await memory.get_snapshot(db, corpus["id"], request.snapshot_id)
        if saved is None:
            raise MemoryError("snapshot_not_found", "Snapshot not found in this corpus", 404)
    if saved:
        cutoff = saved["as_of"]
    # Fix the validity clock once for the entire response, including conflicts.
    valid_at = cutoff if saved else request.valid_at or cutoff or datetime.now(timezone.utc)
    versions = await memory._load(db, corpus["id"], as_of=cutoff)
    if saved:
        ids = set(saved["version_ids"])
        versions = [v for v in versions if v["id"] in ids]
    if request.path and not any(v["path"] == request.path for v in versions):
        raise MemoryError("document_not_found", "Document not found in this corpus/state", 404)
    state = State(as_of=cutoff, valid_at=valid_at, snapshot=saved["id"] if saved else None)
    if operation == "query":
        from api.services.memory_query import query

        # Resolve the COMPLETE scoped state before applying any relevance filter.
        full = project(
            versions, request.model_copy(update={"query": "", "path": None}), "pack", state, saved
        )
        return await query(full, request)
    result = project(versions, request, operation, state, saved)
    return bounded_pack(result, request.budget) if operation == "pack" else result


async def execute(operation: str, request: MemoryRequest) -> MemoryResponse:
    """Canonical public entry point, with transaction ownership and sanitized errors."""
    _validate(operation, request)
    try:
        async with session_scope() as db:
            async with db.begin():
                return await execute_in_session(db, operation, request)
    except MemoryError:
        raise
    except (DBAPIError, SQLAlchemyError, OSError, TimeoutError) as exc:
        raise translate_database_error(exc) from exc
