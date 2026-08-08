# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

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
    heading_path: Optional[str] = None
    document_type: Optional[str] = None


class SearchResponse(BaseModel):
    """Response from GET /api/search."""

    query: str
    results: List[SearchResult]
    total: int


# --- RAG Query (Intent-Specific) ---


class AskRequest(BaseModel):
    """Request body for POST /api/ask - general Q&A."""

    question: str = Field(..., description="The user's question")
    k: int = Field(5, ge=1, le=20, description="Number of retrieved chunks")
    document_type: Optional[str] = Field(None, description="Filter by document type")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")


class SummarizeRequest(BaseModel):
    """Request body for POST /api/summarize - document summarization."""

    document_id: str = Field(..., description="Document ID to summarize")
    max_length: int = Field(500, ge=100, le=2000, description="Max summary length")


class InterviewRequest(BaseModel):
    """Request body for POST /api/interview - interview question generation."""

    document_id: str = Field(..., description="Document ID to generate questions from")
    num_questions: int = Field(5, ge=1, le=15, description="Number of questions")
    difficulty: str = Field("medium", pattern=r"^(easy|medium|hard)$")


class RelatedRequest(BaseModel):
    """Request body for POST /api/related - find related documents."""

    document_id: str = Field(..., description="Document ID to find related docs for")
    k: int = Field(5, ge=1, le=20, description="Number of related documents")


class TimelineRequest(BaseModel):
    """Request body for GET /api/timeline - chronological document view."""

    document_type: Optional[str] = Field(None, description="Filter by document type")
    start_date: Optional[str] = Field(None, description="Start date (YYYY-MM-DD)")
    end_date: Optional[str] = Field(None, description="End date (YYYY-MM-DD)")
    limit: int = Field(50, ge=1, le=200)


class Source(BaseModel):
    """Source document reference."""

    title: str
    path: Optional[str] = None
    document_type: Optional[str] = None


class RetrievalDiagnostics(BaseModel):
    """Debug information about retrieval scores."""

    document_id: str
    vector_score: Optional[float] = None
    keyword_score: Optional[float] = None
    rrf_score: Optional[float] = None


class StructuredResponse(BaseModel):
    """Structured response with metadata for all intent endpoints."""

    answer: str = Field(
        ...,
        description="Generated answer or result",
        example="Feature leakage occurred because the test set was used during feature selection.",
    )
    sources: List[str] = Field(
        default_factory=list, description="Source document IDs or URLs"
    )
    snippets: List[str] = Field(
        default_factory=list, description="Retrieved context snippets"
    )
    latency_ms: int = Field(
        0, description="Total response latency in milliseconds", example=142
    )
    retrieved_chunks: int = Field(
        0, description="Number of chunks retrieved", example=6
    )
    intent: str = Field(
        ...,
        description="Intent type: ask, summarize, interview, related, timeline",
        example="ask",
    )
    provider: Optional[str] = Field(
        None, description="LLM provider used", example="ollama"
    )
    model: Optional[str] = Field(
        None, description="Model name used", example="llama3.3:3b-instruct"
    )
    temperature: Optional[float] = Field(
        None, description="Temperature used for generation", example=0.2
    )
    diagnostics: Optional[List[RetrievalDiagnostics]] = Field(
        None, description="Retrieval score breakdown (debug mode)"
    )


# --- Legacy (for backward compatibility) ---


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
