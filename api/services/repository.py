"""Database repository: CRUD operations for documents and chunks."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from api.services.db import init_db
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession


def content_hash(content: str) -> str:
    """Deterministic SHA-256 hash of content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()


async def upsert_document(
    db: AsyncSession,
    title: str,
    path: str,
    body: str,
    metadata: dict,
) -> str:
    """Insert or update a document and return its ID."""
    doc_hash = content_hash(body)

    # Check if document exists and is unchanged
    result = await db.execute(
        select(func.count())
        .select_from(text("documents"))
        .where(text("documents.path = :path AND documents.content_hash = :hash")),
        {"path": path, "hash": doc_hash},
    )
    unchanged = result.scalar() > 0
    if unchanged:
        return ""

    # Upsert
    doc_id = str(uuid4())
    now = datetime.now(timezone.utc)

    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    await db.execute(
        text("""
            INSERT INTO documents (id, title, path, type, date, summary, tags, git_repo, content_hash, last_indexed, updated_at)
            VALUES (:id, :title, :path, 'markdown', :date, :summary, :tags, :git_repo, :hash, :now, :now)
            ON CONFLICT (path) DO UPDATE SET
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                tags = EXCLUDED.tags,
                content_hash = EXCLUDED.content_hash,
                last_indexed = :now,
                updated_at = :now
        """),
        {
            "id": doc_id,
            "title": metadata.get("title", title),
            "path": path,
            "date": metadata.get("date"),
            "summary": metadata.get("summary", ""),
            "tags": tags,
            "git_repo": metadata.get("git_repo", ""),
            "hash": doc_hash,
            "now": now,
        },
    )
    await db.commit()
    return doc_id


async def delete_chunks_for_doc(db: AsyncSession, doc_id: str) -> None:
    """Remove all chunks belonging to a document."""
    await db.execute(
        text("DELETE FROM chunks WHERE doc_id = :doc_id"),
        {"doc_id": doc_id},
    )
    await db.commit()


async def insert_chunks(
    db: AsyncSession,
    doc_id: str,
    chunks: list[dict],
) -> int:
    """Insert chunk records with embeddings. Each chunk dict has:
    - text: str
    - order_index: int
    - section: str (optional)
    - embedding: list[float]
    """
    if not chunks:
        return 0

    values = []
    for i, chunk in enumerate(chunks):
        values.append(
            {
                "doc_id": doc_id,
                "order_index": chunk.get("order_index", i),
                "text": chunk["text"],
                "section": chunk.get("section"),
                "embedding": chunk.get("embedding"),
            }
        )

    await db.execute(
        text("""
            INSERT INTO chunks (id, doc_id, order_index, text, section, embedding)
            VALUES (gen_random_uuid(), :doc_id, :order_index, :text, :section, :embedding::vector)
        """),
        values,
    )
    await db.commit()
    return len(chunks)


async def search_chunks(
    db: AsyncSession,
    query_vector: list[float],
    k: int = 5,
) -> list[dict]:
    """Semantic search using cosine similarity on embeddings."""
    result = await db.execute(
        text("""
            SELECT c.text, c.section, d.title, d.path,
                   1 - (c.embedding <=> :query::vector) AS score
            FROM chunks c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> :query::vector
            LIMIT :k
        """),
        {"query": query_vector, "k": k},
    )
    rows = result.fetchall()
    return [
        {
            "text": row[0],
            "section": row[1],
            "source_title": row[2],
            "source_path": row[3],
            "score": float(row[4]),
        }
        for row in rows
    ]
