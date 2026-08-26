# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Context endpoint: model-ready context packs with attribution.

This is the primary product surface: an AI application asks a question and
receives bounded, attributable context ready for prompt insertion.
"""

from __future__ import annotations

import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.schemas import ContextPackResponse
from api.services.context_packer import pack_context
from api.services.corpora import get_corpus_by_name
from api.services.db import get_async_db
from api.services.embedder import Embedder
from api.services.observability import OperationTrace
from api.services.retrieval import RetrievalService

router = APIRouter()

embedder = Embedder()

DbSession = Annotated[AsyncSession, Depends(get_async_db)]

VALID_STRATEGIES = {"vector", "hybrid", "hybrid_rrf"}


@router.get("", response_model=ContextPackResponse)
async def get_context(
    q: str = Query(..., description="Query/task to retrieve evidence for"),
    corpus: str | None = Query(
        None, description="Corpus name (required when multiple corpora exist)"
    ),
    k: int = Query(8, ge=1, le=50, description="Maximum evidence chunks to consider"),
    budget_tokens: int = Query(
        4096, ge=256, le=32768, description="Maximum tokens for the packed context"
    ),
    strategy: str = Query("hybrid_rrf", description="Retrieval strategy"),
    db: DbSession = None,
) -> ContextPackResponse:
    """Retrieve evidence and pack it into model-ready context.

    The response ``context`` field is bounded by ``budget_tokens`` and carries
    full source attribution in ``sources``/``chunks``. ``truncated`` reports
    whether evidence was dropped to fit the budget.
    """
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(
            status_code=422,
            detail=f"invalid strategy '{strategy}'; expected one of {sorted(VALID_STRATEGIES)}",
        )

    corpus_id: Optional[str] = None
    if corpus:
        try:
            c = await get_corpus_by_name(db, corpus)
        except OperationalError as e:
            raise HTTPException(status_code=503, detail="Database unavailable") from e
        if not c:
            raise HTTPException(status_code=404, detail=f"corpus '{corpus}' not found")
        corpus_id = c["id"]

    trace = OperationTrace(operation="context", corpus=corpus, strategy=strategy)
    t0 = time.perf_counter()
    query_vector = embedder.embed_single(q)
    trace.embedding_ms = (time.perf_counter() - t0) * 1000

    try:
        retrieval = RetrievalService(db)
        results = await retrieval.search(
            query_vector,
            k=k,
            hybrid=(strategy != "vector"),
            rrf=(strategy == "hybrid_rrf"),
            query_text=q if strategy != "vector" else None,
            corpus_id=corpus_id,
        )
        trace.retrieval_ms = (time.perf_counter() - t0) * 1000 - trace.embedding_ms
        trace.candidate_count = len(results)
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="Database unavailable") from e

    pack = pack_context(q, results, budget_tokens=budget_tokens, strategy=strategy)
    trace.mark_pack(pack.token_estimate, pack.truncated)
    trace.returned_count = len(pack.chunks)
    trace.emit()

    return ContextPackResponse(**pack.to_dict())
