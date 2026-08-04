"""RAG query endpoint: retrieve context + call LLM for answer generation."""

from __future__ import annotations

from api.models.schemas import InsightQuery, InsightResponse
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.repository import search_chunks
from fastapi import APIRouter, HTTPException

router = APIRouter()
embedder = Embedder()


@router.post("", response_model=InsightResponse)
async def query(request: InsightQuery) -> InsightResponse:
    """RAG Q&A: retrieve top-k chunks, call Ollama LLM, return cited answer."""
    query_vector = embedder.embed_single(request.question)

    async for db in [get_async_db()]:
        async with db:
            results = await search_chunks(db, query_vector, k=request.k)

    if not results:
        return InsightResponse(
            answer="No relevant content found in the knowledge base.",
            sources=[],
            snippets=[],
        )

    # Build context from retrieved snippets
    snippets = [r["text"] for r in results]
    sources = list(
        {
            r.get("source_title") or r.get("source_path", "")
            for r in results
            if r.get("source_title") or r.get("source_path")
        }
    )
    context = "\n\n---\n\n".join(snippets)

    # Call Ollama for generation
    try:
        import httpx

        ollama_url = "http://localhost:11434/api/generate"
        prompt = (
            f"Answer the following question using only the provided context. "
            f"Cite the source document for each claim. If the answer cannot be "
            f"found in the context, say so explicitly.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {request.question}\n\n"
            f"Answer:"
        )

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                ollama_url,
                json={
                    "model": "llama3.3:3b-instruct",
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()
            answer = response.json().get("response", "No answer generated.")
    except Exception as e:
        # Fallback: return snippets as a non-LLM answer
        answer = f"(LLM unavailable: {e})\n\nRetrieved snippets:\n" + "\n".join(
            f"- {s}" for s in snippets[:3]
        )

    return InsightResponse(answer=answer, sources=sources, snippets=snippets)
