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
    return f"{claim_text} {key} {path}"


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

    A claim with no row, a changed representation, a different model, or a
    different dimension is simply absent from the result, and the caller
    embeds it. Nothing here can return a vector that a fresh embed would not.
    """
    if not wanted:
        return {}
    rows = await db.execute(
        text("""
            SELECT claim_id, embedding::text AS vector
            FROM memory_claim_embeddings
            WHERE corpus_id = :corpus
              AND claim_id = ANY(CAST(:ids AS CHAR(64)[]))
              AND embedding_model = :model
              AND embedding_dimension = :dimension
              AND representation_hash = ANY(CAST(:hashes AS CHAR(64)[]))
            """),
        {
            "corpus": corpus_id,
            "ids": list(wanted),
            "hashes": list(wanted.values()),
            "model": model,
            "dimension": dimension,
        },
    )
    out: dict[str, list[float]] = {}
    for claim_id, vector in rows.all():
        # A row whose hash is in the request but whose claim has since changed
        # is filtered again here, so a stale row can never be paired with a
        # new claim even if the table were edited directly.
        if wanted.get(claim_id) is None:
            continue
        out[claim_id] = json.loads(vector)
    return out


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
    """Persist freshly computed vectors. Caller owns the transaction."""
    written = 0
    for claim_id, vector in vectors.items():
        if claim_id not in items:
            continue
        await db.execute(
            text(INSERT),
            {
                "corpus": corpus_id,
                "claim": claim_id,
                "hash": items[claim_id],
                "model": model,
                "dimension": dimension,
                "version": version,
                "vector": _vector_literal(vector),
            },
        )
        written += 1
    if commit:
        await db.commit()
    return written


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
