# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Ingestion service for Markdown processing."""

from __future__ import annotations

from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.parser import (
    chunk_with_heading_paths,
    parse_markdown,
)
from api.services.repository import (
    check_manifest,
    delete_chunks_for_doc,
    insert_chunks,
    update_manifest,
    upsert_document,
)


class IngestionService:
    """Service for ingesting Markdown documents."""

    def __init__(self):
        self.embedder = Embedder()

    async def ingest_file(
        self,
        content: str,
        path: str | None = None,
    ) -> dict:
        """Ingest a single Markdown file."""
        # Parse and validate frontmatter
        frontmatter_obj, body, sections = parse_markdown(content)

        async for db in [get_async_db()]:
            async with db:
                # Check manifest for incremental ingestion
                from api.services.repository import content_hash

                doc_hash = content_hash(body)

                if await check_manifest(db, path or "unknown", doc_hash):
                    return {
                        "success": True,
                        "message": "Document unchanged (manifest), skipped",
                        "chunk_count": 0,
                    }

                # Upsert document
                doc_id = await upsert_document(
                    db,
                    frontmatter_obj.title,
                    path or "unknown",
                    body,
                    frontmatter_obj.model_dump(),
                )
                if not doc_id:
                    return {
                        "success": True,
                        "message": "Document unchanged, skipped",
                        "chunk_count": 0,
                    }

                # Delete old chunks
                await delete_chunks_for_doc(db, doc_id)

                # Chunk with heading paths and embed
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
                count = await insert_chunks(
                    db,
                    doc_id,
                    frontmatter_obj.document_type,
                    frontmatter_obj.tags,
                    chunk_records,
                    self.embedder._model.__class__.__name__,
                    self.embedder.dimension,
                    "1.0",
                )

                await update_manifest(db, path or "unknown", doc_hash, doc_id, count)

                return {
                    "success": True,
                    "document_id": doc_id,
                    "chunk_count": count,
                    "message": f"Ingested {count} chunks",
                }

    async def ingest_repo(self, repo_path: str) -> dict:
        """Batch ingest all Markdown files from a repository."""
        from pathlib import Path

        repo_path = Path(repo_path)
        if not repo_path.exists():
            return {"success": False, "message": f"Path not found: {repo_path}"}

        total_chunks = 0
        md_files = list(repo_path.rglob("*.md"))

        for md_file in md_files:
            try:
                content = md_file.read_text(encoding="utf-8")
                rel_path = str(md_file.relative_to(repo_path))

                frontmatter_obj, body, sections = parse_markdown(content)

                async for db in [get_async_db()]:
                    async with db:
                        from api.services.repository import content_hash

                        doc_hash = content_hash(body)

                        if await check_manifest(db, rel_path, doc_hash):
                            continue

                        doc_id = await upsert_document(
                            db,
                            frontmatter_obj.title,
                            rel_path,
                            body,
                            frontmatter_obj.model_dump(),
                        )
                        if not doc_id:
                            continue

                        await delete_chunks_for_doc(db, doc_id)

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
                        count = await insert_chunks(
                            db,
                            doc_id,
                            frontmatter_obj.document_type,
                            frontmatter_obj.tags,
                            chunk_records,
                            self.embedder._model.__class__.__name__,
                            self.embedder.dimension,
                            "1.0",
                        )

                        await update_manifest(db, rel_path, doc_hash, doc_id, count)
                        total_chunks += count
            except Exception as e:  # noqa: BLE001
                # Continue with other files
                print(f"[ingest] Error processing {md_file}: {e}")

        return {
            "success": True,
            "chunk_count": total_chunks,
            "message": f"Ingested {len(md_files)} files, {total_chunks} chunks total",
        }
