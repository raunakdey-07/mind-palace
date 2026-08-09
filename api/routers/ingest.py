# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""API router for ingestion endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models.schemas import IngestFileRequest, IngestRepoRequest, IngestResponse
from api.services.ingestion import IngestionService

router = APIRouter()
ingestion_service = IngestionService()


@router.post("/file", response_model=IngestResponse)
async def ingest_file(request: IngestFileRequest) -> IngestResponse:
    """Ingest a single Markdown file."""
    result = await ingestion_service.ingest_file(request.content, request.path)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("message", "Unknown error"))
    return IngestResponse(**result)


@router.post("/repo", response_model=IngestResponse)
async def ingest_repo(request: IngestRepoRequest) -> IngestResponse:
    """Batch ingest all Markdown files from a repository."""
    result = await ingestion_service.ingest_repo(request.repo_path)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("message", "Unknown error"))
    return IngestResponse(**result)
