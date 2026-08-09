# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Database repository: CRUD operations for documents and chunks."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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
) -> str:
    """Insert or update a document and return its deterministic ID."""
    doc_hash = content_hash(body)
    doc_id = deterministic_doc_id(path, body)

    # Check if document exists and is unchanged
    result = await db.execute(
        text("SELECT 1 FROM documents WHERE id = :id AND content_hash = :hash"),
        {"id": doc_id, "hash": doc_hash},
    )
    if result.first():
        return ""

    # Upsert
    now = datetime.now(timezone.utc)

    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    doc_type = metadata.get("type", "note")
    if doc_type not in ("kaggle", "project", "note", "paper"):
        doc_type = "note"

    await db.execute(
        text("""
            INSERT INTO documents (
                id, title, path, document_type, date, summary, tags,
                git_repo, content_hash, last_indexed, updated_at
            )
            VALUES (
                :id, :title, :path, :doc_type, :date, :summary, :tags,
                :git_repo, :hash, :now, :now
            )
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                path = EXCLUDED.path,
                document_type = EXCLUDED.document_type,
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
            "doc_type": doc_type,
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


async def check_manifest(db: AsyncSession, path: str, content_hash: str) -> bool:
    """Check if file is already ingested with same content."""
    result = await db.execute(
        text("SELECT 1 FROM ingestion_manifest WHERE path = :path AND content_hash = :hash"),
        {"path": path, "hash": content_hash},
    )
    return result.first() is not None


async def update_manifest(
    db: AsyncSession, path: str, content_hash: str, doc_id: str, chunk_count: int
) -> None:
    """Update ingestion manifest after successful ingestion."""
    await db.execute(
        text("""
            INSERT INTO ingestion_manifest (path, content_hash, doc_id, chunk_count, last_ingested)
            VALUES (:path, :hash, :doc_id, :chunk_count, now())
            ON CONFLICT (path) DO UPDATE SET
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
        },
    )
    await db.commit()


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
    document_type: str,
    tags: list[str],
    chunks: list[dict],
    embedding_model: str,
    embedding_dimension: int,
    embedding_version: str,
) -> int:
    """Insert chunk records with full metadata."""
    if not chunks:
        return 0

    values = []
    for i, chunk in enumerate(chunks):
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
                "embedding": chunk.get("embedding"),
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
                :embedding::vector, :embedding_model, :embedding_dimension, :embedding_version
            )
        """),
        values,
    )
    await db.commit()
    return len(chunks)


async def search_chunks(
    db: AsyncSession,
    query_vector: list[float],
    k: int = 5,
    document_type: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """Semantic search with optional metadata filters."""
    where_clauses = ["c.embedding IS NOT NULL"]
    params = {"query": query_vector, "k": k}

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


async def get_document(db: AsyncSession, doc_id: str) -> Optional[dict]:
    """Fetch a document by ID."""
    result = await db.execute(
        text(
            "SELECT id, title, path, document_type, date, summary, tags, git_repo "
            "FROM documents WHERE id = :id"
        ),
        {"id": doc_id},
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


async def get_chunks_for_document(db: AsyncSession, doc_id: str) -> list[dict]:
    """Fetch all chunks for a document (for summarization)."""
    result = await db.execute(
        text(
            "SELECT text, heading_path, order_index "
            "FROM chunks WHERE doc_id = :doc_id "
            "ORDER BY order_index"
        ),
        {"doc_id": doc_id},
    )
    return [
        {"text": row[0], "heading_path": row[1], "order_index": row[2]} for row in result.fetchall()
    ]


async def get_related_documents(
    db: AsyncSession,
    doc_id: str,
    k: int = 5,
) -> list[dict]:
    """Find related documents via shared tags and document type."""
    result = await db.execute(
        text("""
            SELECT d.id, d.title, d.path, d.document_type, d.tags,
                   COUNT(*) as shared_tags
            FROM documents d
            JOIN chunks c ON c.doc_id = d.id
            WHERE d.id != :doc_id
              AND d.tags && (SELECT tags FROM documents WHERE id = :doc_id)
            GROUP BY d.id, d.title, d.path, d.document_type, d.tags
            ORDER BY shared_tags DESC, d.updated_at DESC
            LIMIT :k
        """),
        {"doc_id": doc_id, "k": k},
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
