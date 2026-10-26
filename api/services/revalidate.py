"""Online freshness revalidation for Receipts, Memory Packs, and claim ids.

Offline verification (`memory_proof`, `memory_receipt`) asks whether an artifact
still matches the bytes it was issued over. This module asks the live question:
are the claims an artifact cited still true now, and what changed in the corpus
while the caller was working against it.

Standard library + SQLAlchemy only at the edges; the heavy memory stack is not
imported here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.corpora import get_corpus_by_name


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _corpus_id(db: AsyncSession, corpus_name: str) -> str:
    row = await get_corpus_by_name(db, corpus_name)
    if row is None:
        raise ValueError(f"corpus not found: {corpus_name}")
    return row["id"]


async def _fetch_claims(db: AsyncSession, corpus_id: str) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT c.id, c.key, c.claim, c.version_id, c.memory_document_id,
                   c.supersedes_id, v.observed_at, v.version_number, v.event, d.path
            FROM memory_claims c
            JOIN memory_versions v
              ON v.corpus_id = c.corpus_id AND v.id = c.version_id
            JOIN memory_documents d
              ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
            WHERE c.corpus_id = :c
            """),
        {"c": corpus_id},
    )
    rows = []
    for r in result.mappings():
        rows.append(
            {
                "id": r["id"],
                "key": r["key"],
                "claim": r["claim"],
                "version_id": r["version_id"],
                "memory_document_id": r["memory_document_id"],
                "supersedes_id": r["supersedes_id"],
                "observed_at": r["observed_at"],
                "version_number": r["version_number"],
                "event": r["event"],
                "path": r["path"],
            }
        )
    return rows


async def _fetch_latest_versions(
    db: AsyncSession, corpus_id: str
) -> dict[str, tuple[str, str, int]]:
    result = await db.execute(
        text("""
            SELECT memory_document_id, id, event, version_number
            FROM memory_versions
            WHERE corpus_id = :c
            """),
        {"c": corpus_id},
    )
    out: dict[str, tuple[str, str, int]] = {}
    for r in result.mappings():
        doc = r["memory_document_id"]
        vn = r["version_number"] or 0
        if doc not in out or vn > out[doc][2]:
            out[doc] = (r["id"], r["event"], vn)
    return out


def _build_graph(
    rows: list[dict], latest_versions: dict[str, tuple[str, str, int]] | None = None
) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows}
    superseded_ids = {r["supersedes_id"] for r in rows if r["supersedes_id"]}
    successors: dict[str, list[dict]] = {}
    for r in rows:
        if r["supersedes_id"]:
            successors.setdefault(r["supersedes_id"], []).append(r)

    latest_version_by_doc: dict[str, str] = {}
    latest_vn_by_doc: dict[str, int] = {}
    if latest_versions:
        for doc, (vid, _event, vn) in latest_versions.items():
            latest_vn_by_doc[doc] = vn
            latest_version_by_doc[doc] = vid
    else:
        for r in rows:
            doc = r["memory_document_id"]
            vn = r["version_number"] or 0
            if doc not in latest_vn_by_doc or vn > latest_vn_by_doc[doc]:
                latest_vn_by_doc[doc] = vn
                latest_version_by_doc[doc] = r["version_id"]

    def active(claim_id: str) -> bool:
        r = by_id.get(claim_id)
        if r is None:
            return False
        if claim_id in superseded_ids:
            return False
        doc = r["memory_document_id"]
        if r["version_id"] != latest_version_by_doc.get(doc):
            return False
        # event of the latest version for this document
        if latest_versions:
            _vid, ev, _vn = latest_versions.get(doc, (None, None, None))
            if ev == "DELETED":
                return False
        elif r["event"] == "DELETED":
            return False
        return True

    _cycle_state = {"found": False}
    _graph_holder: dict[str, Any] = {}

    def walk_heads(start_id: str) -> list[str]:
        nonlocal_cycle = _cycle_state
        visiting: set[str] = set()
        visited: set[str] = set()

        def _dfs(cid: str) -> list[str]:
            if cid in visiting:
                nonlocal_cycle["found"] = True
                return []
            if cid in visited:
                return []
            visiting.add(cid)
            succ = successors.get(cid, [])
            if not succ:
                result = [cid]
            else:
                result = []
                for s in succ:
                    result.extend(_dfs(s["id"]))
            visiting.discard(cid)
            visited.add(cid)
            return result

        raw = _dfs(start_id)
        # de-dupe preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for cid in raw:
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
        if nonlocal_cycle["found"]:
            _cycle_state["found"] = True
            _graph_holder.get("graph", {}).update(
                {
                    "cycle_detected": True,
                }
            )
        return unique

    _cycle_state.setdefault("found", False)
    _graph = {
        "rows": rows,
        "by_id": by_id,
        "superseded_ids": superseded_ids,
        "successors": successors,
        "active": active,
        "walk_heads": walk_heads,
        "latest_version_by_doc": latest_version_by_doc,
        "cycle_detected": _cycle_state["found"],
        "_cycle_state": _cycle_state,
    }
    _graph_holder["graph"] = _graph
    return _graph


def _claim_ref_row(graph: dict[str, Any], ref: str) -> dict | None:
    by_id = graph["by_id"]
    if ref in by_id:
        return by_id[ref]
    # prefix match among all claims
    matches = [r for cid, r in by_id.items() if cid.startswith(ref.lower())]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"ambiguous claim prefix: {ref}")
    return None


def _replacement_details(graph: dict[str, Any], claim_id: str) -> list[dict]:
    walk_heads = graph["walk_heads"]
    by_id = graph["by_id"]
    if claim_id not in graph["superseded_ids"]:
        return []
    terminals = walk_heads(claim_id)
    # exclude the original itself
    terminals = [t for t in terminals if t != claim_id]
    out: list[dict] = []
    seen: set[str] = set()
    for tid in terminals:
        r = by_id.get(tid)
        if r is None:
            continue
        if tid in seen:
            continue
        seen.add(tid)
        out.append(
            {
                "id": r["id"],
                "claim": r["claim"],
                "path": r["path"],
                "observed_at": _iso(r["observed_at"]),
                "key": r["key"],
            }
        )
    return out


def _classify(graph: dict[str, Any], ref: str) -> dict:
    row = _claim_ref_row(graph, ref)
    if row is None:
        return {
            "id": ref,
            "claim": None,
            "status": "missing",
            "path": None,
            "observed_at": None,
            "key": None,
            "superseded_by": [],
        }
    cid = row["id"]
    if cid in graph["superseded_ids"]:
        status = "superseded"
    elif not graph["active"](cid):
        status = "retired"
    else:
        status = "current"
    repls = _replacement_details(graph, cid) if status == "superseded" else []
    return {
        "id": cid,
        "claim": row["claim"],
        "key": row["key"],
        "status": status,
        "path": row["path"],
        "observed_at": _iso(row["observed_at"]),
        "superseded_by": repls,
    }


async def revalidate_claims(
    db: AsyncSession,
    corpus_name: str,
    claim_refs: list[str],
    *,
    include_drift: bool = False,
    valid_at: Any = None,
) -> dict:
    include_drift = True if include_drift is None else bool(include_drift)
    corpus_id = await _corpus_id(db, corpus_name)
    rows = await _fetch_claims(db, corpus_id)
    latest_versions = await _fetch_latest_versions(db, corpus_id)
    graph = _build_graph(rows, latest_versions)
    claims_out = [_classify(graph, ref) for ref in claim_refs]
    verdict = "CURRENT" if all(c["status"] == "current" for c in claims_out) else "STALE"
    checked_at = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "verdict": verdict,
        "status": verdict,
        "source_artifact": "claims",
        "artifact_type": "claims",
        "corpus": corpus_name,
        "originally_valid_at": _iso(valid_at) if valid_at is not None else None,
        "valid_at": _iso(valid_at) if valid_at is not None else None,
        "checked_at": checked_at,
        "claims": claims_out,
        "drift": None,
    }
    ts = _parse(valid_at)
    if include_drift:
        if ts is not None:
            result["drift"] = await compute_drift(db, corpus_id, ts)
        else:
            result["drift"] = {
                "status": "unavailable",
                "reason": "drift requires a baseline timestamp (valid_at) for raw claim ids",
                "since": None,
                "checked_at": checked_at,
                "counts": {"new": 0, "superseded": 0, "retired": 0},
            }
    else:
        result["drift"] = None
    return result


def _extract_receipt_claims(receipt_data: dict) -> tuple[str, Any, list[str]]:
    if not isinstance(receipt_data, dict):
        raise ValueError("receipt: missing response")

    # A full receipt bundle as written by `mindpalace receipt` carries both the
    # receipt and the response it describes.
    response = receipt_data.get("response") if "response" in receipt_data else None
    if isinstance(response, dict):
        corpus = response.get("corpus")
        state = response.get("state") or {}
        valid_at = state.get("valid_at") or state.get("as_of")
        claims: list[str] = []
        for c in response.get("current_memories") or []:
            if isinstance(c, dict) and c.get("id"):
                claims.append(c["id"])
    elif "claim_ids" in receipt_data and "corpus" in receipt_data:
        # A bare receipt bundle (e.g. `result.receipt` from the SDK/MCP) carries
        # the claim ids and corpus at the top level.
        corpus = receipt_data.get("corpus")
        state = receipt_data.get("state") or {}
        valid_at = state.get("valid_at") or state.get("as_of")
        claims = [c for c in (receipt_data.get("claim_ids") or []) if c]
    else:
        raise ValueError("receipt: missing response")

    # de-dupe
    seen: set[str] = set()
    unique: list[str] = []
    for cid in claims:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    return corpus, valid_at, unique


def _extract_pack_claims(pack_data: dict) -> tuple[str, Any, list[str]]:
    if not isinstance(pack_data, dict):
        raise ValueError("pack: not an object")
    corpus = pack_data.get("corpus")
    state = pack_data.get("state") or {}
    valid_at = state.get("valid_at") or state.get("as_of")
    claims: list[str] = []
    for field in ("current_memories", "historical_memories", "uncertain_memories"):
        for c in pack_data.get(field) or []:
            if isinstance(c, dict) and c.get("id"):
                claims.append(c["id"])
    for g in pack_data.get("conflicts") or []:
        if isinstance(g, dict):
            for c in g.get("claims") or []:
                if isinstance(c, dict) and c.get("id"):
                    claims.append(c["id"])
    # changes previous/current
    for ch in pack_data.get("changes") or []:
        if isinstance(ch, dict):
            for side in ("previous", "current"):
                for c in ch.get(side) or []:
                    if isinstance(c, dict) and c.get("id"):
                        claims.append(c["id"])
    seen: set[str] = set()
    unique: list[str] = []
    for cid in claims:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    return corpus, valid_at, unique


async def revalidate_receipt(
    db: AsyncSession,
    receipt_data: dict,
    *,
    include_drift: bool = False,
    verify_integrity: bool = True,
) -> dict:
    include_drift = True if include_drift is None else bool(include_drift)
    # Optional integrity check using memory_receipt if available.
    if verify_integrity:
        from memory_receipt import verify_response_receipt

        bundle = receipt_data.get("receipt") if isinstance(receipt_data, dict) else None
        artifact = receipt_data.get("response") if isinstance(receipt_data, dict) else None
        if isinstance(bundle, dict) and isinstance(artifact, dict):
            result = verify_response_receipt(bundle, artifact)
            if not result.get("verified", False):
                checked_at = datetime.now(timezone.utc).isoformat()
                return {
                    "verdict": "STALE",
                    "status": "INVALID_RECEIPT",
                    "rejected": True,
                    "source_artifact": "receipt",
                    "artifact_type": "receipt",
                    "corpus": (artifact.get("corpus") if isinstance(artifact, dict) else None),
                    "originally_valid_at": None,
                    "valid_at": None,
                    "checked_at": checked_at,
                    "claims": [],
                    "drift": None,
                    "integrity": result,
                }

    corpus_name, valid_at, claim_refs = _extract_receipt_claims(receipt_data)
    if not corpus_name:
        raise ValueError("receipt: missing corpus")
    res = await revalidate_claims(
        db,
        corpus_name,
        claim_refs,
        include_drift=include_drift,
        valid_at=valid_at,
    )
    res["source_artifact"] = "receipt"
    res["artifact_type"] = "receipt"
    if not include_drift:
        res["drift"] = None
    return res


async def revalidate_pack(
    db: AsyncSession,
    pack_data: dict,
    *,
    include_drift: bool = False,
) -> dict:
    include_drift = True if include_drift is None else bool(include_drift)
    corpus_name, valid_at, claim_refs = _extract_pack_claims(pack_data)
    if not corpus_name:
        raise ValueError("pack: missing corpus")
    res = await revalidate_claims(
        db,
        corpus_name,
        claim_refs,
        include_drift=include_drift,
        valid_at=valid_at,
    )
    res["source_artifact"] = "pack"
    res["artifact_type"] = "pack"
    if not include_drift:
        res["drift"] = None
    return res


async def compute_drift(
    db: AsyncSession,
    corpus_id: str,
    since: datetime,
) -> dict:
    rows = await _fetch_claims(db, corpus_id)
    latest_versions = await _fetch_latest_versions(db, corpus_id)
    graph = _build_graph(rows, latest_versions)
    checked_at = datetime.now(timezone.utc)

    new_claims: list[dict] = []
    superseded_events: list[dict] = []
    retired_events: list[dict] = []

    def _norm(text: Any) -> str:
        return " ".join(str(text or "").split()).casefold()

    for r in rows:
        obs = r["observed_at"]
        if obs is None:
            continue
        if isinstance(obs, str):
            obs = _parse(obs)
        if obs is None:
            continue
        if obs <= since:
            continue
        # Do not count re-versioned claims with identical normalized content
        # as net-new drift unless authoritative state actually changed.
        prior_identical = False
        for other in rows:
            if other["id"] == r["id"]:
                continue
            other_obs = other["observed_at"]
            if isinstance(other_obs, str):
                other_obs = _parse(other_obs)
            if other_obs is None or other_obs > since:
                continue
            if other.get("key") != r.get("key"):
                continue
            if _norm(other.get("claim")) != _norm(r.get("claim")):
                continue
            prior_identical = True
            break
        if prior_identical:
            continue
        new_claims.append(
            {
                "id": r["id"],
                "claim": r["claim"],
                "key": r["key"],
                "path": r["path"],
                "observed_at": _iso(r["observed_at"]),
            }
        )

    # superseded claims: claims that introduced a supersession edge, observed after since
    for r in rows:
        if not r["supersedes_id"]:
            continue
        obs = _parse(r["observed_at"])
        if obs is None or obs <= since:
            continue
        superseded_events.append(
            {
                "id": r["supersedes_id"],
                "claim_id": r["supersedes_id"],
                "successor_id": r["id"],
                "successor_claim": r["claim"],
                "observed_at": _iso(r["observed_at"]),
            }
        )

    # retired claims: claims that became retired after since
    # For superseded claims, retirement time = min successor observed_at.
    successors_by_target: dict[str, list[dict]] = {}
    for r in rows:
        if r["supersedes_id"]:
            successors_by_target.setdefault(r["supersedes_id"], []).append(r)
    # document deletions
    deletion_times: dict[str, datetime] = {}
    # We approximate document retirement via versions not in claims table? Use versions table.
    vres = await db.execute(
        text("""
            SELECT memory_document_id, observed_at, event
            FROM memory_versions
            WHERE corpus_id = :c AND event = 'DELETED'
            """),
        {"c": corpus_id},
    )
    for vr in vres.mappings():
        obs = _parse(vr["observed_at"])
        if obs is not None:
            deletion_times[vr["memory_document_id"]] = obs

    for cid, r in graph["by_id"].items():
        if cid in graph["superseded_ids"]:
            continue
        if graph["active"](cid):
            continue
        # determine retirement time
        rtime: datetime | None = None
        succs = successors_by_target.get(cid, [])
        if succs:
            times = [_parse(s["observed_at"]) for s in succs]
            times = [t for t in times if t is not None]
            if times:
                rtime = min(times)
        else:
            # document deletion
            rtime = deletion_times.get(r["memory_document_id"])
        if rtime is not None and rtime > since:
            retired_events.append(
                {
                    "id": cid,
                    "claim_id": cid,
                    "key": r["key"],
                    "claim": r["claim"],
                    "text": r["claim"],
                    "path": r["path"],
                    "source": r["path"],
                    "retired_at": rtime.isoformat(),
                    "observed_at": _iso(r["observed_at"]),
                }
            )

    drift = {
        "since": since.isoformat(),
        "originally_valid_at": since.isoformat(),
        "checked_at": checked_at.isoformat(),
        "new_claims": new_claims,
        "newly_recorded_claims": new_claims,
        "new": new_claims,
        "superseded_claims": superseded_events,
        "superseded": superseded_events,
        "retired_claims": retired_events,
        "retired": retired_events,
        "counts": {
            "new": len(new_claims),
            "superseded": len(superseded_events),
            "retired": len(retired_events),
        },
    }
    return drift
