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
using the first occurrence in the lowest-order archived chunk that holds both the
quote and the claim's own wording. Every supporting chunk must also contain the
claim text. All references are authored atomically with the version; there is no
service for appending evidence later.

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

from api.services import similarity


class ClaimValidationError(ValueError):
    """Validation failure with identifiers, without exposing source content in logs."""

    def __init__(self, path: str, version_id: str, reason: str):
        self.path = path
        self.version_id = version_id
        self.reason = reason
        super().__init__(f"document={path} version={version_id}: {reason}")


class UnresolvedSupersession(ValueError):
    """A supersession reference names nothing this archive can see.

    Split out from the ordinary `ValueError` because the two mean opposite things
    to the caller. An ambiguous reference -- two documents, two claims, a count
    that does not pair -- is a mistake to refuse, because guessing moves a live
    fact into history on the strength of a coincidence. A reference to nothing is
    not a mistake at all: it is an ordinary state of a repository mid-migration,
    where `ad-002.md` was written before `adr-0001.md` existed or before it was
    ingested.

    Aborting the whole document over the second one loses the claims it *does*
    state, which is the part a reader wants, over the edge it cannot draw. So this
    becomes a warning on the ingest report and the document lands with its claims.
    """

    def __init__(self, reference: str, reason: str):
        self.reference = reference
        self.reason = reason
        super().__init__(reason)


class SupersessionTargetUnresolved(ValueError):
    """An explicit supersession reference that names no live claim.

    The declared path -- a document that says `supersedes: <id>` -- treats this
    as a warning, because a repository mid-migration is full of references to
    documents that have not been written yet. The explicit path treats it as a
    refusal, because a person who typed `--supersedes` meant one specific claim
    and can be told what went wrong.
    """


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


def _vector_literal(values) -> str:
    """pgvector's text literal for a float list.

    `repr`, not `str`: `str(1e-05)` is `1e-05`, which PostgreSQL reads as a
    numeric literal rather than a `double precision` one, and the cast of the
    whole vector then fails. Every other value in a 384-dimension vector is
    unaffected either way.
    """
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


def _canonical(value) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(*parts) -> str:
    return hashlib.sha256(_canonical(list(parts)).encode("utf-8")).hexdigest()


def memory_document_id(corpus_id: str, path: str) -> str:
    """Stable SHA-256(corpus_id + ':' + path), including across deletion/restore.

    `corpus_id` is stripped first, and that step decides whether this function
    agrees with the rows already in the table. The column is `CHAR(64)`, and
    PostgreSQL blank-pads a shorter value on the way in and strips the padding on a
    cast to text. So the id that reaches this function is 64 characters wide even
    when the id itself is 63 -- which is exactly the case for the default corpus,
    whose id is 56 zeroes followed by "default".

    The database says so itself. `memory_documents_check` is

        id = sha256(rtrim(corpus_id::text) || ':' || path)

    so it hashes the *stripped* id, and this function used to hash the padded one.
    For every corpus whose id is a 64-character digest the two agree and nobody
    notices. For the default corpus they differ by the one padding byte, and every
    write fails:

        new row for relation "memory_documents" violates check constraint
        "memory_documents_check"

    That is `mindpalace remember` -- the README's third step -- on a fresh
    install. See tests/test_default_corpus_is_writable.py.
    """
    return hashlib.sha256(f"{corpus_id.strip()}:{path}".encode("utf-8")).hexdigest()


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


def _says_what_the_document_says(claim_text: str, chunk_text: str) -> bool:
    """Whether the claim reuses the chunk's own wording.

    Where the writer put the line breaks is not a wording difference. A bullet
    that is wrapped over three lines in the source is one statement, and the
    structural extractor reads it as one statement with the breaks collapsed
    to single spaces. The words and the punctuation still have to match the
    chunk exactly: this is about where the lines break, not about what the
    claim says.
    """
    if claim_text in chunk_text:
        return True
    return " ".join(claim_text.split()) in " ".join(chunk_text.split())


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
        # What an author sees when this rejects, and whether they can act on it.
        #
        # The message must not carry the document's text. `ClaimValidationError`
        # exists precisely so a rejection is identifiable without putting a
        # customer's prose in a log -- the same reasoning as `unexpected_error`
        # in api/main.py and as the no-traceback decision in
        # `ingestion._cache_claim_vectors`. So the remedy is stated, not shown:
        # a count and an instruction cost nothing and leak nothing.
        searched = len(archived)
        for quote in sorted(quotes):
            # The chunk that holds the quote *and* the claim's own wording.
            #
            # A table row that appears in two tables is the same bytes twice,
            # and the two read differently because the headers differ: the same
            # `| recall@1 | 0.5284 | 0.5227 |` is "recall@1: before: 0.5284;
            # after: 0.5227" under one header and "recall@1: OLD: 0.5284; NEW:
            # 0.5227" under another. Taking the first chunk with the row found
            # the wrong table, the claim's own wording was not in it, and the
            # document was refused outright -- two of this repository's own
            # documents, one of them the record of every measurement the
            # project has taken.
            matching = next(
                (
                    chunk
                    for chunk in archived
                    if quote in chunk["text"]
                    and _says_what_the_document_says(claim["claim"], chunk["text"])
                ),
                None,
            )
            if matching is None:
                matching = next((c for c in archived if quote in c["text"]), None)
            if matching is None:
                raise ValueError(
                    f"claim[{index}] evidence must be an exact substring of an archived "
                    f"chunk; searched {searched} chunk(s). Copy the sentence character for "
                    f"character from the document body -- paraphrasing, re-wrapping, "
                    f"smart quotes and em dashes are all rejected"
                )
            if not _says_what_the_document_says(claim["claim"], matching["text"]):
                raise ValueError(
                    f"claim[{index}] text must appear in the supporting chunk that its "
                    f"evidence matched; semantic inference is not supported. The claim "
                    f"must reuse the document's own wording"
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


#: How many nearest cached vectors `_nearest_active_claims` fetches.
#:
#: A *result* count, not a candidate pool. HNSW returns the true nearest N across
#: the whole corpus, so a claim outside the N is genuinely further away rather
#: than excluded for being old. The earlier `ACTIVE_CLAIM_SCAN_LIMIT` was the
#: other thing: a slice of the corpus by observation time, with the rest
#: unsearched and nothing in the receipt saying so.
#:
#: Larger than the five finally reported, because active-ness is applied after the
#: neighbour search. A corpus where the nearest vectors are mostly superseded
#: claims would otherwise return a short list from a correct search.
NEIGHBOUR_FETCH = 40


#: At or below this many cached vectors in the corpus, the neighbour search is a
#: sequential scan of the corpus's own rows rather than an HNSW walk. Measured at
#: well under a millisecond for 3,000 rows, and exact.
#:
#: `idx_claim_embeddings_hnsw` is a single global index over the whole table, so
#: an ANN walk picks its `ef_search` candidates from every corpus in the database
#: and the `corpus_id` filter is applied afterwards. Where that filter arrives from
#: RLS rather than from the predicate, a small tenant loses: on `pgvector 0.5.1`,
#: which has no `iterative_scan`, a 50-claim corpus sharing a table with 10,000
#: others recovered **0 of 5** of its true neighbours and returned 1 of the 5 rows
#: asked for. Below this many rows the index buys nothing anyway -- the planner
#: picks a corpus-scoped B-tree and the result is exact -- so the scan is the same
#: answer for a different reason, and it stays right when the corpus grows past the
#: point where the planner would have chosen the graph.
SMALL_CORPUS_EXACT = 1000

#: The corpus-scoped count that decides between the two. Separate from the
#: threshold so a test can state the boundary without restating the policy.
SMALL_CORPUS_COUNT_SQL = """
            SELECT count(*) FROM memory_claim_embeddings
            WHERE corpus_id = :c AND embedding_model = :model
              AND embedding_dimension = :dim AND embedding IS NOT NULL
"""

#: The nearest-neighbour half, on its own, so a plan can be read for it.
#:
#: Split out from `NEAREST_ACTIVE_SQL` because the thing that has to use the HNSW
#: index is exactly this subquery. The active-ness joins around it can be a
#: nested loop without making the neighbour search sequential, and a test that
#: cannot `EXPLAIN` this part cannot say the index is used at all.
NEIGHBOUR_SQL = """
            SELECT claim_id, 1 - (embedding <=> CAST(:q AS vector)) AS similarity
            FROM memory_claim_embeddings
            WHERE corpus_id = :c
              AND embedding_model = :model
              AND embedding_dimension = :dim
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :fetch
"""

NEAREST_ACTIVE_SQL = (
    """
        WITH latest AS MATERIALIZED (
            SELECT memory_document_id, max(version_number) AS version_number
            FROM memory_versions
            WHERE corpus_id = :c
            GROUP BY memory_document_id
        ),
        retired AS MATERIALIZED (
            SELECT supersedes_id FROM memory_claims
            WHERE corpus_id = :c AND supersedes_id IS NOT NULL
        )
        SELECT c.id, c.key, c.claim, c.memory_document_id, c.version_id,
               v.observed_at, n.similarity, e.representation_hash, d.path
        FROM (
    """
    + NEIGHBOUR_SQL
    + """
        ) n
        JOIN memory_claims c ON c.corpus_id = :c AND c.id = n.claim_id
        JOIN memory_claim_embeddings e ON e.corpus_id = :c AND e.claim_id = c.id
        JOIN memory_versions v ON v.corpus_id = c.corpus_id AND v.id = c.version_id
        JOIN memory_documents d ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        JOIN latest l ON l.memory_document_id = c.memory_document_id
                      AND l.version_number = v.version_number
        WHERE v.event <> 'DELETED'
          AND NOT EXISTS (SELECT 1 FROM retired r WHERE r.supersedes_id = c.id)
          AND (CAST(:doc AS char(64)) IS NULL OR c.memory_document_id <> :doc)
        ORDER BY n.similarity DESC, c.id
        LIMIT :limit
"""
)


async def _nearest_active_claims(
    db,
    corpus_id: str,
    vector,
    *,
    model: str,
    dimension: int,
    limit: int = 5,
    exclude_document=None,
):
    """The active claims whose cached vectors are nearest to `vector`.

    Returns `[{claim_id, key, claim, memory_document_id, version_id, observed_at,
    similarity, representation_hash, path}]`, most similar first.

    Two properties are worth stating, because the alternative was wrong in a way
    nothing reported.

    **It searches the whole corpus.** The `LIMIT` bounds how many come back, not
    which rows were considered. The previous implementation read the most recent
    200 active claims and tokenised them, so a fact written 3,800 claims ago was
    invisible and the receipt did not say so.

    **A stale vector cannot be served as a fact.** `representation_hash` travels
    out with every row so the caller can compare it against the hash of the text
    it would embed now, exactly as `load_cached` does. A claim whose cached text
    has changed is dropped by the caller, not trusted here.

    The active-ness test runs after the neighbour search rather than inside it,
    because folding it into the ordered subquery stops PostgreSQL choosing the
    index scan. That is why `NEIGHBOUR_FETCH` exceeds the reported count, and it
    is why `NEAREST_ACTIVE_SQL` is a module constant: a plan that cannot be read
    without calling the function cannot be `EXPLAIN`ed.

    **A small corpus does not use the graph.** `idx_claim_embeddings_hnsw` is one
    global index over the whole table, so its candidates are drawn from every
    corpus in the database and the `corpus_id` filter lands afterwards. Below
    `SMALL_CORPUS_EXACT` vectors the scan is exact, sub-millisecond, and immune to
    that. Above it the graph is what makes the search affordable, so it stays.
    """
    params = {
        "c": corpus_id,
        "q": _vector_literal(vector),
        "model": model,
        "dim": dimension,
        "fetch": int(NEIGHBOUR_FETCH),
        "limit": int(limit),
        "doc": exclude_document,
    }
    small = int(
        (
            await db.execute(
                text(SMALL_CORPUS_COUNT_SQL),
                {"c": corpus_id, "model": model, "dim": dimension},
            )
        ).scalar()
        or 0
    )
    if small <= SMALL_CORPUS_EXACT:
        # One transaction-local setting pair, so the exact plan cannot leak into
        # the caller's session or into the next query on this connection.
        await db.execute(text("SET LOCAL enable_indexscan = off"))
        await db.execute(text("SET LOCAL enable_indexonlyscan = off"))
    rows = (await db.execute(text(NEAREST_ACTIVE_SQL), params)).all()
    return [
        {
            "id": row[0],
            "key": row[1],
            "claim": row[2],
            "memory_document_id": row[3],
            "version_id": row[4],
            "observed_at": row[5],
            "similarity": float(row[6]),
            "representation_hash": row[7],
            "path": row[8],
        }
        for row in rows
    ]


async def _active_claims(
    db,
    corpus_id: str,
    *,
    key: str | None = None,
    exclude_document=None,
    limit: int | None = None,
):
    """Claims the corpus currently holds as fact, newest observation first.

    A claim is active when nothing supersedes it, its version is the latest one
    for its document, and that version is not a tombstone. Those three
    conditions are what `_query_result` calls CURRENT, and this is the same
    question asked in SQL so a write can ask it without loading the archive.

    `exclude_document` is the document being written. A claim in it is either the
    claim's own predecessor -- which `record_version` already handles -- or a
    claim this same write is about to supersede.

    `limit=None`, which is the default: an unbounded read of a specific set of
    claims. This is the *complete* answer to "which claims are live", and a caller
    that is looking for a near-duplicate must not be handed a slice of it.
    `_nearest_active_claims` is the bounded alternative, and it is bounded by
    similarity rather than by age.

    The latest-version test is a grouped join rather than a correlated subquery
    per claim, and both CTEs are `MATERIALIZED` because PostgreSQL inlines a
    single-reference CTE and re-executes it per outer row -- which turns the
    grouped join back into the per-claim subquery it replaced. Measured on 180
    claims: 43 ms inlined on the first call, and `MATERIALIZED` is what makes the
    plan a single pass. This is the same cliff `memory_relevance` and
    `_load` document, in a different query.
    """
    sql = """
        WITH latest AS MATERIALIZED (
            SELECT memory_document_id, max(version_number) AS version_number
            FROM memory_versions
            WHERE corpus_id = :c
            GROUP BY memory_document_id
        ),
        retired AS MATERIALIZED (
            SELECT supersedes_id FROM memory_claims
            WHERE corpus_id = :c AND supersedes_id IS NOT NULL
        )
        SELECT c.id, c.key, c.claim, c.memory_document_id, c.version_id, v.observed_at
        FROM memory_claims c
        JOIN memory_versions v ON v.corpus_id = c.corpus_id AND v.id = c.version_id
        JOIN latest l ON l.memory_document_id = c.memory_document_id
                      AND l.version_number = v.version_number
        WHERE c.corpus_id = :c
          AND (CAST(:key AS text) IS NULL OR c.key = :key)
          AND (CAST(:doc AS char(64)) IS NULL OR c.memory_document_id <> :doc)
          AND v.event <> 'DELETED'
          AND NOT EXISTS (SELECT 1 FROM retired r WHERE r.supersedes_id = c.id)
        ORDER BY v.observed_at DESC, c.id
    """
    # Inlined, not bound. A LIMIT parameter makes this a prepared statement, and
    # asyncpg's prepare threshold is where the generic-plan cliff lives; the value
    # comes from this module's own constants, never from a caller.
    bounded = f"\n        LIMIT {int(limit)}" if limit else ""
    rows = (
        await db.execute(text(sql + bounded), {"c": corpus_id, "key": key, "doc": exclude_document})
    ).all()
    return [
        {
            "id": row[0],
            "key": row[1],
            "claim": row[2],
            "memory_document_id": row[3],
            "version_id": row[4],
            "observed_at": row[5],
        }
        for row in rows
    ]


async def _resolve_claim_id(db, corpus_id: str, identity: str, given: str) -> str:
    """Expand a full claim id, a unique prefix of one, or a phrase to the full id.

    A prefix is accepted because that is what the CLI prints. It prints 12 of 64
    characters because a 64-character id on every answer is unreadable, and then
    the command that tells you to use that id rejects it. Matching what the user
    can see is the difference between a documented workflow and a broken one.

    A phrase is accepted for the same reason one level up: the caller who wants to
    retire the "primary datastore" claim usually has that phrase in front of them
    and the 64-character id is on a different screen. The forms do not overlap --
    claim ids are lowercase hex, so anything containing a space, a `.` or a letter
    outside `a-f` is a phrase and is matched as one.

    Both are refused rather than guessed at when more than one claim matches. The
    whole cost model here is that a supersession landing on the wrong claim moves a
    live fact into history, and picking the first of two candidates is exactly
    that mistake.
    """
    if len(given) == 64 and all(c in "0123456789abcdef" for c in given.casefold()):
        return given
    candidates = await _active_claims(db, corpus_id, exclude_document=identity, limit=None)
    hex_like = bool(given) and all(c in "0123456789abcdef" for c in given.casefold())
    matches = [c for c in candidates if c["id"].startswith(given.casefold())] if hex_like else []
    if not matches and not hex_like:
        matches = _phrase_matches(given, candidates)
    if not matches:
        raise SupersessionTargetUnresolved(
            f"supersedes_claim_id {given!r} is not an active claim in this corpus. "
            "It matches no active claim by id prefix or by text. A superseded, "
            "deleted or other-corpus claim cannot be superseded again."
        )
    if len(matches) > 1:
        raise SupersessionTargetUnresolved(
            f"supersedes_claim_id {given!r} matches {len(matches)} active claims "
            f"({', '.join(c['id'][:16] for c in matches[:3])}); use more of the id"
            + ("" if hex_like else ". Retire the one you meant by its id, which `recall` prints.")
        )
    return matches[0]["id"]


def _phrase_matches(given: str, candidates: list[dict], threshold: float = 0.60) -> list[dict]:
    """Active claims the phrase might name, by the measure in `api/services/similarity`.

    The same measure `remember.related_active` uses, for the same reason: it needs
    no model, so `remember` does not have to pay a model load to resolve a reference
    the caller wrote in plain English. Sharing the function is also what stops the
    two from drifting: a phrase that resolves to forty claims in one path and two in
    the other would be a bug report nobody could reproduce.

    Refusing on more than one match is the point. A phrase matching forty claims
    matches none of them usefully, and the caller is better served by the error
    naming forty ids than by the archive picking one.
    """
    from api.services.similarity import document_frequencies, terms, weighted_overlap

    words = terms(given)
    if not words or not candidates:
        return []
    frequencies = document_frequencies(terms(c["claim"]) for c in candidates)
    total = len(candidates)
    return [
        claim
        for claim in candidates
        if weighted_overlap(words, terms(claim["claim"]), frequencies, total) >= threshold
    ]


def _claims_by_id_prefix(candidates: list[dict], reference: str) -> list[dict]:
    """Active claims whose id starts with a hex prefix, if the prefix is one.

    A claim id is a SHA-256 hex digest, so it is lowercase hex by construction.
    The prefix `recall` prints is twelve characters of it, and the reference a
    document writes is that same twelve characters.

    Eight characters is the floor. Below that, a prefix is more likely to be a
    word that happens to be hex than a deliberate identifier, and a claim key
    that starts with hex digits is a far more likely reading of it.

    More than one match is returned rather than resolved. The caller reports
    the ambiguity and names the claims that collided; a prefix that quietly
    picked one of them would be a supersession landing on a claim nobody named.
    """
    text = reference.strip().casefold()
    if not 8 <= len(text) <= 64:
        return []
    if not all(c in "0123456789abcdef" for c in text):
        return []
    return [c for c in candidates if c["id"].startswith(text)]


async def _targets_for_reference(db, corpus_id: str, identity: str, reference: str) -> list[dict]:
    """The active claims a supersession reference names, or none with a reason.

    A reference is a document path, a claim key, or a claim id, and it may be either
    in full or as the part of the path that distinguishes it. `adr/0001-use-pg15.md`,
    `0001-use-pg15.md` and `examples/adr/0001-use-pg15.md` all name one document,
    because an author writes the shortest thing that identifies it and the archive
    is the only party that knows the corpus layout.

    Matching is exact first and then by unique path suffix. "Unique" is doing real
    work: two documents called `0001.md` in different directories are an ambiguous
    reference, and an ambiguous reference that resolves anyway is a supersession
    landing on the wrong decision.

    A claim id is matched the way `recall` prints it. `recall` shows twelve
    characters of a sixty-four character id, because a full id on every answer is
    unreadable, and a document that says `supersedes: f74ee8c3b475` has to name
    the same thing the reader was shown. A hex prefix of eight to sixty-four
    characters is matched against the active claims, and more than one match is
    refused rather than resolved by taking the first.
    """
    from api.services.markdown_claims import looks_like_path

    candidates = await _active_claims(db, corpus_id, exclude_document=identity, limit=None)
    if not looks_like_path(reference):
        by_key = [c for c in candidates if c["key"] == reference]
        if not by_key:
            by_id = _claims_by_id_prefix(candidates, reference)
            if len(by_id) > 1:
                raise ValueError(
                    f"supersedes reference {reference!r} matches {len(by_id)} active "
                    f"claims ({', '.join(c['id'][:16] for c in by_id[:3])}); use more of the id"
                )
            if by_id:
                return by_id
            raise UnresolvedSupersession(
                reference,
                f"supersedes reference {reference!r} matches no active claim key or claim id "
                "in this corpus. Write the key exactly as it appears in the archive, the "
                "id `recall` printed, or the path of the document that holds the claim.",
            )
        if len(by_key) > 1:
            raise ValueError(
                f"supersedes reference {reference!r} matches {len(by_key)} active claims; "
                f"the ids are {', '.join(c['id'][:16] for c in by_key[:3])}. Name one."
            )
        return by_key

    needle = reference.replace("\\", "/").casefold().lstrip("./")
    rows = (
        await db.execute(
            text("""
                SELECT d.id, d.path
                FROM memory_documents d
                WHERE d.corpus_id = :c
                  AND d.path ILIKE :suffix
                ORDER BY d.path
                """),
            {"c": corpus_id, "suffix": f"%/{needle}"},
        )
    ).all()
    if not rows:
        rows = (
            await db.execute(
                text(
                    "SELECT d.id, d.path FROM memory_documents d "
                    "WHERE d.corpus_id = :c AND lower(d.path) = :exact"
                ),
                {"c": corpus_id, "exact": needle},
            )
        ).all()
    if not rows:
        raise UnresolvedSupersession(
            reference,
            f"supersedes reference {reference!r} matches no document in this corpus. "
            "Ingest the superseded document first; supersession cannot retire a claim "
            "this archive has never seen.",
        )
    if len(rows) > 1:
        raise ValueError(
            f"supersedes reference {reference!r} matches {len(rows)} documents "
            f"({', '.join(row[1] for row in rows[:3])}). Write more of the path."
        )
    document = rows[0][0]
    return [c for c in candidates if c["memory_document_id"] == document]


async def _declared_supersessions(
    db, corpus_id: str, identity: str, references: list[str], claims: list[dict]
) -> tuple[dict[str, tuple[str, str]], list[UnresolvedSupersession]]:
    """Pair this version's claims with the ones a document says it replaces.

    A reference names a set of live claims -- one document's worth, or one key's.
    Each has to be paired with a claim in the version being written, because that
    is what the supersession edge points *from*, and an edge has to point at
    something. Pairing, in order:

    1. **Same key.** A rewritten ADR keeps its section headings, so the positional
       keys line up and the match is exact rather than inferred.
    2. **Same position, when the remaining counts are equal.** The remaining
       unmatched claims are paired in claim order. Equal counts mean the two
       documents have the same shape, which is what "this replaces that" means for
       a document that was edited rather than rewritten.
    3. **Refused.** Unequal counts and no shared key means the reference names
       claims this document says nothing about. Retiring them anyway would move
       live facts into history on the strength of a guess, and the guess would be
       invisible afterwards. The error names the ids so `--supersedes` can be used
       for exactly the claims the author meant.

    A reference that resolves to nothing is returned alongside the pairs rather
    than raised. It is the normal state of a repository where `ad-002.md` was
    written before `adr-0001.md` arrived, and the claims in the document that
    *were* written are worth archiving without it. The caller reports each one as
    `supersession_unresolved`, so the edge that did not happen is on the record
    instead of absent from it.
    """
    keys = [claim["key"] for claim in claims]
    pairs: dict[str, tuple[str, str]] = {}
    targets: list[dict] = []
    unresolved: list[UnresolvedSupersession] = []
    for reference in references:
        try:
            found = await _targets_for_reference(db, corpus_id, identity, reference)
        except UnresolvedSupersession as exc:
            unresolved.append(exc)
            continue
        for target in found:
            if target["id"] not in {t["id"] for t in targets}:
                targets.append(target)

    unmatched_keys = list(keys)
    for target in targets:
        if target["key"] in unmatched_keys:
            pairs[target["key"]] = (target["id"], "explicit_claim_id")
            unmatched_keys.remove(target["key"])

    if targets and unmatched_keys:
        remaining = [t for t in targets if t["key"] not in pairs]
        if len(remaining) == len(unmatched_keys):
            for key, target in zip(unmatched_keys, remaining):
                pairs[key] = (target["id"], "explicit_claim_id")
        else:
            orphans = [t["id"] for t in remaining]
            raise ValueError(
                f"this document supersedes {len(targets)} claim(s) but authors "
                f"{len(keys)} claim(s), and {len(remaining)} of the superseded claims share "
                f"no key with any of them. Refusing to pair by guesswork. Retire the "
                f"remainder explicitly with --supersedes: {', '.join(o[:16] for o in orphans[:4])}."
            )
    return pairs, unresolved


async def _cross_document_targets(
    db,
    corpus_id: str,
    identity: str,
    claims: list[dict],
    supersedes_claim_id: str | None,
    topic_key: str | None,
) -> dict[str, tuple[str, str]]:
    """Map each authored claim key to the `(claim_id, basis)` it retires elsewhere.

    Returns an empty mapping when neither argument was given, so the ordinary
    rewrite path costs one branch and no query.

    Both arguments are refused rather than guessed at when they do not resolve to
    exactly one target. A supersession that lands on the wrong claim is worse
    than one that does not land: it moves a live fact into `historical` and the
    reader is told it is old. An `ad-002.md` that retires `adr-001.md` must say
    which claim, or say a key that resolves to exactly one live claim.
    """
    if not supersedes_claim_id and not topic_key:
        return {}
    keys = {claim["key"] for claim in claims}
    if topic_key and topic_key not in keys:
        raise ValueError(
            f"topic_key {topic_key!r} is not one of this version's claim keys; "
            "superseding a key would record a claim about something the new "
            "document does not say"
        )

    if supersedes_claim_id:
        # Unbounded: a named target must be found or reported missing, never
        # missed because a newer claim pushed it past a scan limit.
        wanted = await _resolve_claim_id(db, corpus_id, identity, supersedes_claim_id)
        candidates = await _active_claims(db, corpus_id, exclude_document=identity, limit=None)
        targets = [c for c in candidates if c["id"] == wanted]
        if not targets:
            raise ValueError(
                f"supersedes_claim_id {supersedes_claim_id!r} is not an active claim "
                "in this corpus. A superseded, deleted or other-corpus claim cannot "
                "be superseded again."
            )
        target = targets[0]
        # Exactly one claim in this version may take the target. Naming a claim is
        # specific enough on its own -- which is the point, since a claim read out
        # of plain Markdown carries a positional key that a newer document has no
        # way to reproduce. More than one candidate means the caller named a key,
        # not a claim, and the archive will not guess which.
        matching = [key for key in keys if key == target["key"]]
        if matching:
            return {matching[0]: (target["id"], "explicit_claim_id")}
        if len(keys) == 1:
            return {next(iter(keys)): (target["id"], "explicit_claim_id")}
        raise ValueError(
            f"this version authors {len(keys)} claims ({sorted(keys)}), so "
            f"supersedes_claim_id {supersedes_claim_id!r} cannot say which one replaces "
            f"{target['id']!r} on key {target['key']!r}. Use topic_key to retire by "
            "key, or write one claim."
        )

    candidates = await _active_claims(
        db, corpus_id, key=topic_key, exclude_document=identity, limit=None
    )
    if not candidates:
        return {}
    if len(candidates) > 1:
        # Refuse rather than pick. Retiring the newest of three live claims leaves
        # two disagreeing ones and makes the archive look like it resolved
        # something it did not. The caller can name the claim instead.
        raise ValueError(
            f"topic_key {topic_key!r} has {len(candidates)} active claims across "
            "documents; topic_key would retire one arbitrarily. Pass "
            "supersedes_claim_id with the id of the one to replace."
        )
    # Most recently observed wins. An older live claim under the same key stays
    # CURRENT and is reported as a conflict, which is the honest state: two
    # documents really do disagree until one of them is retired too.
    return {topic_key: (candidates[0]["id"], "topic_key")}


async def record_version(
    db: AsyncSession,
    corpus_id: str,
    path: str,
    document_id: str,
    content: str,
    metadata: dict,
    chunks: list[dict],
    owner_principal_id: str | None = None,
    supersedes_claim_id: str | None = None,
    topic_key: str | None = None,
    supersedes_refs: list[str] | None = None,
) -> dict:
    """Archive NEW/MODIFIED/RESTORED or return the existing version with skipped=True.

    Change detection includes raw content and all normalized metadata, not live
    IDs or rechunking. IDs derive from identity + predecessor + event + payload.
    Evidence is validated even on an unchanged call. No commit; propagate errors.

    `supersedes_claim_id` retires a named claim in this corpus, whatever document
    it lives in. `topic_key` retires whatever is currently active under that key.
    `supersedes_refs` carries the references a Markdown document wrote about
    itself, so `adr-0002` replacing `adr-0001` links itself without anybody
    copying an id out of the database. It is consulted only when neither of the
    other two was given.
    Both are refused rather than guessed at when they do not resolve to exactly one
    target, because a supersession that lands on the wrong claim is worse than one
    that does not land at all -- it marks a live fact as history.

    `owner_principal_id` is inert while resource-level authorization is
    withdrawn. It is kept on the signature so the callers, the CLI, and
    `api/services/resources.py` still describe the intended shape, and so
    reinstating it is a migration rather than an edit at twenty call sites. Do not
    rely on it setting anything today -- see `docs/withdrawn/`.
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
        INSERT INTO memory_documents (corpus_id, id, path)
        VALUES (:c, :d, :p)
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
    cross_document = await _cross_document_targets(
        db, corpus_id, identity, claims, supersedes_claim_id, topic_key
    )
    if not cross_document and supersedes_refs:
        # Nothing explicit was passed, so the document itself is the instruction.
        # An explicit `--supersedes` outranks it: the caller named a claim, and a
        # declaration in the file is not a reason to overrule them.
        cross_document, unresolved = await _declared_supersessions(
            db, corpus_id, identity, supersedes_refs, claims
        )
    else:
        unresolved = []
    # The rows are collected first and written in two statements rather than
    # two per claim.
    #
    # Every value in a claim row is known before the loop starts: the
    # supersession target comes from the previous version or from the
    # cross-document resolution, and both are resolved above. So the loop is
    # pure arithmetic, and the only thing the per-row statements bought was a
    # round trip to the database for every claim and every piece of evidence.
    # A repository of 78 documents produced 2,487 claims and 2,487 evidence
    # rows: 4,974 statements, each one a round trip, for a write that is
    # 4,974 rows.
    claim_rows: list[dict] = []
    evidence_rows: list[dict] = []
    for claim in claims:
        supersedes = previous_claims.get(claim["key"])
        basis = "same_document_key" if supersedes else None
        if single_replacement:
            supersedes = next(iter(previous_claims.values()))
            basis = "single_claim_replacement"
        # An explicit target outranks whatever this document's own history
        # offered. The author named the claim being retired, and the fact that
        # the previous version happened to use the same key is not a reason to
        # ignore them.
        if claim["key"] in cross_document:
            supersedes, basis = cross_document[claim["key"]]
        claim_id = _hash("claim", version["id"], claim["key"])
        claim_rows.append(
            {
                **params,
                **claim,
                "id": claim_id,
                "value": _canonical(claim["value"]),
                "supersedes": supersedes,
                "basis": basis,
            }
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
            evidence_rows.append(
                {
                    **params,
                    **reference,
                    "id": evidence_id,
                    "claim_id": claim_id,
                    "chunk_id": chunk_id,
                }
            )
    if claim_rows:
        await db.execute(
            text("""
            INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id,
                key, value, claim, valid_from, valid_until, supersedes_id, supersession_basis)
            VALUES (:c, :d, :v, :id, :key, CAST(:value AS jsonb), :claim,
                    :valid_from, :valid_until, :supersedes, :basis)
        """),
            claim_rows,
        )
    if evidence_rows:
        await db.execute(
            text("""
            INSERT INTO memory_evidence (corpus_id, version_id, id, claim_id, chunk_id,
                                         quote, start_offset, end_offset)
            VALUES (:c, :v, :id, :claim_id, :chunk_id, :quote, :start_offset, :end_offset)
        """),
            evidence_rows,
        )
    return {**version, "skipped": False, "supersession_unresolved": unresolved}


async def record_deletion(db: AsyncSession, corpus_id: str, path: str) -> bool:
    """Append a tombstone; False for unknown/already deleted paths. Never touch live rows."""
    await lock_corpus(db, corpus_id)
    identity = memory_document_id(corpus_id, path)
    previous = await _latest(db, corpus_id, identity)
    if not previous or previous["event"] == "DELETED":
        return False
    await _insert_version(db, corpus_id, identity, previous, previous["document_id"], "DELETED")
    return True


async def _load(db, corpus_id, path=None, as_of=None, *, chunk_text=True, version_text=True):
    """Load versions with their claims, evidence and chunks.

    The child aggregates are correlated subqueries rather than one set-based pass
    per table. A set-based rewrite was built and measured: it returns byte-
    identical rows and cuts shared buffer reads at 1,000 claims from 1,057,320 to
    2,475, with EXPLAIN ANALYZE execution 639.93 ms to 95.91 ms. It was reverted.
    End to end it is 3.5x faster at 1,000 claims but 2.2x slower at 100, because
    three hash aggregates plus three joins cost more than a handful of index
    lookups on a small corpus, and it needs `plan_cache_mode = force_custom_plan`
    to avoid a 12x generic-plan cliff that asyncpg walks into after five calls.
    See `docs/research/m012-fast-memory-substrate.md`.

    ``chunk_text=False`` returns only each chunk's id and heading. The public
    projection reads nothing else from a chunk, and chunk text is the bulk of a
    real document. The raw ``history`` surface keeps the text, because a
    developer asking for history wants the source.

    ``version_text=False`` drops each version's ``content`` and ``metadata``.
    Those two columns hold the whole document body and are the widest part of a
    version row, but the projection never reads either: authority comes from the
    claims, evidence and the version graph. Same reasoning as ``chunk_text``, and
    the raw ``history`` surface keeps both for the same reason.
    """
    columns = (
        "v.*"
        if version_text
        else (
            "v.corpus_id, v.memory_document_id, v.id, v.predecessor_id, v.version_number, "
            "v.document_id, v.event, v.observed_at, v.fingerprint"
        )
    )
    chunk_expr = (
        "jsonb_build_object('id', c.id, 'heading_path', c.heading_path)"
        if not chunk_text
        else "to_jsonb(c)"
    )
    # One statement gives every read a coherent MVCC view, even without a read lock.
    result = await db.execute(
        text(f"""
        SELECT {columns}, d.path,
            COALESCE((SELECT jsonb_agg({chunk_expr} ORDER BY c.order_index)
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


#: How close a claim has to be to stand in for a query that matched nothing, and how
#: wide a band around the best score still counts as "the same subject".
#:
#: 0.30 is the relevance floor the rest of the product uses, so a relaxed anchor is
#: never something the query path would have refused outright. 0.10 is the same
#: near-tie margin `memory_relevance` uses for `ambiguous`. Both are here to bound
#: a fallback, not to tune it: the fallback runs only when strict matching found
#: nothing, and it is better to name one subject than to report no history at all.
ANCHOR_FLOOR = 0.30
ANCHOR_BAND = 0.10

#: Words that frame a question about history rather than naming what changed.
#:
#: "How did the default install change?" is a question about the default install.
#: "Change" is the frame the question is put in, and no claim is about the word
#: "change", so requiring a claim to carry it means the question matches nothing
#: and the answer says there is no history. The archive knows what changed; the
#: question does not have to say so.
#:
#: These are dropped only when a question is being resolved against the
#: supersession graph. A question about the word "history" as a subject is
#: answered by the corpus's own text, not by this list.
HISTORY_FRAMING_TERMS = frozenset(
    """change changed history evolve evolved before previously replace replaced""".split()
)


def _supersession_chains(claims: list[dict]) -> list[list[dict]]:
    """The connected components of the supersession graph.

    An edge runs from a claim to the claim it supersedes, and from a claim to
    every claim that supersedes it. The graph is walked in **both** directions,
    so a claim that was retired is found together with the claim that retired
    it, whichever one the reader happened to anchor on.

    Walking backwards only is the defect this exists to fix. `mindpalace history
    "PyTorch embedder"` anchored on the newer claim and walked back to the older
    one, so the timeline was right. `mindpalace history "How did the default
    embedding backend and install size change?"` anchored on the *older* claim,
    found nothing to walk back to, and reported the retired claim as the one
    that is true now.

    Cross-document edges are edges. The key and the path of the two claims
    differ, and the archive still records that one replaced the other, so the
    chain is built from the `supersedes_id` column alone.
    """
    by_id = {claim["id"]: claim for claim in claims}
    parent = {claim["id"]: claim["id"] for claim in claims}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for claim in claims:
        target = claim.get("supersedes_id")
        if target and target in by_id:
            left, right = find(claim["id"]), find(target)
            if left != right:
                parent[right] = left

    groups: dict[str, list[dict]] = {}
    for claim in claims:
        groups.setdefault(find(claim["id"]), []).append(claim)
    return list(groups.values())


def _query_result(versions, query_text, valid_at=None, *, lineage: bool = False):
    """Bucket the claims of a coherent archive view against a question.

    ``lineage=True`` resolves the question against the supersession graph
    rather than against one claim at a time. A claim and the claim that
    replaced it are one subject to a reader, and the question is asked of the
    pair, so the answer carries both halves and the retired half is never the
    last thing the timeline says. The frame words are dropped first, because a
    question about what changed is not a question about the word "changed".

    Off by default: a ranked read asks which claim answers the question, and
    there the answer is one claim.
    """
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
    # Interrogatives and function words are not part of what a claim is about, so
    # they cannot be required of it. `mindpalace history "What is the primary
    # datastore?"` and `mindpalace history "primary datastore"` now reach the same
    # claims, which is what an MCP agent will send; only this normalisation stands
    # between them. An empty result means an empty query, which returns everything,
    # as it always has.
    # Interrogatives and function words are not part of what a claim is about, so
    # they cannot be required of it. `mindpalace history "What is the primary
    # datastore?"` and `mindpalace history "primary datastore"` now reach the same
    # claims, which is what an MCP agent sends; only this normalisation stands
    # between them. A query with no content words left is an empty query, which
    # returns everything, as it always has.
    content = similarity.terms(query_text)
    if lineage:
        # The frame the question is put in is not part of what it is about. See
        # `HISTORY_FRAMING_TERMS`.
        content = content - HISTORY_FRAMING_TERMS

    def haystack(claim):
        return frozenset(
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

    # Second pass, used only when the first matched nothing at all. A question can
    # still miss a subject whose name the query paraphrases -- "how did the cache
    # policy change" against a claim that says "shared cache" -- and the honest
    # answer there is the tightest cluster of claims about it, not an empty
    # timeline. Scoped to the best scorers and never used to remove a claim, so a
    # query that already matched keeps exactly the answer it had.
    #
    # Plain overlap, not the weighted measure the conflict hint uses. This asks
    # whether the claim is about the query at all; that one asks which of several
    # candidates is closest. A query word appearing in no claim is weak evidence
    # here, where inverse document frequency makes it the strongest signal there
    # is. `similarity.overlap` documents the split.
    relaxed: frozenset[str] = frozenset()
    if not lineage and content and not any(content <= haystack(claim) for claim in claims):
        scored = [(similarity.overlap(content, similarity.terms(c["claim"])), c) for c in claims]
        best = max((score for score, _c in scored), default=0.0)
        if best >= ANCHOR_FLOOR:
            relaxed = frozenset(c["id"] for score, c in scored if best - score <= ANCHOR_BAND)

    if lineage:
        # A claim and the claim that replaced it are one subject to a reader, so
        # the question is asked of the chain and not of one claim in it. A
        # question that names the before and the after is a question about the
        # chain: "the default install" is in the older claim, "the default
        # backend" is in the newer one, and neither half answers it alone.
        #
        # The chain is the connected component of the supersession graph, so it
        # carries the successor as well as the predecessor. Anchoring on the
        # retired claim therefore reaches the claim that retired it, and the
        # retired claim is never the last thing the timeline says.
        chains = _supersession_chains(claims)
        chain_tokens = [frozenset().union(*(haystack(c) for c in chain)) for chain in chains]
        chain_words = [
            frozenset().union(*(similarity.terms(c["claim"]) for c in chain)) for chain in chains
        ]
        # A chain may only anchor when at least one of its own claims clears
        # the relevance floor on its own. Union scoring alone lets a multi-node
        # chain accumulate one query word per node and beat a genuinely relevant
        # claim, so the floor is checked per claim, not per union. The check uses
        # the same haystack the strict path uses (key, claim, value and path), so
        # a claim identified by its key still counts as an anchor on its own.
        chain_has_anchor = [
            any(
                (content <= haystack(member))
                or similarity.overlap(content, haystack(member)) >= ANCHOR_FLOOR
                or similarity.overlap(content, similarity.terms(member["claim"])) >= ANCHOR_FLOOR
                for member in chain
            )
            for chain in chains
        ]
        if not content:
            matched_ids = {claim["id"] for claim in claims}
        else:
            anchors = [index for index, tokens in enumerate(chain_tokens) if content <= tokens]
            if not anchors:
                # Second pass, as above, but scored on the chain: the question is
                # measured against the union of the tokens the whole lineage
                # carries, so a question spanning the before and the after is
                # scored on both halves. A chain with no individual anchor is
                # disqualified: union overlap alone is not evidence of relevance.
                scores = [
                    similarity.overlap(content, words) if chain_has_anchor[index] else 0.0
                    for index, words in enumerate(chain_words)
                ]
                best = max(scores, default=0.0)
                if best >= ANCHOR_FLOOR:
                    anchors = [
                        index for index, score in enumerate(scores) if best - score <= ANCHOR_BAND
                    ]
            matched_ids = {claim["id"] for index in anchors for claim in chains[index]}

        def matches(claim):
            if not content:
                return True
            return claim["id"] in matched_ids

    else:

        def matches(claim):
            if not content:
                return True
            return content <= haystack(claim) or claim["id"] in relaxed

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
    # A claim that one of *this query's* matches replaced is history for this query,
    # whether or not its own text matches. The edge is the archive's own record that
    # the claim used to be the answer, and it is the only thing tying two differently
    # worded decisions together: "What is the primary datastore?" matches
    # "PostgreSQL 16 with three replicas" but not "PostgreSQL 15 with three replicas",
    # so requiring a text match on the older claim made `history` report that the
    # decision was never replaced. Scoped to matched claims so an unrelated
    # supersession elsewhere in the corpus cannot drag itself into this answer.
    replaced_by_match = {
        claim["supersedes_id"] for claim in claims if claim["supersedes_id"] and matches(claim)
    }
    current = [claim for claim in active if matches(claim) and claim["status"] == "CURRENT"]
    # A superseded claim is historical even when the version holding it is still
    # the latest one for its document. That is what cross-document supersession
    # produces: `adr-002.md` retires a claim in `adr-001.md`, and `adr-001.md` has
    # not changed, so its version stays current while the claim inside it does not.
    # Keying history on the version alone made such a claim appear in no bucket at
    # all -- not current, not historical, not uncertain -- and every read of the
    # corpus that mentioned it raised KeyError.
    historical = [
        claim
        for claim in claims
        if (matches(claim) or claim["id"] in replaced_by_match)
        and (claim["status"] == "SUPERSEDED" or claim["version_id"] not in current_ids)
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
        # Every claim in the load, status-annotated, whether or not a bucket
        # claimed it. `project` builds `Claim` objects from this rather than from
        # the buckets above, because `changes` walks version contents and a claim
        # in no bucket -- which is what a cross-document supersession leaves
        # behind -- would then be unrepresentable.
        "all_claims": claims,
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
    On migration 006+, the seal is written last in the same transaction and blocks
    later membership inserts. The schema check preserves the isolated 004 contract.
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
    seal_available = (
        await db.execute(
            text(
                "SELECT to_regclass(format('%I.memory_snapshot_seals', "
                "current_schema())) IS NOT NULL"
            )
        )
    ).scalar_one()
    if seal_available:
        await db.execute(
            text("""
            INSERT INTO memory_snapshot_seals(corpus_id, snapshot_id)
            VALUES (:c, :id)
        """),
            {"c": corpus_id, "id": snapshot_id},
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
