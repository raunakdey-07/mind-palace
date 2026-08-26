# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Ingestion service for Markdown processing."""

from __future__ import annotations

import time
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

    async def _ingest_content(self, db, content: str, path: str, corpus_id: str) -> dict:
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

        if await check_manifest(db, path, doc_hash, corpus_id):
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
            corpus_id=corpus_id,
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

        await update_manifest(db, path, doc_hash, doc_id, count, corpus_id)

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
        corpus_id: str = "00000000000000000000000000000000000000000000000000000000default",
    ) -> dict:
        """Ingest a single Markdown file into the given corpus."""
        async with session_scope() as db:
            return await self._ingest_content(db, content, path or "unknown", corpus_id)

    async def ingest_repo(
        self,
        repo_path: str,
        corpus_id: str = "00000000000000000000000000000000000000000000000000000000default",
    ) -> dict:
        """Batch ingest all Markdown files from a repository into a corpus."""
        return await self.sync_repo(repo_path, corpus_id, delete_removed=False)

    async def sync_repo(
        self,
        repo_path: str,
        corpus_id: str = "00000000000000000000000000000000000000000000000000000000default",
        delete_removed: bool = True,
    ) -> dict:
        """Synchronize a corpus with a source directory.

        Reconciles indexed state against the source snapshot:

        - added:     new files are ingested
        - changed:   modified files are reprocessed, stale chunks replaced
        - unchanged: skipped via manifest content-hash match
        - deleted:   files removed from the source are removed from the index
                     (when ``delete_removed`` is true)

        Returns a machine-readable sync summary. A failed document never
        leaves a false 'successfully indexed' state.
        """
        start = time.perf_counter()
        repo_path = Path(repo_path)
        if not repo_path.exists():
            return {"success": False, "message": f"Path not found: {repo_path}"}

        added = changed = unchanged = deleted = failed = 0
        total_chunks = 0
        md_files = sorted(repo_path.rglob("*.md"))
        source_paths: set[str] = set()

        for md_file in md_files:
            rel_path = str(md_file.relative_to(repo_path))
            source_paths.add(rel_path)
            try:
                content = md_file.read_text(encoding="utf-8")
                doc_hash = content_hash(parse_markdown(content)[1])

                # Classify before ingesting so the summary is accurate.
                async with session_scope() as db:
                    prior = await check_manifest(db, rel_path, doc_hash, corpus_id)
                    known = await self._path_known(db, corpus_id, rel_path)

                if prior:
                    unchanged += 1
                    continue

                state = "changed" if known else "added"
                async with session_scope() as db:
                    result = await self._ingest_content(db, content, rel_path, corpus_id)

                if not result["success"]:
                    failed += 1
                    print(f"[ingest] Failed {md_file}: {result.get('message')}")
                    continue

                if state == "added":
                    added += 1
                else:
                    changed += 1
                total_chunks += result.get("chunk_count", 0)
            except Exception as e:
                failed += 1
                print(f"[ingest] Error processing {md_file}: {e}")

        if delete_removed:
            async with session_scope() as db:
                removed = await self._delete_stale_paths(db, corpus_id, source_paths)
            deleted = removed

        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "success": failed == 0,
            "corpus_id": corpus_id,
            "added": added,
            "changed": changed,
            "unchanged": unchanged,
            "deleted": deleted,
            "failed": failed,
            "chunk_count": total_chunks,
            "duration_ms": duration_ms,
            "message": (
                f"sync: +{added} ~{changed} ={unchanged} -{deleted} "
                f"!{failed}, {total_chunks} chunks"
            ),
        }

    @staticmethod
    async def _path_known(db, corpus_id: str, path: str) -> bool:
        from sqlalchemy import text

        result = await db.execute(
            text("SELECT 1 FROM documents WHERE corpus_id = :c AND path = :p"),
            {"c": corpus_id, "p": path},
        )
        return result.first() is not None

    @staticmethod
    async def _delete_stale_paths(db, corpus_id: str, keep_paths: set[str]) -> int:
        """Delete indexed documents, chunks, and manifest entries whose source
        paths no longer exist in the source directory.

        Also purges orphaned manifest rows (manifest entry survives but the
        document is gone) — a stale manifest must never cause a file to be
        reported as 'unchanged' when it is not actually indexed.
        """
        from sqlalchemy import text

        result = await db.execute(
            text("SELECT id, path FROM documents WHERE corpus_id = :c"),
            {"c": corpus_id},
        )
        stale = [(row[0], row[1]) for row in result.fetchall() if row[1] not in keep_paths]
        for doc_id, path in stale:
            await delete_chunks_for_doc(db, doc_id)
            await db.execute(text("DELETE FROM documents WHERE id = :d"), {"d": doc_id})
            await db.execute(
                text("DELETE FROM ingestion_manifest WHERE doc_id = :d"), {"d": doc_id}
            )

        # Orphaned manifests: no matching document (doc_id NULL or dangling).
        keep_list = sorted(keep_paths) or ["__none__"]
        await db.execute(
            text("""
                DELETE FROM ingestion_manifest m
                WHERE m.corpus_id = :c
                  AND (m.path <> ALL(:keep))
                  AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = m.doc_id)
            """),
            {"c": corpus_id, "keep": keep_list},
        )

        if stale:
            await db.commit()
        return len(stale)
