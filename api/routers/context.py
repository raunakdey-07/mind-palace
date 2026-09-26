# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Context endpoint: the product surface for feeding an AI system.

``context()`` answers "what should this application know for this question?" and
returns the smallest useful, evidence-backed representation. The archive decides
what is true, what changed, what conflicts, and what is absent. Retrieval adds
raw source material and is skipped when no model is available.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.schemas import ContextPackResponse
from api.services.context_packer import ContextPack
from api.services.context_service import ContextError, build_context
from api.services.corpora import (
    CorpusScopeNotFound,
    CorpusScopeRequired,
    resolve_corpus_scope,
)
from api.services.db import get_async_db
from api.services.observability import OperationTrace

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_async_db)]


def _as_response(pack: ContextPack) -> ContextPackResponse:
    return ContextPackResponse(**pack.to_dict())


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
    as_of: Optional[str] = Query(
        None, description="ISO-8601 instant; resolves what was known then"
    ),
    intent: Optional[str] = Query(
        None,
        description=(
            "current, historical, temporal, change, conflict or provenance. "
            "Inferred from the question when omitted."
        ),
    ),
    db: DbSession = None,
) -> ContextPackResponse:
    """Assemble bounded, evidence-backed context for ``q``.

    ``status`` reports what happened: ``resolved``, ``conflicting``,
    ``no_relevant_memory``, or ``empty_corpus``. When the archive holds no
    relevant memory the pack is empty and says so, rather than returning the
    nearest unrelated chunks.
    """
    try:
        corpus_id, selected_corpus = await resolve_corpus_scope(db, corpus)
    except CorpusScopeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CorpusScopeRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationalError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    if corpus_id is None:
        return ContextPackResponse(query=q, context="", strategy=strategy, status="empty_corpus")

    if intent is not None and intent not in {
        "auto",
        "current",
        "historical",
        "temporal",
        "change",
        "conflict",
        "provenance",
    }:
        raise HTTPException(status_code=422, detail=f"invalid intent '{intent}'")

    from datetime import datetime

    cutoff = None
    if as_of:
        try:
            cutoff = datetime.fromisoformat(as_of)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid as_of timestamp") from exc
        if cutoff.tzinfo is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")

    try:
        pack, trace = await build_context(
            db,
            selected_corpus,
            q,
            budget_tokens=budget_tokens,
            k=k,
            strategy=strategy,
            as_of=cutoff,
            intent=intent,
        )
    except ContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except OperationalError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    trace_out = OperationTrace(
        operation="context",
        corpus=selected_corpus,
        strategy=strategy,
        candidate_count=trace.get("candidate_count", 0),
        returned_count=trace.get("returned_count", 0),
    )
    trace_out.mark_pack(pack.token_estimate, pack.truncated)
    trace_out.emit()
    return _as_response(pack)
