"""Rebuild the disposable live index from the authoritative memory archive.

The archive is the source of truth for rehydration. This service writes only
``documents``, ``chunks``, and ``ingestion_manifest``; it never calls
``record_version`` and therefore creates no new observation or feed event. It
also rebuilds the claim representation cache, which is derived from immutable
claim text.

A caller owns the transaction. The service acquires the corpus lock, replaces the
live projection for archived paths, and leaves live-only paths untouched. That
last rule matters while L0 archiving is opt-in: the command must not silently
delete documents that have never been observed by the archive.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.ingestion import IngestionService
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
    extract_title,
    parse_markdown,
)
from api.services.repository import (
    content_hash,
    insert_chunks,
    update_manifest,
    upsert_document,
)

logger = logging.getLogger(__name__)


async def _archived_documents(db: AsyncSession, corpus_id: str) -> list[dict[str, Any]]:
    result = await db.execute(
        text("""
            SELECT d.path, v.document_id, v.content, v.metadata, v.event
            FROM memory_documents AS d
            LEFT JOIN LATERAL (
                SELECT mv.document_id, mv.content, mv.metadata, mv.event
                FROM memory_versions AS mv
                WHERE mv.corpus_id = d.corpus_id
                  AND mv.memory_document_id = d.id
                ORDER BY mv.version_number DESC
                LIMIT 1
            ) AS v ON TRUE
            WHERE d.corpus_id = :corpus
            ORDER BY d.path
            """),
        {"corpus": corpus_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def rehydrate_corpus(
    db: AsyncSession,
    corpus_id: str,
    *,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Rebuild archived paths in the live projection.

    The caller must invoke this inside a transaction and must not commit or
    roll back here. ``embedder`` is injectable for deterministic offline tests.
    """
    from api.services import memory

    await memory.lock_corpus(db, corpus_id)
    archived = await _archived_documents(db, corpus_id)
    service = IngestionService(memory_enabled=False)
    if embedder is not None:
        service.embedder = embedder

    rebuilt_documents = 0
    rebuilt_chunks = 0
    removed_documents = 0
    deleted_archive_paths = 0
    cached_claims = await backfill_claim_embeddings(db, corpus_id)

    for row in archived:
        path = row["path"]
        # A pre-archive legacy document has no version row. It is not part of
        # the authoritative projection this command rebuilds, so leave it alone.
        if row["event"] is None:
            continue

        # Replace the L2 projection for this archived path. ON DELETE CASCADE
        # removes old chunks; the manifest is removed first for clarity.
        await db.execute(
            text("DELETE FROM ingestion_manifest WHERE corpus_id = :c AND path = :p"),
            {"c": corpus_id, "p": path},
        )
        await db.execute(
            text("DELETE FROM documents WHERE corpus_id = :c AND path = :p"),
            {"c": corpus_id, "p": path},
        )

        if row["event"] == "DELETED" or not row["content"]:
            deleted_archive_paths += 1
            removed_documents += 1
            continue

        document_id = row["document_id"]
        if not document_id:
            raise ValueError(f"archived document {path!r} has no live document identity")

        metadata, body = parse_markdown(row["content"])
        if not metadata and row["metadata"]:
            metadata = dict(row["metadata"])
        if not body.strip():
            deleted_archive_paths += 1
            removed_documents += 1
            continue

        stored_document_id = await upsert_document(
            db,
            extract_title(metadata),
            path,
            body,
            metadata,
            corpus_id=corpus_id,
            force=True,
            document_id=document_id,
        )
        if stored_document_id != document_id:
            raise ValueError(
                f"archived document identity changed for {path!r}: "
                f"expected {document_id}, got {stored_document_id}"
            )

        sections = extract_sections_with_paths(body)
        chunk_data = chunk_with_heading_paths(sections)
        embeddings = service.embedder.embed([chunk["text"] for chunk in chunk_data])
        if len(embeddings) != len(chunk_data):
            raise ValueError("embedding count does not match chunk count")

        doc_type = metadata.get("document_type") or metadata.get("type") or "note"
        if doc_type not in {"kaggle", "project", "note", "paper"}:
            doc_type = "note"
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",")]

        records = [
            {**chunk, "order_index": index, "embedding": embedding}
            for index, (chunk, embedding) in enumerate(zip(chunk_data, embeddings))
        ]
        count = await insert_chunks(
            db,
            stored_document_id,
            doc_type,
            tags,
            records,
            service.embedder.model_name,
            service.embedder.dimension,
            getattr(service.embedder, "version", "rehydrate"),
            commit=False,
        )
        await update_manifest(
            db,
            path,
            content_hash(row["content"]),
            stored_document_id,
            count,
            corpus_id,
            commit=False,
        )
        rebuilt_documents += 1
        rebuilt_chunks += count

    return {
        "corpus_id": corpus_id,
        "archived_paths": len(archived),
        "rebuilt_documents": rebuilt_documents,
        "rebuilt_chunks": rebuilt_chunks,
        "removed_documents": removed_documents,
        "deleted_archive_paths": deleted_archive_paths,
        "cached_claims": cached_claims,
    }


async def backfill_claim_embeddings(db: AsyncSession, corpus_id: str) -> int:
    """Rebuild the L2 claim representation cache from the archive.

    Pure L2: the rows are derived from immutable claim text, so deleting them
    and running this again produces the same cache. A failure is reported, not
    raised, because the cache is an optimization and rehydration itself is what
    the caller actually needs.
    """
    from api.services.claim_embeddings import pending, store
    from api.services.embedder import Embedder

    try:
        wanted, texts = await pending(db, corpus_id)
        if not wanted:
            return 0
        embedder = Embedder()
        vectors = dict(zip(wanted, embedder.embed(texts)))
        return await store(
            db,
            corpus_id,
            wanted,
            vectors,
            embedder.model_name,
            embedder.dimension,
            getattr(embedder, "version", "rehydrate"),
            commit=False,
        )
    except Exception:  # noqa: BLE001 - L2 backfill must not break rehydration
        logger.warning("claim_embedding_backfill_failed", exc_info=True)
        return 0
