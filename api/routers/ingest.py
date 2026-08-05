"""Ingestion endpoints: file and repo batch ingestion."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from api.models.schemas import IngestFileRequest, IngestRepoRequest, IngestResponse
from api.services.chunker import chunk_document
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.parser import (
    FrontmatterSchema,
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

router = APIRouter()
embedder = Embedder()


@router.post("/file", response_model=IngestResponse)
async def ingest_file(request: IngestFileRequest) -> IngestResponse:
    """Ingest a single Markdown file (body with frontmatter)."""
    try:
        frontmatter_obj, body, sections = parse_markdown(request.content)
        path = request.path or "unknown"

        async for db in [get_async_db()]:
            async with db:
                # Check manifest for incremental ingestion
                body_hash = embedder.embed_single(
                    body
                )  # Reuse embedder for hash? No, use content_hash
                from api.services.repository import content_hash

                doc_hash = content_hash(body)

                if await check_manifest(db, path, doc_hash):
                    return IngestResponse(
                        success=True,
                        chunk_count=0,
                        message="Document unchanged (manifest), skipped",
                    )

                doc_id = await upsert_document(
                    db, frontmatter_obj.title, path, body, frontmatter_obj.model_dump()
                )
                if not doc_id:
                    return IngestResponse(
                        success=True,
                        chunk_count=0,
                        message="Document unchanged, skipped",
                    )

                # Delete old chunks for this doc
                await delete_chunks_for_doc(db, doc_id)

                # Chunk with heading paths and embed
                chunks = chunk_with_heading_paths(sections)
                chunk_texts = [c["text"] for c in chunks]
                embeddings = embedder.embed(chunk_texts)

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
                    embedder._model.__class__.__name__,
                    embedder.dimension,
                    "1.0",
                )

                await update_manifest(db, path, doc_hash, doc_id, count)

                return IngestResponse(
                    success=True,
                    document_id=doc_id,
                    chunk_count=count,
                    message=f"Ingested {count} chunks",
                )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/repo", response_model=IngestResponse)
async def ingest_repo(request: IngestRepoRequest) -> IngestResponse:
    """Batch ingest all Markdown files from a local repo path."""
    from pathlib import Path

    repo_path = Path(request.repo_path)
    if not repo_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Path not found: {request.repo_path}"
        )

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
                    embeddings = embedder.embed(chunk_texts)

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
                        embedder._model.__class__.__name__,
                        embedder.dimension,
                        "1.0",
                    )

                    await update_manifest(db, rel_path, doc_hash, doc_id, count)
                    total_chunks += count
        except ValueError as e:
            print(f"[ingest] Validation error for {md_file}: {e}")
        except Exception as e:
            print(f"[ingest] Error processing {md_file}: {e}")

    return IngestResponse(
        success=True,
        chunk_count=total_chunks,
        message=f"Ingested {len(md_files)} files, {total_chunks} chunks total",
    )
