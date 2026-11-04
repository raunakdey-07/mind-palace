"""Typed memory application boundary shared by REST, SDK, MCP and CLI.

Archive semantics remain in memory.py. This module owns request transactions,
projection and bounded selection; adapters must not implement those policies.
"""

from __future__ import annotations

import time
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

#: Claim authority states a caller may ask to see via include_states.
_PR1_STATES = frozenset(
    {"approved", "draft", "rejected", "superseded", "historical_observation", "inferred_insight"}
)
#: States withheld by default from current and pack.
_WITHHELD_STATES = frozenset({"draft", "rejected", "superseded"})


def _prefixes(request: MemoryRequest) -> tuple[str, ...]:
    pp = request.path_prefix
    if pp is None:
        return ()
    if isinstance(pp, str):
        pp = [pp]
    out: list[str] = []
    seen: set[str] = set()
    for p in pp:
        if isinstance(p, str) and p.endswith("/"):
            p = p[:-1]
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def _in_scope(path: str, prefixes: tuple[str, ...]) -> bool:
    for p in prefixes:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _validate(operation: str, request: MemoryRequest) -> None:
    if operation not in OPERATIONS:
        raise MemoryError("invalid_operation", "Unknown memory operation", 422)
    required = {"as-of": "as_of", "evidence": "claim_id", "replay": "snapshot_id"}
    if operation in required and getattr(request, required[operation]) is None:
        raise MemoryError("invalid_request", f"{required[operation]} is required", 422)
    allowed = {"corpus", "query"}
    allowed |= {
        "current": {"path", "path_prefix", "valid_at", "include_states"},
        "as-of": {"path", "as_of", "valid_at"},
        "history": {"path", "path_prefix", "as_of", "valid_at"},
        "changes": {"path", "as_of", "valid_at"},
        "evidence": {"claim_id", "as_of", "valid_at"},
        "snapshot": {"as_of"},
        "replay": {"snapshot_id", "path"},
        "pack": {
            "path",
            "path_prefix",
            "as_of",
            "valid_at",
            "snapshot_id",
            "budget",
            "include_states",
        },
        "query": {
            "path",
            "path_prefix",
            "as_of",
            "valid_at",
            "snapshot_id",
            "budget",
            "intent",
            "include_receipt",
        },
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
    if request.include_states is not None:
        bad = [s for s in request.include_states if s not in _PR1_STATES]
        if bad:
            raise MemoryError(
                "invalid_state",
                f"unknown state(s) in include_states: {', '.join(sorted(bad))}",
                422,
            )
    if request.path is not None and request.path_prefix is not None:
        raise MemoryError("path_conflict", "path and path_prefix cannot both be set", 422)
    if request.path_prefix is not None:
        prefixes = _prefixes(request)
        if not prefixes or any(p == "" for p in prefixes):
            raise MemoryError("invalid_request", "path_prefix must not be empty", 422)
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


def _withhold(response: MemoryResponse, include_states: list[str] | None = None) -> None:
    """Remove draft, rejected and superseded claims from current and pack responses.

    Mutates the response in place. history and query are never passed here, so
    they are unchanged. `include_states` names states the caller wants to keep;
    they are not withheld. A conflict group reduced to a single claim promotes
    that claim to current_memories; a change emptied on both sides is dropped.
    Rebuilds evidence and sources so withheld text appears nowhere in the body,
    and sets withheld_counts when anything was withheld.
    """
    include = set(include_states or [])
    withheld: dict[str, set[str]] = {state: set() for state in _WITHHELD_STATES}
    for claim in _claims(response):
        if claim.info_state in _WITHHELD_STATES and claim.info_state not in include:
            withheld[claim.info_state].add(claim.id)

    for field in ("current_memories", "historical_memories", "uncertain_memories"):
        kept = [
            c
            for c in getattr(response, field)
            if c.info_state not in _WITHHELD_STATES or c.info_state in include
        ]
        setattr(response, field, kept)

    promoted: list[Claim] = []
    kept_conflicts: list[Conflict] = []
    for group in response.conflicts:
        kept = [
            c
            for c in group.claims
            if c.info_state not in _WITHHELD_STATES or c.info_state in include
        ]
        if len(kept) >= 2:
            kept_conflicts.append(Conflict(id=group.id, key=group.key, claims=kept))
        elif len(kept) == 1:
            promoted.append(kept[0])
    response.conflicts = kept_conflicts
    if promoted:
        existing = {c.id for c in response.current_memories}
        response.current_memories.extend(c for c in promoted if c.id not in existing)

    kept_changes: list[Change] = []
    for change in response.changes:
        prev = [
            c
            for c in change.previous
            if c.info_state not in _WITHHELD_STATES or c.info_state in include
        ]
        curr = [
            c
            for c in change.current
            if c.info_state not in _WITHHELD_STATES or c.info_state in include
        ]
        if not prev and not curr:
            continue
        kept_changes.append(
            Change(
                version_id=change.version_id,
                predecessor_id=change.predecessor_id,
                event=change.event,
                path=change.path,
                observed_at=change.observed_at,
                memory_changed=change.memory_changed,
                relationship=change.relationship,
                previous=prev,
                current=curr,
            )
        )
    response.changes = kept_changes

    _attach(response, {e.id: e for e in response.evidence})

    counts = {state: len(ids) for state, ids in withheld.items()}
    if any(counts.values()):
        response.withheld_counts = counts


def _scope_conflicts(response: MemoryResponse, prefixes: tuple[str, ...]) -> None:
    """Keep only the in-scope sides of each conflict group.

    Markdown claim keys come from the heading path, not the file path, so two
    documents built from one template (e.g. two campaign briefs) get the same
    keys and conflict with each other. A scoped pack must not show the other
    campaign's headline as "the other side".

    A group with 2+ in-scope sides is kept with those sides. A group reduced
    to one in-scope side promotes that claim into current_memories. A group
    with none is dropped. Evidence and sources are rebuilt for the surviving
    claims.
    """
    promoted: list[Claim] = []
    kept_conflicts: list[Conflict] = []
    for group in response.conflicts:
        kept = [c for c in group.claims if _in_scope(c.path, prefixes)]
        if len(kept) >= 2:
            kept_conflicts.append(Conflict(id=group.id, key=group.key, claims=kept))
        elif len(kept) == 1:
            promoted.append(kept[0])
    response.conflicts = kept_conflicts
    if promoted:
        existing = {c.id for c in response.current_memories}
        response.current_memories.extend(c for c in promoted if c.id not in existing)
    _attach(response, {e.id: e for e in response.evidence})


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
    """Project one coherent archive view without exposing database rows or raw files.

    `Claim` objects are built from `_query_result`'s complete claim list rather
    than from the four buckets it returns. A claim the buckets do not classify
    still has to be representable, because `changes` walks version contents and a
    claim reachable only as "superseded" -- which is what a cross-document
    supersession leaves behind -- would otherwise raise KeyError on every read of
    the corpus.
    """
    resolved = memory._query_result(versions, "", state.valid_at or state.as_of)
    matched = (
        resolved
        if not request.query
        else memory._query_result(
            versions,
            request.query,
            state.valid_at or state.as_of,
            # A history read asks about a chain of claims, so the question is
            # resolved against the supersession graph: a claim that was retired
            # is found together with the claim that retired it, whichever one
            # the question happened to name. Every other operation asks which
            # claim answers the question, and there the answer is one claim.
            lineage=operation in {"history", "changes"},
        )
    )
    raw_claims = {c["id"]: c for c in resolved["all_claims"]}
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
                    "info_state",
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
    elif request.path_prefix is not None:
        prefixes = _prefixes(request)
        matched_ids = {cid for cid in matched_ids if _in_scope(claims[cid].path, prefixes)}
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
            # Cross-document supersession leaves no version predecessor, because
            # the retiring document is a new document rather than a new version of
            # the old one. Surface the retired claims as the "before" half so a
            # reader sees the supersession instead of a bare "recorded" line.
            if not before:
                seen_targets: set[str] = set()
                for claim in after:
                    target_id = getattr(claim, "supersedes_id", None)
                    if target_id and target_id in claims and target_id not in seen_targets:
                        seen_targets.add(target_id)
                        before.append(claims[target_id])
            if request.path and v["path"] != request.path:
                continue
            if request.path_prefix is not None and not _in_scope(v["path"], _prefixes(request)):
                continue
            if operation in {"history", "changes"}:
                # A version can carry several claims; only the ones on the matched
                # claim's own lineage belong in this timeline. Filtering here keeps
                # an unrelated sibling claim from masquerading as the transition
                # just because it sits at ordinal 0 of the superseding document.
                after = [c for c in after if c.id in matched_ids]
                targets = {c.supersedes_id for c in after if c.supersedes_id}
                before = [c for c in before if c.id in matched_ids or c.id in targets]
            if request.query and not any(c.id in matched_ids for c in before + after):
                continue
            if not before and not after:
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
    if operation in {"history", "pack", "changes"}:
        # A projection that came back with claims or events found something.
        # The default status says "abstained", which is the one word on the wire
        # that means nothing was found, so a caller reading the status instead
        # of the claims is told the opposite of what happened.
        response.match_status = (
            "confident" if (response.changes or _claims(response)) else "abstained"
        )
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
        withheld_counts=full.withheld_counts,
        # The ranking that produced this selection, not the pack that survived
        # it. Truncating the shortlist would hide exactly the near-misses the
        # caller needs in order to notice that the budget cost them the answer.
        match_status=full.match_status,
        match_margin=full.match_margin,
        candidates=list(full.candidates),
        unmatched_subparts=list(full.unmatched_subparts),
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


#: Operations that write. Must agree with `api.routers.memory.WRITE_OPERATIONS`
#: and `mcp_server.WRITE_OPERATIONS`; three lists is already too many, so this is
#: the one the shared core uses.
WRITE_OPERATIONS = frozenset({"snapshot"})


async def execute_in_session(db, operation: str, request: MemoryRequest, vectorized: bool = False):
    """Execute in caller's transaction (also used by integration tests).

    Public callers use execute(); ingestion retains its existing transaction.
    Reads load one coherent MVCC view. Snapshot creation uses the writer lock.
    """
    _validate(operation, request)
    # `write=True` for write operations. The REST and MCP adapters each applied
    # this themselves, so the SDK and the local runtime -- which arrive here
    # directly -- resolved every corpus as a reader. A `read_only` credential
    # could therefore create a snapshot through the SDK while the identical REST
    # call returned 403.
    corpus = await get_corpus_by_name(db, request.corpus, write=operation in WRITE_OPERATIONS)
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
    # The public projection reads only each chunk's id and heading, never its
    # text, and chunk text is the bulk of the payload. It also reads no version
    # content or metadata: authority comes from claims, evidence and the version
    # graph. The raw history surface still returns all of it.
    versions = await memory._load(
        db, corpus["id"], as_of=cutoff, chunk_text=False, version_text=False
    )
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
            versions,
            request.model_copy(update={"query": "", "path": None}),
            "pack",
            state,
            saved,
        )
        result = await query(full, request, db=db, corpus_id=corpus["id"], vectorized=vectorized)
        if request.path_prefix is not None:
            _scope_conflicts(result, _prefixes(request))
        return _with_receipt(result, request)
    result = project(versions, request, operation, state, saved)
    if request.path_prefix is not None:
        _scope_conflicts(result, _prefixes(request))
    if operation in {"current", "pack"}:
        _withhold(result, request.include_states)
    result = bounded_pack(result, request.budget) if operation == "pack" else result
    return _with_receipt(result, request)


def _with_receipt(result, request):
    """Attach the canonical receipt when the caller asked for one.

    The single attachment point for every public surface, so REST, SDK and MCP
    cannot drift into returning different receipts for the same answer. Returns
    the response unchanged when no receipt was requested, or when there is no
    current memory to attest to.
    """
    if not getattr(request, "include_receipt", False):
        return result
    from api.services import telemetry
    from memory_receipt import receipt_for_response

    started = time.perf_counter()
    with telemetry.span(
        "memory.receipt", claims=len(getattr(result, "current_memories", []) or [])
    ) as active:
        receipt = receipt_for_response(result)
        if receipt is not None:
            result.receipt = receipt
        # No digest attribute: it is a stable fingerprint of the user's entire
        # memory state, and a trace backend is not where that belongs.
        if active is not None:
            active.set_attribute("issued", receipt is not None)
            active.set_attribute("duration_ms", (time.perf_counter() - started) * 1000)
    return result


async def execute(operation: str, request: MemoryRequest) -> MemoryResponse:
    """Canonical public entry point, with transaction ownership and sanitized errors."""
    _validate(operation, request)
    from api.services import telemetry
    from api.services.query_log import Call

    started = time.perf_counter()
    # One recorder for every surface. REST, SDK, MCP, the CLI and the local
    # runtime all reach this function, so instrumenting it here is what makes the
    # log a property of the product rather than of whichever adapter remembered
    # to call something.
    #
    # The operation is the one the caller asked for, not a constant. Hardcoding
    # "query" filed a `history` read under `query`, so two weeks of feedback
    # could not tell the operations apart and a verdict on a history question
    # landed against a row that looked like a ranked recall.
    call = Call(operation, request.query, corpus=request.corpus)
    with telemetry.span(
        f"memory.{operation}",
        corpus=request.corpus,
        query_chars=len(request.query),
        as_of=bool(request.as_of),
        intent=request.intent,
    ) as active:
        try:
            async with session_scope() as db:
                async with db.begin():
                    result = await execute_in_session(db, operation, request)
        except MemoryError:
            raise
        except (DBAPIError, SQLAlchemyError, OSError, TimeoutError) as exc:
            raise translate_database_error(exc) from exc
        # Attributes are set inside the block on purpose: OpenTelemetry drops
        # attributes set on a span that has already ended.
        if active is not None:
            # Sizes and counts only. The answer itself is memory content, and it
            # must not be copied into a trace.
            active.set_attribute("result.claims", len(result.current_memories))
            active.set_attribute("result.changes", len(result.changes))
            active.set_attribute("result.chars", len(result.canonical_json()))
            active.set_attribute("result.receipt", result.receipt is not None)
            active.set_attribute("duration_ms", (time.perf_counter() - started) * 1000)
    call.log(**_logged_outcome(result))
    return result


def _logged_outcome(result) -> dict:
    """What the flight recorder should say about one read.

    `candidates`, `match_status` and `match_margin` are the ranking signal, and
    only the ranked `query` path produces them. `history`, `changes`, `pack` and
    `replay` return a timeline and have no ranking to report, so reading them off
    a response that never set them logged a successful history read as an
    abstention: `match_status: "abstained"` and `returned_claim_ids: []` beside a
    claim, a `NEW` event and thirteen evidence items. Grading is by `query_id`, so
    that row would have taken a verdict filed against a failed recall.

    So the claim ids come from the response itself when there is no ranking to
    read, and `abstained` means what it says: nothing came back. A non-ranked read
    that returned claims is recorded `confident`, which is the only other value
    the schema allows that is not a claim about a ranking that was never computed.

    The ids are the claims that were returned, not the ranking's shortlist. The
    two are not the same set: the shortlist carries one id per key, and a
    response of three claims can rank behind two keys the caller never saw. A
    row that said "one claim" beside a response that displayed three was a
    recording of the ranking, not of the answer, and grading the answer from it
    is grading the wrong thing.
    """
    claims = [
        *(result.current_memories or []),
        *(result.historical_memories or []),
        *(result.uncertain_memories or []),
    ]
    status = result.match_status
    if status == "abstained" and (claims or result.changes):
        status = "confident"
    return {
        "match_status": status,
        "match_margin": result.match_margin,
        "top_scores": [c.score for c in (result.candidates or [])],
        "returned_claim_ids": [c.id for c in claims],
    }
