# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Database repository: CRUD operations for documents and chunks."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.corpora import DEFAULT_CORPUS_ID


def content_hash(content: str) -> str:
    """Deterministic SHA-256 hash of content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()


def deterministic_doc_id(path: str, content: str) -> str:
    """Generate a deterministic document ID from path + content hash."""
    return hashlib.sha256(f"{path}:{content}".encode()).hexdigest()


async def upsert_document(
    db: AsyncSession,
    title: str,
    path: str,
    body: str,
    metadata: dict,
    corpus_id: str = DEFAULT_CORPUS_ID,
    *,
    force: bool = False,
    document_id: str | None = None,
) -> str:
    """Insert or update a document and return its (deterministic) ID.

    Documents are keyed by ``(corpus_id, path)``: re-ingesting changed content
    updates the existing row in place rather than colliding with the unique
    constraint. The row keeps its original id on update so chunk/manifest
    references stay valid.
    """
    doc_hash = content_hash(body)
    doc_id = document_id or (
        deterministic_doc_id(path, body)
        if corpus_id == DEFAULT_CORPUS_ID
        else content_hash(f"{corpus_id}:{path}")
    )

    # Unchanged content -> nothing to do (path-keyed within this corpus)
    result = await db.execute(
        text(
            "SELECT 1 FROM documents WHERE corpus_id = :corpus AND path = :path "
            "AND content_hash = :hash"
        ),
        {"corpus": corpus_id, "path": path, "hash": doc_hash},
    )
    if result.first() and not force:
        return ""

    now = datetime.now(timezone.utc)

    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    doc_type = metadata.get("document_type") or metadata.get("type") or "note"
    if doc_type not in ("kaggle", "project", "note", "paper"):
        doc_type = "note"

    await db.execute(
        text("""
            INSERT INTO documents (
                id, title, path, document_type, date, summary, tags,
                git_repo, content_hash, last_indexed, updated_at, corpus_id
            )
            VALUES (
                COALESCE(
                    (SELECT id FROM documents WHERE corpus_id = :corpus AND path = :path),
                    :id
                ),
                :title, :path, :doc_type, :date, :summary, :tags,
                :git_repo, :hash, :now, :now, :corpus
            )
            ON CONFLICT (corpus_id, path) DO UPDATE SET
                title = EXCLUDED.title,
                document_type = EXCLUDED.document_type,
                date = EXCLUDED.date,
                summary = EXCLUDED.summary,
                tags = EXCLUDED.tags,
                git_repo = EXCLUDED.git_repo,
                content_hash = EXCLUDED.content_hash,
                last_indexed = :now,
                updated_at = :now
            """),
        {
            "id": doc_id,
            "title": metadata.get("title", title),
            "path": path,
            "doc_type": doc_type,
            "date": metadata.get("date"),
            "summary": metadata.get("summary", ""),
            "tags": tags,
            "git_repo": metadata.get("git_repo", ""),
            "hash": doc_hash,
            "now": now,
            "corpus": corpus_id,
        },
    )

    # Return the id actually stored for this (corpus, path): on update the
    # existing row keeps its original id so chunk/manifest references stay valid.
    result = await db.execute(
        text("SELECT id FROM documents WHERE corpus_id = :corpus AND path = :path"),
        {"corpus": corpus_id, "path": path},
    )
    return result.scalar_one()


async def check_manifest(
    db: AsyncSession,
    path: str,
    content_hash: str,
    corpus_id: str = DEFAULT_CORPUS_ID,
) -> bool:
    """Check if file is already ingested with same content in this corpus."""
    result = await db.execute(
        text(
            "SELECT 1 FROM ingestion_manifest WHERE corpus_id = :corpus "
            "AND path = :path AND content_hash = :hash"
        ),
        {"corpus": corpus_id, "path": path, "hash": content_hash},
    )
    return result.first() is not None


async def update_manifest(
    db: AsyncSession,
    path: str,
    content_hash: str,
    doc_id: str,
    chunk_count: int,
    corpus_id: str = DEFAULT_CORPUS_ID,
    *,
    commit: bool = True,
) -> None:
    """Update ingestion manifest after successful ingestion."""
    await db.execute(
        text("""
            INSERT INTO ingestion_manifest (
                path, content_hash, doc_id, chunk_count, last_ingested, corpus_id
            )
            VALUES (:path, :hash, :doc_id, :chunk_count, now(), :corpus)
            ON CONFLICT (corpus_id, path) DO UPDATE SET
                content_hash = EXCLUDED.content_hash,
                doc_id = EXCLUDED.doc_id,
                chunk_count = EXCLUDED.chunk_count,
                last_ingested = now()
        """),
        {
            "path": path,
            "hash": content_hash,
            "doc_id": doc_id,
            "chunk_count": chunk_count,
            "corpus": corpus_id,
        },
    )
    if commit:
        await db.commit()


async def delete_chunks_for_doc(
    db: AsyncSession, corpus_id: str, doc_id: str, *, commit: bool = True
) -> None:
    """Remove all chunks belonging to a document, within one corpus.

    `chunks` has no corpus column of its own, so the boundary is reached through
    the parent document -- which is why `corpus_id` is required. `doc_id` is a
    global primary key, so without it this delete could remove another tenant's
    chunks if a caller ever passed an id it had not just resolved.
    """
    await db.execute(
        text(
            "DELETE FROM chunks WHERE doc_id = :doc_id AND doc_id IN "
            "(SELECT id FROM documents WHERE id = :doc_id AND corpus_id = :corpus_id)"
        ),
        {"doc_id": doc_id, "corpus_id": corpus_id},
    )
    if commit:
        await db.commit()


async def insert_chunks(
    db: AsyncSession,
    corpus_id: str,
    doc_id: str,
    document_type: str,
    tags: list[str],
    chunks: list[dict],
    embedding_model: str,
    embedding_dimension: int,
    embedding_version: str,
    *,
    commit: bool = True,
) -> int:
    """Insert chunk records with full metadata.

    `corpus_id` is required and is *verified* against the document rather than
    trusted: `chunks` has no corpus column, so without that check a caller could
    attach another tenant's document id and write its text into a corpus it does
    not own.
    """
    if not chunks:
        return 0

    owned = (
        await db.execute(
            text("SELECT 1 FROM documents WHERE id = :doc_id AND corpus_id = :corpus_id"),
            {"doc_id": doc_id, "corpus_id": corpus_id},
        )
    ).first()
    if owned is None:
        raise ValueError(f"document {doc_id} does not belong to corpus {corpus_id}")

    values = []
    for i, chunk in enumerate(chunks):
        embedding = chunk.get("embedding")
        # asyncpg/pgvector expects the vector as its text representation.
        if embedding is not None and not isinstance(embedding, str):
            embedding = "[" + ",".join(str(float(x)) for x in embedding) + "]"
        values.append(
            {
                "doc_id": doc_id,
                "order_index": chunk.get("order_index", i),
                "text": chunk["text"],
                "heading_path": chunk.get("heading_path"),
                "document_type": document_type,
                "source_url": chunk.get("source_url"),
                "tags": tags,
                "language": chunk.get("language", "en"),
                "token_count": chunk.get("token_count", 0),
                "embedding": embedding,
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
                "embedding_version": embedding_version,
            }
        )

    await db.execute(
        text("""
            INSERT INTO chunks (
                id, doc_id, order_index, text, heading_path, document_type,
                source_url, tags, language, token_count,
                embedding, embedding_model, embedding_dimension, embedding_version
            )
            VALUES (
                gen_random_uuid(), :doc_id, :order_index, :text, :heading_path, :document_type,
                :source_url, :tags, :language, :token_count,
                CAST(:embedding AS vector),
                :embedding_model, :embedding_dimension, :embedding_version
            )
        """),
        values,
    )
    if commit:
        await db.commit()
    return len(chunks)


#: How many chunks one backfill gives vectors to.
#:
#: The vectors are a cache, and the cache is filled from the chunks' own text.
#: A corpus with more chunks than this backfills over several searches rather
#: than in one enormous request.
CHUNK_BACKFILL_BATCH = 2000


async def backfill_chunk_embeddings(
    db: AsyncSession,
    corpus_id: str | None = None,
    *,
    embedder=None,
) -> int:
    """Give the chunks that have no vector one, and return how many it gave.

    A corpus synced without `--embed-chunks` has chunks with no vector, and the
    chunk-level search reads only the chunks that have one. This fills the gap
    the first time the search runs, so the search answers instead of silently
    returning nothing.

    The vectors are the ones an ingest would have computed: the chunk's own
    text, through the same embedder. The chunk texts are immutable, so the
    vector a chunk gets here is the vector it would have got then.

    It runs in the caller's transaction and commits it. The vectors are a cache,
    and a cache written in a transaction nobody commits is a cache written on
    every read; and the search has to see them, which a transaction that is
    still open would not allow.
    """
    from api.services.embedder import Embedder

    params: dict = {}
    where = "c.embedding IS NULL"
    if corpus_id is not None:
        where += " AND d.corpus_id = :corpus_id"
        params["corpus_id"] = corpus_id
    rows = (
        await db.execute(
            text(
                "SELECT c.id, c.text FROM chunks c JOIN documents d ON c.doc_id = d.id "
                f"WHERE {where} LIMIT :limit"
            ),
            {**params, "limit": CHUNK_BACKFILL_BATCH},
        )
    ).all()
    if not rows:
        return 0
    embedder = embedder or Embedder()
    vectors = embedder.embed([row[1] for row in rows])
    if len(vectors) != len(rows):
        raise ValueError("embedding count does not match chunk count")
    await db.execute(
        text("""
            UPDATE chunks SET embedding = CAST(:vector AS vector),
                              embedding_model = :model,
                              embedding_dimension = :dimension,
                              embedding_version = :version
            WHERE id = :id
        """),
        [
            {
                "id": row[0],
                "vector": "[" + ",".join(repr(float(x)) for x in vector) + "]",
                "model": embedder.model_name,
                "dimension": embedder.dimension,
                "version": getattr(embedder, "version", "backfill"),
            }
            for row, vector in zip(rows, vectors)
        ],
    )
    await db.commit()
    return len(rows)


async def search_chunks(
    db: AsyncSession,
    corpus_id: str,
    query_vector: list[float],
    k: int = 5,
    document_type: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """Semantic search with optional metadata filters, within one corpus.

    `corpus_id` is required. This is the only unscoped semantic search in the
    codebase, and it already joins `documents`; making the corpus a required
    argument is the difference between a filter someone can forget and a
    boundary the call cannot be written without.
    """
    where_clauses = ["c.embedding IS NOT NULL", "d.corpus_id = :corpus_id"]
    params = {"query": query_vector, "k": k, "corpus_id": corpus_id}

    if document_type:
        where_clauses.append("c.document_type = :doc_type")
        params["doc_type"] = document_type

    if tags:
        where_clauses.append("c.tags && :tags")
        params["tags"] = tags

    where_sql = " AND ".join(where_clauses)

    result = await db.execute(
        text(f"""
            SELECT c.text, c.heading_path, c.document_type, c.source_url, c.tags,
                   d.title, d.path, d.document_type as doc_type,
                   1 - (c.embedding <=> :query::vector) AS score
            FROM chunks c
            JOIN documents d ON c.doc_id = d.id
            WHERE {where_sql}
            ORDER BY c.embedding <=> :query::vector
            LIMIT :k
        """),
        params,
    )
    rows = result.fetchall()
    return [
        {
            "text": row[0],
            "heading_path": row[1],
            "chunk_document_type": row[2],
            "source_url": row[3],
            "chunk_tags": row[4],
            "source_title": row[5],
            "source_path": row[6],
            "source_document_type": row[7],
            "score": float(row[8]),
        }
        for row in rows
    ]


async def get_document(db: AsyncSession, corpus_id: str, doc_id: str) -> Optional[dict]:
    """Fetch a document by ID, within one corpus.

    `corpus_id` is required. `documents.id` is a global primary key, so a lookup
    by id alone resolves in every corpus at once; making the corpus a required
    argument turns "works in a single-tenant deployment" into "cannot be called
    at all without saying which tenant you mean".
    """
    result = await db.execute(
        text(
            "SELECT id, title, path, document_type, date, summary, tags, git_repo "
            "FROM documents WHERE id = :id AND corpus_id = :corpus_id"
        ),
        {"id": doc_id, "corpus_id": corpus_id},
    )
    row = result.first()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "path": row[2],
        "document_type": row[3],
        "date": row[4],
        "summary": row[5],
        "tags": row[6],
        "git_repo": row[7],
    }


async def get_chunks_for_document(db: AsyncSession, corpus_id: str, doc_id: str) -> list[dict]:
    """Fetch all chunks for a document (for summarization), within one corpus.

    `chunks` has no corpus column, so the boundary is the parent document's --
    which is why `corpus_id` is required here rather than inferred.
    """
    result = await db.execute(
        text(
            "SELECT c.text, c.heading_path, c.order_index "
            "FROM chunks c JOIN documents d ON c.doc_id = d.id "
            "WHERE c.doc_id = :doc_id AND d.corpus_id = :corpus_id "
            "ORDER BY c.order_index"
        ),
        {"doc_id": doc_id, "corpus_id": corpus_id},
    )
    return [
        {"text": row[0], "heading_path": row[1], "order_index": row[2]} for row in result.fetchall()
    ]


async def get_related_documents(
    db: AsyncSession,
    corpus_id: str,
    doc_id: str,
    k: int = 5,
) -> list[dict]:
    """Find related documents via shared tags and document type, within one corpus.

    `corpus_id` is required, and it constrains *both* sides: the outer scan and
    the tag sub-query that reads the source document. Scoping only the outer
    query would still let the sub-query read another tenant's tags.
    """
    result = await db.execute(
        text("""
            SELECT d.id, d.title, d.path, d.document_type, d.tags,
                   COUNT(*) as shared_tags
            FROM documents d
            JOIN chunks c ON c.doc_id = d.id
            WHERE d.id != :doc_id
              AND d.corpus_id = :corpus_id
              AND d.tags && (
                    SELECT tags FROM documents
                    WHERE id = :doc_id AND corpus_id = :corpus_id
              )
            GROUP BY d.id, d.title, d.path, d.document_type, d.tags
            ORDER BY shared_tags DESC, d.updated_at DESC
            LIMIT :k
        """),
        {"doc_id": doc_id, "corpus_id": corpus_id, "k": k},
    )
    return [
        {
            "id": row[0],
            "title": row[1],
            "path": row[2],
            "document_type": row[3],
            "tags": row[4],
            "shared_tags": row[5],
        }
        for row in result.fetchall()
    ]
