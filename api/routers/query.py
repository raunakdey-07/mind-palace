"""Intent-specific RAG endpoints: ask, summarize, interview, related, timeline."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.models.schemas import (
    AskRequest,
    InterviewRequest,
    RelatedRequest,
    StructuredResponse,
    SummarizeRequest,
    TimelineRequest,
)
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.llm import get_llm_provider
from api.services.retrieval import RetrievalService

router = APIRouter()
embedder = Embedder()
llm = get_llm_provider()


@router.post("/ask", response_model=StructuredResponse)
async def ask(request: AskRequest) -> StructuredResponse:
    """General Q&A over the knowledge base."""
    start = time.perf_counter()
    query_vector = embedder.embed_single(request.question)

    async for db in [get_async_db()]:
        async with db:
            retrieval = RetrievalService(db)
            results = await retrieval.search(
                query_vector,
                k=request.k,
                document_type=request.document_type,
                tags=request.tags,
            )

    if not results:
        return StructuredResponse(
            answer="No relevant content found in the knowledge base.",
            sources=[],
            snippets=[],
            latency_ms=int((time.perf_counter() - start) * 1000),
            retrieved_chunks=0,
            intent="ask",
        )

    snippets = [r.text for r in results]
    sources = list(
        {
            r.source_title or r.source_path
            for r in results
            if r.source_title or r.source_path
        }
    )
    context = "\n\n---\n\n".join(snippets)

    prompt = (
        f"Answer the following question using only the provided context. "
        f"Cite the source document for each claim. If the answer cannot be "
        f"found in the context, say so explicitly.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {request.question}\n\n"
        f"Answer:"
    )
    answer = await llm.generate(prompt)

    return StructuredResponse(
        answer=answer,
        sources=sources,
        snippets=snippets,
        latency_ms=int((time.perf_counter() - start) * 1000),
        retrieved_chunks=len(results),
        intent="ask",
    )


@router.post("/summarize", response_model=StructuredResponse)
async def summarize(request: SummarizeRequest) -> StructuredResponse:
    """Summarize a specific document."""
    start = time.perf_counter()

    async for db in [get_async_db()]:
        async with db:
            retrieval = RetrievalService(db)
            chunks = await retrieval.get_document_chunks(request.document_id)

    if not chunks:
        return StructuredResponse(
            answer="Document has no content to summarize.",
            sources=[],
            snippets=[],
            latency_ms=int((time.perf_counter() - start) * 1000),
            retrieved_chunks=0,
            intent="summarize",
        )

    # Get document title from first chunk
    doc_title = chunks[0].source_title

    # Combine all chunks (truncate if too long)
    full_text = "\n\n".join(c.text for c in chunks)
    max_chars = 8000
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "..."

    prompt = (
        f"Summarize the following document in {request.max_length} characters or less. "
        f"Focus on key findings, methods, and conclusions. Use bullet points for clarity.\n\n"
        f"Document: {doc_title}\n\n"
        f"Content:\n{full_text}\n\n"
        f"Summary:"
    )
    answer = await llm.generate(prompt)

    return StructuredResponse(
        answer=answer,
        sources=[doc_title],
        snippets=[c.text[:200] for c in chunks[:3]],
        latency_ms=int((time.perf_counter() - start) * 1000),
        retrieved_chunks=len(chunks),
        intent="summarize",
    )


@router.post("/interview", response_model=StructuredResponse)
async def interview(request: InterviewRequest) -> StructuredResponse:
    """Generate interview questions from a document."""
    start = time.perf_counter()

    async for db in [get_async_db()]:
        async with db:
            retrieval = RetrievalService(db)
            chunks = await retrieval.get_document_chunks(request.document_id)

    if not chunks:
        return StructuredResponse(
            answer="Document has no content to generate questions from.",
            sources=[],
            snippets=[],
            latency_ms=int((time.perf_counter() - start) * 1000),
            retrieved_chunks=0,
            intent="interview",
        )

    doc_title = chunks[0].source_title
    full_text = "\n\n".join(c.text for c in chunks)
    max_chars = 8000
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "..."

    difficulty_prompts = {
        "easy": "Focus on basic understanding and recall.",
        "medium": "Focus on application, analysis, and trade-offs.",
        "hard": "Focus on deep technical details, edge cases, and novel extensions.",
    }

    prompt = (
        f"Generate {request.num_questions} interview questions based on the following document. "
        f"{difficulty_prompts.get(request.difficulty, difficulty_prompts['medium'])} "
        f"Format as a numbered list with brief expected answer outlines.\n\n"
        f"Document: {doc_title}\n\n"
        f"Content:\n{full_text}\n\n"
        f"Interview Questions:"
    )
    answer = await llm.generate(prompt)

    return StructuredResponse(
        answer=answer,
        sources=[doc_title],
        snippets=[c.text[:200] for c in chunks[:3]],
        latency_ms=int((time.perf_counter() - start) * 1000),
        retrieved_chunks=len(chunks),
        intent="interview",
    )


@router.post("/related", response_model=StructuredResponse)
async def related(request: RelatedRequest) -> StructuredResponse:
    """Find documents related to a given document."""
    start = time.perf_counter()

    async for db in [get_async_db()]:
        async with db:
            retrieval = RetrievalService(db)
            related_docs = await retrieval.get_related_documents(
                request.document_id, k=request.k
            )

    if not related_docs:
        return StructuredResponse(
            answer="No related documents found.",
            sources=[],
            snippets=[],
            latency_ms=int((time.perf_counter() - start) * 1000),
            retrieved_chunks=0,
            intent="related",
        )

    # Get the source document title
    async for db in [get_async_db()]:
        async with db:
            from sqlalchemy import text

            result = await db.execute(
                text("SELECT title FROM documents WHERE id = :id"),
                {"id": request.document_id},
            )
            row = result.first()
            doc_title = row[0] if row else "Unknown"

    lines = [f"Documents related to **{doc_title}**:\n"]
    for i, rd in enumerate(related_docs, 1):
        tags_str = ", ".join(rd["tags"]) if rd["tags"] else "no tags"
        lines.append(
            f"{i}. **{rd['title']}** ({rd['document_type']}) — Tags: {tags_str} — Shared tags: {rd['shared_tags']}"
        )

    return StructuredResponse(
        answer="\n".join(lines),
        sources=[doc_title] + [rd["title"] for rd in related_docs],
        snippets=[],
        latency_ms=int((time.perf_counter() - start) * 1000),
        retrieved_chunks=len(related_docs),
        intent="related",
    )


@router.get("/timeline", response_model=StructuredResponse)
async def timeline(
    document_type: Optional[str] = Query(None, description="Filter by document type"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=200),
) -> StructuredResponse:
    """Chronological view of documents."""
    start = time.perf_counter()

    async for db in [get_async_db()]:
        async with db:
            retrieval = RetrievalService(db)
            docs = await retrieval.get_timeline(
                document_type, start_date, end_date, limit
            )

    if not docs:
        return StructuredResponse(
            answer="No documents found matching the criteria.",
            sources=[],
            snippets=[],
            latency_ms=int((time.perf_counter() - start) * 1000),
            retrieved_chunks=0,
            intent="timeline",
        )

    lines = ["**Document Timeline** (newest first):\n"]
    for i, d in enumerate(docs, 1):
        date_str = d["date"].isoformat() if d["date"] else "no date"
        tags_str = ", ".join(d["tags"]) if d["tags"] else "no tags"
        lines.append(
            f"{i}. **{d['title']}** ({d['document_type']}) — {date_str} — Tags: {tags_str}"
        )

    return StructuredResponse(
        answer="\n".join(lines),
        sources=[d["title"] for d in docs],
        snippets=[],
        latency_ms=int((time.perf_counter() - start) * 1000),
        retrieved_chunks=len(docs),
        intent="timeline",
    )


# Keep legacy endpoint for backward compatibility
@router.post("", response_model=StructuredResponse)
async def legacy_query(request: AskRequest) -> StructuredResponse:
    """Legacy /api/query endpoint - delegates to /ask."""
    return await ask(request)
