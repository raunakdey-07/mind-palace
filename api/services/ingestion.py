# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Ingestion service for Markdown processing."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from api.services.corpora import DEFAULT_CORPUS_ID
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

logger = logging.getLogger(__name__)

# Fallback document type when frontmatter is missing or invalid.
DEFAULT_DOC_TYPE = "note"
MAX_SYNC_ERRORS = 50
#: Below this many written rows a sync cannot have moved the planner off a
#: heuristic estimate, so statistics are not worth the ANALYZE.
STATISTICS_REFRESH_MINIMUM = 200


def _sync_error(path: str, exc: Exception) -> str:
    """Return a bounded, non-sensitive sync failure description."""
    reason = getattr(exc, "reason", None) or type(exc).__name__
    return f"{path}: {reason}"


class IngestionService:
    """Service for ingesting Markdown documents."""

    def __init__(self, *, memory_enabled: bool = False, require_embeddings: bool = True):
        self.embedder = Embedder()
        self.memory_enabled = memory_enabled
        # `remember` records authoritative memory and sets this False, so a first
        # memory does not depend on a model download. Bulk ingestion leaves it True:
        # silently accepting a directory of documents and producing a corpus that
        # cannot be searched would be worse than failing.
        self.require_embeddings = require_embeddings

    def _embed_records(self, records: list[dict]) -> tuple[str | None, int | None]:
        """Embed chunk records, or skip the model entirely when the caller allows it.

        Embeddings are L2: `rehydrate` rebuilds them from the archive, and the read
        path already treats them as an optimization. So when the caller has said it
        does not need them, the model is never loaded -- not attempted-and-caught,
        which would still block on a first-run download -- and a missing model
        costs retrieval quality and nothing else. The authoritative claim and its
        evidence are still recorded.

        Returns the (model_name, dimension) to persist. Both are None when no
        vector was produced, which the schema already permits.
        """
        if not self.require_embeddings:
            logger.info("embedding_skipped", extra={"chunks": len(records)})
            return None, None
        embeddings = self.embedder.embed([r["text"] for r in records])
        if len(embeddings) != len(records):
            raise ValueError("embedding count does not match chunk count")
        for record, embedding in zip(records, embeddings):
            record["embedding"] = embedding
        return self.embedder.model_name, self.embedder.dimension

    async def _ingest_content(self, db, content: str, path: str, corpus_id: str) -> dict:
        """Shared ingestion pipeline for one parsed document.

        The caller owns the session lifetime. Returns a result dict with
        ``success``, ``message``, and (on success) ``document_id``/``chunk_count``.
        """
        from api.services import memory

        async with db.begin():
            await memory.lock_corpus(db, corpus_id)
            metadata, _ = parse_markdown(content)
            use_memory = (
                self.memory_enabled or "claims" in metadata or await memory.enabled(db, corpus_id)
            )
            if use_memory:
                result = await self._ingest_memory_content(db, content, path, corpus_id)
            else:
                result = await self._ingest_live_content(db, content, path, corpus_id)
        logging.getLogger(__name__).info(
            "document_ingested",
            extra={
                "corpus_id": corpus_id,
                "document_id": result.get("document_id"),
                "event": result.get("event"),
                "memory_enabled": use_memory,
            },
        )
        return result

    async def _ingest_live_content(self, db, content: str, path: str, corpus_id: str) -> dict:
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
                "event": "UNCHANGED",
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
        await delete_chunks_for_doc(db, doc_id, commit=False)

        sections = extract_sections_with_paths(body)
        chunks = chunk_with_heading_paths(sections)
        chunk_records = [
            {
                "text": c["text"],
                "order_index": i,
                "heading_path": c["heading_path"],
                "token_count": c["token_count"],
            }
            for i, c in enumerate(chunks)
        ]
        embedding_model, embedding_dimension = self._embed_records(chunk_records)

        doc_type = metadata.get("type") or metadata.get("document_type") or DEFAULT_DOC_TYPE
        tags = metadata.get("tags", [])

        count = await insert_chunks(
            db,
            doc_id,
            doc_type,
            tags,
            chunk_records,
            embedding_model,
            embedding_dimension,
            "1.0",
            commit=False,
        )

        await update_manifest(db, path, doc_hash, doc_id, count, corpus_id, commit=False)

        return {
            "success": True,
            "document_id": doc_id,
            "chunk_count": count,
            "message": f"Ingested {count} chunks",
        }

    async def _ingest_memory_content(self, db, content: str, path: str, corpus_id: str) -> dict:
        """One caller-owned transaction for the archive and live-index projection."""
        from api.services import memory

        await memory.lock_corpus(db, corpus_id)
        metadata, body = parse_markdown(content)
        chunks = chunk_with_heading_paths(extract_sections_with_paths(body))
        records = [{**chunk, "order_index": i} for i, chunk in enumerate(chunks)]
        # Validate before embedding or any live mutation. No inferred claims.
        memory.validate_source(path, content, metadata, records)
        previous = await memory._latest(db, corpus_id, memory.memory_document_id(corpus_id, path))
        fingerprint = memory._hash(content, metadata)
        live = (
            await db.execute(
                text("SELECT id FROM documents WHERE corpus_id = :c AND path = :p"),
                {"c": corpus_id, "p": path},
            )
        ).scalar_one_or_none()
        if (
            previous
            and previous["event"] != "DELETED"
            and previous["fingerprint"] == fingerprint
            and live
        ):
            return {
                "success": True,
                "document_id": live,
                "chunk_count": 0,
                "version_id": previous["id"],
                "event": "UNCHANGED",
                "message": "Document unchanged, skipped",
            }

        embedding_model, embedding_dimension = self._embed_records(records)
        doc_id = await upsert_document(
            db, extract_title(metadata), path, body, metadata, corpus_id=corpus_id, force=True
        )
        await delete_chunks_for_doc(db, doc_id, commit=False)
        doc_type = metadata.get("document_type") or metadata.get("type") or DEFAULT_DOC_TYPE
        if doc_type not in ("project", "kaggle", "note", "paper"):
            doc_type = DEFAULT_DOC_TYPE
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",")]
        count = await insert_chunks(
            db,
            doc_id,
            doc_type,
            tags,
            records,
            embedding_model,
            embedding_dimension,
            "1.0",
            commit=False,
        )
        version = await memory.record_version(
            db, corpus_id, path, doc_id, content, metadata, records
        )
        await update_manifest(
            db, path, content_hash(content), doc_id, count, corpus_id, commit=False
        )
        # L2 follows L1. Populate the claim representation cache here rather
        # than during a read, so a query never writes and a read path that
        # cannot write still answers correctly, just more slowly.
        await self._cache_claim_vectors(db, corpus_id, [version["id"]])
        return {
            "success": True,
            "document_id": doc_id,
            "chunk_count": count,
            "version_id": version["id"],
            "event": version["event"],
            "message": f"Ingested {count} chunks with version history",
        }

    async def _cache_claim_vectors(self, db, corpus_id: str, version_ids: list[str]) -> int:
        """Best-effort fill of the L2 claim representation cache.

        A failure here is not an ingestion failure: the claim is already
        authoritative, and the cache can always be rebuilt.

        Skipped when the caller did not require embeddings. A first memory must not
        import the model: `import sentence_transformers` measures 5.3 s on mains
        machine, and paying that on the first command a newcomer runs is worse than
        a slower ranking. `memory_query` warms this cache from the read side instead,
        from vectors it has already computed, so a corpus authored without
        embeddings is encoded once rather than on every query.
        """
        if not self.require_embeddings:
            return 0
        try:
            from api.services.claim_embeddings import pending, store

            wanted, texts = await pending(db, corpus_id, list(version_ids))
            if not wanted:
                return 0
            vectors = dict(zip(wanted, self.embedder.embed(texts)))
            return await store(
                db,
                corpus_id,
                wanted,
                vectors,
                self.embedder.model_name,
                self.embedder.dimension,
                getattr(self.embedder, "version", "ingest"),
                commit=False,
            )
        except Exception:  # noqa: BLE001 - the cache is L2, never a contract
            logger.warning("claim_embedding_cache_fill_failed", exc_info=True)
            return 0

    async def ingest_file(
        self,
        content: str,
        path: Optional[str] = None,
        corpus_id: str = DEFAULT_CORPUS_ID,
    ) -> dict:
        """Ingest a single Markdown file into the given corpus."""
        async with session_scope() as db:
            return await self._ingest_content(db, content, path or "unknown", corpus_id)

    async def ingest_repo(
        self,
        repo_path: str,
        corpus_id: str = DEFAULT_CORPUS_ID,
    ) -> dict:
        """Batch ingest all Markdown files from a repository into a corpus."""
        return await self.sync_repo(repo_path, corpus_id, delete_removed=False)

    async def sync_repo(
        self,
        repo_path: str,
        corpus_id: str = DEFAULT_CORPUS_ID,
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
        if not repo_path.is_dir():
            return {"success": False, "message": f"Path not found: {repo_path}"}

        added = changed = unchanged = deleted = failed = 0
        total_chunks = 0
        errors: list[str] = []
        md_files = sorted(repo_path.rglob("*.md"))
        source_paths: set[str] = set()

        for md_file in md_files:
            rel_path = str(md_file.relative_to(repo_path))
            source_paths.add(rel_path)
            try:
                content = md_file.read_text(encoding="utf-8")
                async with session_scope() as db:
                    known = await self._path_known(db, corpus_id, rel_path)

                state = "changed" if known else "added"
                async with session_scope() as db:
                    result = await self._ingest_content(db, content, rel_path, corpus_id)

                if not result["success"]:
                    failed += 1
                    reason = str(result.get("message") or "ingestion failed")
                    if len(errors) < MAX_SYNC_ERRORS:
                        errors.append(f"{rel_path}: {reason}")
                    logging.getLogger(__name__).warning(
                        "document_ingestion_failed",
                        extra={"corpus_id": corpus_id, "path": rel_path, "reason": reason},
                    )
                    continue

                if result.get("event") == "UNCHANGED":
                    unchanged += 1
                elif state == "added":
                    added += 1
                else:
                    changed += 1
                total_chunks += result.get("chunk_count", 0)
            except Exception as e:
                failed += 1
                reason = _sync_error(rel_path, e)
                if len(errors) < MAX_SYNC_ERRORS:
                    errors.append(reason)
                logging.getLogger(__name__).warning(
                    "document_ingestion_failed",
                    extra={
                        "corpus_id": corpus_id,
                        "path": rel_path,
                        "error_type": type(e).__name__,
                        "reason": reason,
                    },
                )

        if delete_removed:
            async with session_scope() as db:
                async with db.begin():
                    from api.services import memory

                    await memory.lock_corpus(db, corpus_id)
                    use_memory = self.memory_enabled or await memory.enabled(db, corpus_id)
                    removed = await self._delete_stale_paths(
                        db, corpus_id, source_paths, memory_enabled=use_memory
                    )
            deleted = removed

        await self._refresh_planner_statistics(corpus_id, added + changed + deleted)

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
            "errors": errors,
        }

    @staticmethod
    async def _path_known(db, corpus_id: str, path: str) -> bool:
        result = await db.execute(
            text("SELECT 1 FROM documents WHERE corpus_id = :c AND path = :p"),
            {"c": corpus_id, "p": path},
        )
        return result.first() is not None

    async def _refresh_planner_statistics(self, corpus_id: str, touched: int) -> None:
        """Collect statistics once a bulk sync has written enough to change a plan.

        PostgreSQL only ever guesses how many rows a table holds, and it guesses
        badly when it has no statistics at all. ``reltuples = -1`` means "never
        analysed", and on that basis the planner estimated about 4 rows where a
        1,000-version corpus holds 1,000. It then chose an index on ``corpus_id``
        alone and applied ``version_id`` as a filter, discarding 999 of every 1,000
        rows: 1,079,038 shared buffers where 9,190 were needed, a 117x difference.
        The returned rows were byte-identical either way, so this is a cost
        problem, not a correctness one.

        Autovacuum fixes this on its own in about a minute on a committed corpus
        (measured: it fired unaided at t+55s with stock settings), so this is not
        urgent. It matters where autovacuum cannot keep up or does not run: a bulk
        load, a restore, or a database with autovacuum disabled. Right after a sync
        the rows are committed and the estimates are certainly wrong, which is
        exactly when a developer is most likely to run the query that exposes it.

        The corpus lock is taken deliberately. ANALYZE does not deadlock against the
        advisory lock used for writes, but a concurrent ANALYZE from another session
        was observed writing ``reltuples = 0`` while 200 rows were still
        uncommitted, so the two must be serialised or the statistics collected here
        will be wrong in the other direction.

        Bounded: once per sync, not once per document. Non-fatal: a failure to
        refresh statistics must never fail an otherwise successful ingest, because
        statistics affect cost only and never what a query returns. Observable: the
        outcome is logged either way.
        """
        if touched <= STATISTICS_REFRESH_MINIMUM:
            return
        tables = (
            "memory_versions",
            "memory_documents",
            "memory_chunks",
            "memory_claims",
            "memory_evidence",
        )
        try:
            async with session_scope() as db:
                async with db.begin():
                    from api.services import memory

                    await memory.lock_corpus(db, corpus_id)
                    for table in tables:
                        await db.execute(text(f"ANALYZE {table}"))
            logging.getLogger(__name__).info(
                "planner_statistics_refreshed",
                extra={
                    "corpus_id": corpus_id,
                    "event": "PLANNER_STATISTICS_REFRESHED",
                    "touched": touched,
                    "tables": len(tables),
                },
            )
        except Exception as exc:
            # Statistics are an optimisation. Losing them costs speed, not truth.
            logging.getLogger(__name__).warning(
                "planner_statistics_refresh_failed",
                extra={
                    "corpus_id": corpus_id,
                    "event": "PLANNER_STATISTICS_REFRESH_FAILED",
                    "touched": touched,
                    "error_type": type(exc).__name__,
                    "reason": _sync_error("ANALYZE", exc),
                },
            )

    @staticmethod
    async def _delete_stale_paths(
        db, corpus_id: str, keep_paths: set[str], *, memory_enabled: bool = False
    ) -> int:
        """Delete indexed documents, chunks, and manifest entries whose source
        paths no longer exist in the source directory.

        Also purges orphaned manifest rows (manifest entry survives but the
        document is gone) — a stale manifest must never cause a file to be
        reported as 'unchanged' when it is not actually indexed.
        """
        result = await db.execute(
            text("SELECT id, path FROM documents WHERE corpus_id = :c"),
            {"c": corpus_id},
        )
        stale = [(row[0], row[1]) for row in result.fetchall() if row[1] not in keep_paths]
        for doc_id, path in stale:
            if memory_enabled:
                from api.services import memory

                await memory.record_deletion(db, corpus_id, path)
            await delete_chunks_for_doc(db, doc_id, commit=False)
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

        return len(stale)
