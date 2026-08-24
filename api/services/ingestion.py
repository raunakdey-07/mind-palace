# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Ingestion service for Markdown processing."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from api.services.db import session_scope
from api.services.embedder import Embedder
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
    extract_title,
    parse_markdown,
)
from api.services.repository import (
    check_manifest,
    content_hash,
    delete_chunks_for_doc,
    insert_chunks,
    update_manifest,
    upsert_document,
)

# Fallback document type when frontmatter is missing or invalid.
DEFAULT_DOC_TYPE = "note"


class IngestionService:
    """Service for ingesting Markdown documents."""

    def __init__(self):
        self.embedder = Embedder()

    async def _ingest_content(self, db, content: str, path: str) -> dict:
        """Shared ingestion pipeline for one parsed document.

        The caller owns the session lifetime. Returns a result dict with
        ``success``, ``message``, and (on success) ``document_id``/``chunk_count``.
        """
        metadata, body = parse_markdown(content)
        doc_hash = content_hash(body)

        if not body.strip():
            return {
                "success": True,
                "message": f"Skipped empty document: {path}",
                "chunk_count": 0,
            }

        if await check_manifest(db, path, doc_hash):
            return {
                "success": True,
                "message": "Document unchanged (manifest), skipped",
                "chunk_count": 0,
            }

        doc_id = await upsert_document(
            db,
            extract_title(metadata),
            path,
            body,
            metadata,
        )
        if not doc_id:
            return {
                "success": True,
                "message": "Document unchanged, skipped",
                "chunk_count": 0,
            }

        # Replace stale chunks with the freshly computed ones.
        await delete_chunks_for_doc(db, doc_id)

        sections = extract_sections_with_paths(body)
        chunks = chunk_with_heading_paths(sections)
        chunk_texts = [c["text"] for c in chunks]
        embeddings = self.embedder.embed(chunk_texts)

        chunk_records = [
            {
                "text": c["text"],
                "order_index": i,
                "heading_path": c["heading_path"],
                "token_count": c["token_count"],
                "embedding": emb,
            }
            for i, (c, emb) in enumerate(zip(chunks, embeddings))
        ]

        doc_type = metadata.get("type") or metadata.get("document_type") or DEFAULT_DOC_TYPE
        tags = metadata.get("tags", [])

        count = await insert_chunks(
            db,
            doc_id,
            doc_type,
            tags,
            chunk_records,
            self.embedder._model.__class__.__name__,
            self.embedder.dimension,
            "1.0",
        )

        await update_manifest(db, path, doc_hash, doc_id, count)

        return {
            "success": True,
            "document_id": doc_id,
            "chunk_count": count,
            "message": f"Ingested {count} chunks",
        }

    async def ingest_file(
        self,
        content: str,
        path: Optional[str] = None,
    ) -> dict:
        """Ingest a single Markdown file."""
        async with session_scope() as db:
            return await self._ingest_content(db, content, path or "unknown")

    async def ingest_repo(self, repo_path: str) -> dict:
        """Batch ingest all Markdown files from a repository."""
        repo_path = Path(repo_path)
        if not repo_path.exists():
            return {"success": False, "message": f"Path not found: {repo_path}"}

        total_chunks = 0
        ingested = 0
        failed = 0
        md_files = sorted(repo_path.rglob("*.md"))

        for md_file in md_files:
            try:
                content = md_file.read_text(encoding="utf-8")
                rel_path = str(md_file.relative_to(repo_path))

                async with session_scope() as db:
                    result = await self._ingest_content(db, content, rel_path)

                if not result["success"]:
                    failed += 1
                    print(f"[ingest] Failed {md_file}: {result.get('message')}")
                    continue

                total_chunks += result.get("chunk_count", 0)
                if result.get("chunk_count"):
                    ingested += 1
            except Exception as e:
                failed += 1
                # Continue with other files; do not leave a partial manifest entry.
                print(f"[ingest] Error processing {md_file}: {e}")

        message = f"Ingested {ingested}/{len(md_files)} files, {total_chunks} chunks total"
        if failed:
            message += f", {failed} failed"

        return {
            "success": failed == 0,
            "chunk_count": total_chunks,
            "message": message,
        }
