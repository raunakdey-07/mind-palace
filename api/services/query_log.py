# SPDX-License-Identifier: Apache-2.0
"""An append-only log of what was asked and what came back, for grading real use.

Synthetic benchmarks measure the questions someone thought of. They cannot
measure the ones a developer's agent actually asks, and they drift the moment the
corpus changes underneath them. This records the real traffic instead: every
`recall` and `remember`, with the ranking it produced and how long it took, so the
verdicts come from the caller instead of from a grader with opinions.

`mindpalace feedback <query_id> --verdict right|wrong|abstained_wrongly --note ...`
appends a second line pointing at the first. Two weeks of that is a graded
sample of actual usage, which no offline suite can substitute for.

## What is recorded, and what is not

Recorded: timestamp, operation, the query, `match_status`, the top three scores,
the claim ids returned, and latency.

Not recorded: the claim text, the source path, the corpus, or anything about the
document. A log that survives on a developer's laptop is a place memory content
ends up by accident, and this file sits in the same home directory as the database
it describes. The claim ids are 64 hex characters; on their own they carry no
content, and `--verify` a receipt with one in hand.

## When it runs

On by default in local development, off by default everywhere else, and off
entirely when `MIND_PALACE_QUERY_LOG=0`. "Local development" means a loopback
connection with no tenant scope configured, which is the single-user local mode
and nothing else. A server deployment writes nothing unless it is asked to.

Writes are best-effort and never raise into a query. A full disk must not turn a
recall into a 500, and a log that can fail a query does not get written.

## Format

One JSON object per line, append only. Each carries a `query_id`, which is the
first 12 characters of a SHA-256 over the corpus, operation, query and start
time: stable enough to quote in a bug report, and different for two identical
questions asked a second apart, so a retry is visible as two rows rather than
silently merged into one.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: How many top scores a row carries. Three is what `candidates` reports, and a
#: fourth number nobody reads is not more information.
TOP_SCORES = 3

#: Cap on the query text stored verbatim. A query is the one part of the row that
#: is the caller's own words rather than memory content, and it still gets a bound.
QUERY_LIMIT = 512


def default_path() -> Path:
    """Where the log lives. Overridable with `MIND_PALACE_QUERY_LOG`."""
    override = os.getenv("MIND_PALACE_QUERY_LOG")
    if override and override != "1" and override.lower() != "true":
        return Path(override).expanduser()
    return (
        Path(os.getenv("MIND_PALACE_DATA_DIR", "~/.mind-palace")).expanduser()
        / "logs"
        / "queries.jsonl"
    )


def enabled(tenant_scoped: bool) -> bool:
    """Whether this process should write rows.

    A scoped connection is a server deployment. Logging there is somebody's job
    with somebody's retention policy, not a default this module gets to choose.
    """
    setting = os.getenv("MIND_PALACE_QUERY_LOG")
    if setting is not None:
        return setting not in ("0", "false", "off", "")
    return not tenant_scoped


def query_id(corpus: str, operation: str, query: str, started: float) -> str:
    """A short, quotable handle for one call.

    Over the start time as well as the content, so a retry after a timeout shows
    up as a second row. Two rows with the same question are a fact about the
    traffic; one row would hide it.
    """
    seed = "\x00".join([corpus, operation, query, f"{started:.6f}"])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def record(
    *,
    operation: str,
    query: str,
    started: float,
    corpus: str = "",
    match_status: str = "abstained",
    top_scores: list[float] | None = None,
    returned_claim_ids: list[str] | None = None,
    match_margin: float | None = None,
    note: str = "",
) -> str | None:
    """Append one row. Returns its `query_id`, or None if nothing was written.

    Every failure here is silent on purpose, with one exception: a failure to
    write must not reach the caller, because the caller already has its answer
    and a log is not part of it.
    """
    identifier = query_id(corpus, operation, query, started)
    row: dict[str, Any] = {
        "query_id": identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "operation": operation,
        "query": query[:QUERY_LIMIT],
        "match_status": match_status,
        "top_scores": [round(float(s), 6) for s in (top_scores or [])[:TOP_SCORES]],
        "returned_claim_ids": list(returned_claim_ids or []),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if match_margin is not None:
        row["match_margin"] = round(float(match_margin), 6)
    if note:
        row["note"] = note
    try:
        path = default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except (OSError, ValueError):
        # `OSError` covers a full disk, a read-only home and a missing parent.
        # `ValueError` covers a path Python itself rejects -- `mkdir` raises it,
        # not `OSError`, on an embedded NUL byte -- so an unusual path would
        # otherwise become an exception in the middle of somebody's query.
        return None
    return identifier


def feedback(identifier: str, verdict: str, note: str = "") -> dict[str, Any] | None:
    """Grade a logged call. Returns the appended row, or None if it was not logged.

    A verdict on a row that does not exist is refused rather than recorded
    against nothing: over two weeks, feedback that silently lands nowhere looks
    exactly like feedback that says nothing is wrong.
    """
    if verdict not in ("right", "wrong", "abstained_wrongly"):
        raise ValueError(
            "verdict must be one of right, wrong, abstained_wrongly; " f"got {verdict!r}"
        )
    path = default_path()
    if not path.exists():
        return None
    target = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("query_id") == identifier:
            target = row
    if target is None:
        return None
    stamped = {
        "query_id": identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "operation": target.get("operation", ""),
        "query": target.get("query", ""),
        "kind": "feedback",
        "verdict": verdict,
        "note": note[:QUERY_LIMIT],
        "original_match_status": target.get("match_status", ""),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stamped, sort_keys=True) + "\n")
    return stamped


def tenant_scoped_now() -> bool:
    """Whether this connection is inside the tenant boundary right now.

    The local runtime and the local CLI open a connection with no tenant scope,
    which is the single-user development mode. A server sets one.
    """
    try:
        return bool(os.getenv("MIND_PALACE_TENANT_SCOPE"))
    except Exception:  # noqa: BLE001 - a probe about the environment never raises
        return False


class Call:
    """One timed call, logged when it ends. `with Call(...) as call:` .

    A context manager rather than a pair of functions, because the failure case
    matters: an operation that raises still has to be recorded, and a
    try/finally at every call site is twenty chances to get it wrong.
    """

    def __init__(self, operation: str, query: str, corpus: str = "", active: bool = True):
        self.operation = operation
        self.query = query
        self.corpus = corpus
        self.started = time.perf_counter()
        self.active = active and enabled(tenant_scoped_now())
        self.query_id: str | None = None

    def __enter__(self) -> "Call":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.log()
        return False

    def log(self, **fields) -> None:
        if not self.active:
            return
        self.query_id = record(
            operation=self.operation,
            query=self.query,
            corpus=self.corpus,
            started=self.started,
            **fields,
        )


def session_id() -> str:
    """Per-process id, so rows from separate sessions can be told apart."""
    return uuid.uuid4().hex[:8]
