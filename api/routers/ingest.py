"""Ingestion endpoints: file and repo batch ingestion."""

from __future__ import annotations

from typing import List

from api.models.schemas import IngestFileRequest, IngestRepoRequest, IngestResponse
from api.services.chunker import chunk_document
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.parser import parse_markdown
from api.services.repository import (
    delete_chunks_for_doc,
    insert_chunks,
    upsert_document,
)
from fastapi import APIRouter, HTTPException

router = APIRouter()
embedder = Embedder()


@router.post("/file", response_model=IngestResponse)
async def ingest_file(request: IngestFileRequest) -> IngestResponse:
    """Ingest a single Markdown file (body with frontmatter)."""
    try:
        metadata, body = parse_markdown(request.content)
        title = metadata.get("title", request.path or "Untitled")
        path = request.path or "unknown"

        async for db in [get_async_db()]:
            async with db:
                doc_id = await upsert_document(db, title, path, body, metadata)
                if not doc_id:
                    return IngestResponse(
                        success=True,
                        chunk_count=0,
                        message="Document unchanged, skipped",
                    )

                # Delete old chunks for this doc
                await delete_chunks_for_doc(db, doc_id)

                # Chunk and embed
                chunks_text = chunk_document(body)
                embeddings = embedder.embed(chunks_text)

                chunk_records = [
                    {
                        "text": ct,
                        "order_index": i,
                        "section": metadata.get("title", ""),
                        "embedding": emb,
                    }
                    for i, (ct, emb) in enumerate(zip(chunks_text, embeddings))
                ]
                count = await insert_chunks(db, doc_id, chunk_records)

                return IngestResponse(
                    success=True,
                    document_id=doc_id,
                    chunk_count=count,
                    message=f"Ingested {count} chunks",
                )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/repo", response_model=IngestResponse)
async def ingest_repo(request: IngestRepoRequest) -> IngestResponse:
    """Batch ingest all Markdown files from a local repo path."""
    import os
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
            metadata, body = parse_markdown(content)
            title = metadata.get("title", md_file.stem)

            async for db in [get_async_db()]:
                async with db:
                    doc_id = await upsert_document(db, title, rel_path, body, metadata)
                    if not doc_id:
                        continue
                    await delete_chunks_for_doc(db, doc_id)
                    chunks_text = chunk_document(body)
                    embeddings = embedder.embed(chunks_text)
                    chunk_records = [
                        {
                            "text": ct,
                            "order_index": i,
                            "section": title,
                            "embedding": emb,
                        }
                        for i, (ct, emb) in enumerate(zip(chunks_text, embeddings))
                    ]
                    total_chunks += await insert_chunks(db, doc_id, chunk_records)
        except Exception as e:
            # Log and continue with next file
            print(f"[ingest] Error processing {md_file}: {e}")

    return IngestResponse(
        success=True,
        chunk_count=total_chunks,
        message=f"Ingested {len(md_files)} files, {total_chunks} chunks total",
    )
