"""Cache claim representations for authoritative relevance scoring.

This is L2. It holds no authority: deleting the table changes latency and
nothing else. The rebuild path is `ensure_claim_embeddings`, which re-derives
every row from immutable claim text.

Claim representations are derived from `claim.claim`, `claim.key` and
`claim.path`, all of which are immutable once the claim is written. The cache
key is the SHA-256 of the exact string that was embedded, together with the
model name and dimension, so a changed claim, a changed model or a changed
dimension each invalidate their own rows instead of being served stale.
"""

from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

INSERT = """
    INSERT INTO memory_claim_embeddings (
        corpus_id, claim_id, representation_hash,
        embedding_model, embedding_dimension, embedding_version, embedding
    ) VALUES (
        :corpus, :claim, :hash, :model, :dimension, :version,
        CAST(:vector AS vector)
    )
    ON CONFLICT (corpus_id, claim_id) DO UPDATE
        SET representation_hash = EXCLUDED.representation_hash,
            embedding_model = EXCLUDED.embedding_model,
            embedding_dimension = EXCLUDED.embedding_dimension,
            embedding_version = EXCLUDED.embedding_version,
            embedding = EXCLUDED.embedding
    WHERE memory_claim_embeddings.representation_hash IS DISTINCT FROM EXCLUDED.representation_hash
       OR memory_claim_embeddings.embedding_model IS DISTINCT FROM EXCLUDED.embedding_model
       OR memory_claim_embeddings.embedding_dimension IS DISTINCT FROM EXCLUDED.embedding_dimension
"""

#: How many representation hashes are bound into one lookup.
#:
#: Bounded so a reindex of a whole corpus does not bind one enormous array,
#: and large enough that a document's worth of hashes is one round trip.
HASH_BATCH = 500


class ClaimView(NamedTuple):
    """Minimal shape `representation` needs, for rows straight from SQL."""

    claim: str
    key: str
    path: str


def representation(claim) -> str:
    """The exact text embedded for one claim.

    The relevance gate and the cache must agree on this string, so it lives in
    one place and both call it.
    """
    return representation_of(claim.claim, claim.key, claim.path)


def representation_of(claim_text: str, key: str, path: str) -> str:
    return f"{lead_clause(claim_text)} {key} {path}"


def lead_clause(claim_text: str) -> str:
    """The topical part of a claim: its first source line.

    A statement wrapped over three lines is one sentence, and the mean-pooled
    vector of the whole thing is the mean of all three lines' words. The
    continuation clauses are detail, and averaging them in dilutes the claim's
    own topic: the row-level-security claim in `docs/security.md` scored 0.530
    on "How are tenants isolated in the database?" as the line the writer
    wrapped it in, and 0.465 as the whole paragraph.

    The lead is the first line of the statement as it was written, because that
    is where the writer put the topic. A claim that fits on one line is its
    own lead, and the representation of every authored claim is unchanged.
    """
    stripped = claim_text.strip()
    if "\n" not in stripped:
        return stripped
    return stripped.split("\n", 1)[0].rstrip()


def representation_hash(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


async def pending(
    db: AsyncSession, corpus_id: str, version_ids: list[str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Return (claim id -> representation hash, the exact texts to embed).

    ``path`` is not on ``memory_claims``; it comes from the document the version
    belongs to. Both the ingestion fill and the reindex backfill use this so
    they cannot drift apart, and so the hash always describes the text that was
    actually embedded.
    """
    sql = """
        SELECT c.id, c.claim, c.key, d.path
        FROM memory_claims c
        JOIN memory_versions v
          ON v.corpus_id = c.corpus_id AND v.id = c.version_id
        JOIN memory_documents d
          ON d.corpus_id = v.corpus_id AND d.id = v.memory_document_id
        WHERE c.corpus_id = :c
    """
    params: dict = {"c": corpus_id}
    if version_ids is not None:
        sql += " AND c.version_id = ANY(CAST(:v AS CHAR(64)[]))"
        params["v"] = list(version_ids)
    sql += " ORDER BY c.id"
    rows = (await db.execute(text(sql), params)).all()
    texts = [representation_of(row[1], row[2], row[3]) for row in rows]
    return {row[0]: representation_hash(t) for row, t in zip(rows, texts)}, texts


async def load_cached(
    db: AsyncSession, corpus_id: str, wanted: dict[str, str], model: str, dimension: int
) -> dict[str, list[float]]:
    """Return vectors for claims whose cached text and model both still match.

    The rows are selected by the indexed ``(corpus_id, embedding_model,
    embedding_dimension)`` prefix, which is exactly the set this corpus caches
    under the running model, and then filtered in Python against ``wanted``.

    The obvious alternative, passing the wanted ids and hashes as two SQL array
    parameters, was measured and is a production defect: with 5,000 claims that
    statement runs in about 380 ms for the first five executions of a connection
    and about 8,400 ms for every execution after that, with an unchanged plan.
    The cliff follows asyncpg's prepare threshold, not a plan switch, so it
    arrives in any long-lived server and never recovers. The arrays bought
    nothing, because the Python filter below already rejected any row that was
    not asked for, and it is also the stricter check now: the stored hash must
    equal the wanted hash, not merely be a member of the wanted set.
    """
    if not wanted:
        return {}
    rows = await db.execute(
        text("""
            SELECT claim_id, representation_hash, embedding::text AS vector
            FROM memory_claim_embeddings
            WHERE corpus_id = :corpus
              AND embedding_model = :model
              AND embedding_dimension = :dimension
        """),
        {
            "corpus": corpus_id,
            "model": model,
            "dimension": dimension,
        },
    )
    out: dict[str, list[float]] = {}
    for claim_id, stored_hash, vector in rows.all():
        # The same guarantees the SQL array membership test used to give, made
        # explicit: an unasked claim is skipped, and so is a row whose recorded
        # hash no longer matches the text about to be scored.
        if wanted.get(claim_id) != stored_hash:
            continue
        out[claim_id] = json.loads(vector)
    return out


async def load_cached_by_hash(
    db: AsyncSession, corpus_id: str, hashes: set[str], model: str, dimension: int
) -> dict[str, list[float]]:
    """Vectors for representations already cached under this model.

    Keyed by representation hash rather than by claim id, because the hash is
    the hash of the exact text that was embedded: two claims whose
    representations are the same text have the same vector, so the second one
    does not have to be encoded again.

    The array parameter holds hashes and nothing else, and it is bound in
    batches. `load_cached` binds no array at all because it is called on every
    read with the whole corpus's claims, and the array version of that query
    cost 22x at 5,000 claims once asyncpg started preparing it. Here the set
    is a document's worth of hashes, and a batch of them is an index lookup
    rather than a cliff.
    """
    if not hashes:
        return {}
    ordered = sorted(hashes)
    out: dict[str, list[float]] = {}
    for start in range(0, len(ordered), HASH_BATCH):
        batch = ordered[start : start + HASH_BATCH]
        rows = await db.execute(
            text("""
                SELECT representation_hash, embedding::text AS vector
                FROM memory_claim_embeddings
                WHERE corpus_id = :corpus
                  AND embedding_model = :model
                  AND embedding_dimension = :dimension
                  AND representation_hash = ANY(CAST(:hashes AS CHAR(64)[]))
            """),
            {
                "corpus": corpus_id,
                "model": model,
                "dimension": dimension,
                "hashes": batch,
            },
        )
        for stored_hash, vector in rows.all():
            out[stored_hash] = json.loads(vector)
    return out


async def embed_representations(
    db: AsyncSession,
    corpus_id: str,
    wanted: dict[str, str],
    texts: list[str],
    embedder,
) -> dict[str, list[float]]:
    """The vector for every representation, encoding only what is not cached.

    Three things are skipped, and all three are exact rather than approximate.

    A representation whose hash is already in `memory_claim_embeddings` under
    the running model is not encoded again: the row holds the hash of the
    exact text that was embedded, so the same text has the same vector.

    Two claims whose representations are the same text are encoded once
    between them rather than twice.

    And nothing here changes what a vector is. The values are the ones the
    model produced for those texts, whether this call encoded them or found
    them.
    """
    if not wanted:
        return {}
    pairs = list(zip(wanted.keys(), texts))
    cached = await load_cached_by_hash(
        db, corpus_id, set(wanted.values()), embedder.model_name, embedder.dimension
    )
    missing: dict[str, list[str]] = {}
    for claim_id, representation in pairs:
        if wanted[claim_id] in cached:
            continue
        missing.setdefault(representation, []).append(claim_id)
    vectors: dict[str, list[float]] = {}
    if missing:
        keys = list(missing)
        encoded = embedder.embed(keys)
        if len(encoded) != len(keys):
            raise ValueError("embedding count does not match representation count")
        for representation, vector in zip(keys, encoded):
            for claim_id in missing[representation]:
                vectors[claim_id] = vector
    for claim_id, _representation in pairs:
        if claim_id not in vectors:
            vectors[claim_id] = cached[wanted[claim_id]]
    return vectors


async def store(
    db: AsyncSession,
    corpus_id: str,
    items: dict[str, str],
    vectors: dict[str, list[float]],
    model: str,
    dimension: int,
    version: str,
    *,
    commit: bool = True,
) -> int:
    """Persist freshly computed vectors. Caller owns the transaction.

    One statement for the whole batch. The rows were written one at a time, and
    a repository of 78 documents produced 2,487 of them: 2,487 round trips for
    a write that is 2,487 rows.
    """
    rows = []
    for claim_id, vector in vectors.items():
        if claim_id not in items:
            continue
        rows.append(
            {
                "corpus": corpus_id,
                "claim": claim_id,
                "hash": items[claim_id],
                "model": model,
                "dimension": dimension,
                "version": version,
                "vector": _vector_literal(vector),
            }
        )
    if rows:
        await db.execute(text(INSERT), rows)
    if commit:
        await db.commit()
    return len(rows)


async def stats(db: AsyncSession, corpus_id: str) -> dict:
    row = await db.execute(
        text(
            "SELECT count(*) AS rows, "
            "count(DISTINCT embedding_model) AS models, "
            "min(embedding_dimension) AS min_dim, max(embedding_dimension) AS max_dim "
            "FROM memory_claim_embeddings WHERE corpus_id = :c"
        ),
        {"c": corpus_id},
    )
    data = row.mappings().one()
    return {k: v for k, v in data.items()}
