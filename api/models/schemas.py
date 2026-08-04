"""Pydantic models for Mind Palace API request/response schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# --- Ingestion ---


class IngestFileRequest(BaseModel):
    """Request body for POST /api/ingest/file."""

    content: str = Field(..., description="Raw Markdown content (frontmatter + body)")
    path: Optional[str] = Field(None, description="File path for reference")


class IngestRepoRequest(BaseModel):
    """Request body for POST /api/ingest/repo."""

    repo_path: str = Field(..., description="Local path to the content repository")


class IngestResponse(BaseModel):
    """Response from ingestion endpoints."""

    success: bool
    document_id: Optional[str] = None
    chunk_count: int = 0
    message: str = ""


# --- Search ---


class SearchRequest(BaseModel):
    """Query parameters for GET /api/search."""

    q: str = Field(..., description="Search query text")
    k: int = Field(5, ge=1, le=50, description="Number of results to return")


class SearchResult(BaseModel):
    """A single search result chunk."""

    text: str
    source_title: Optional[str] = None
    source_id: Optional[str] = None
    score: float = 0.0


class SearchResponse(BaseModel):
    """Response from GET /api/search."""

    query: str
    results: List[SearchResult]
    total: int


# --- RAG Query ---


class InsightQuery(BaseModel):
    """Request body for POST /api/query (matches spec)."""

    question: str = Field(..., description="The user's question")
    k: int = Field(5, ge=1, le=20, description="Number of retrieved chunks")


class InsightResponse(BaseModel):
    """Response from POST /api/query (matches spec)."""

    answer: str = Field(..., description="Generated answer from the LLM")
    sources: List[str] = Field(
        default_factory=list, description="Source document IDs or URLs"
    )
    snippets: List[str] = Field(
        default_factory=list, description="Retrieved context snippets"
    )


# --- Agent ---


class AgentRequest(BaseModel):
    """Request body for agent endpoints."""

    topic: str = Field(..., description="The topic or question for the agent")
    k: int = Field(5, ge=1, le=20)


class AgentResponse(BaseModel):
    """Response from agent endpoints."""

    agent: str
    result: str
    sources: List[str] = Field(default_factory=list)
