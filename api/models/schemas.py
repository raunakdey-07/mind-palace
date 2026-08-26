# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Pydantic models for Mind Palace API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Ingestion ---


class IngestFileRequest(BaseModel):
    """Request body for POST /api/ingest/file."""

    content: str = Field(..., description="Raw Markdown content (frontmatter + body)")
    path: str | None = Field(None, description="File path for reference")


class IngestRepoRequest(BaseModel):
    """Request body for POST /api/ingest/repo."""

    repo_path: str = Field(..., description="Local path to the content repository")


class IngestResponse(BaseModel):
    """Response from ingestion endpoints."""

    success: bool
    document_id: str | None = None
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
    source_title: str | None = None
    source_id: str | None = None
    score: float = 0.0
    heading_path: str | None = None
    document_type: str | None = None


class SearchResponse(BaseModel):
    """Response from GET /api/search."""

    query: str
    results: list[SearchResult]
    total: int


# --- Corpora ---


class CorpusCreate(BaseModel):
    """Request body for POST /api/corpora."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Corpus name (letters, digits, '-', '_', '.')",
    )
    description: str = Field("", description="Human-readable description")


class CorpusResponse(BaseModel):
    """A corpus namespace."""

    id: str
    name: str
    description: str = ""
    document_count: int = 0


class CorpusStatsResponse(BaseModel):
    """Detailed corpus statistics."""

    id: str
    name: str
    description: str
    document_count: int
    chunk_count: int = 0
    last_ingested: str | None = None


class SyncRequest(BaseModel):
    """Request body for POST /api/corpora/{name}/sync."""

    path: str = Field(..., description="Source directory to synchronize from")


class SyncResponse(BaseModel):
    """Machine-readable sync summary."""

    success: bool
    corpus_id: str | None = None
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    chunk_count: int = 0
    duration_ms: int = 0
    message: str = ""


# --- Context packing ---


class ContextSourceInfo(BaseModel):
    """Provenance for one source document in a context pack."""

    title: str
    path: str = ""
    doc_id: str
    heading_path: str | None = None
    document_type: str | None = None


class ContextChunkInfo(BaseModel):
    """One evidence chunk in a context pack."""

    text: str
    score: float
    rank: int
    source: ContextSourceInfo


class ContextPackResponse(BaseModel):
    """Model-ready context with full attribution.

    The ``context`` field is ready to be inserted into an LLM prompt.
    ``sources`` and ``chunks`` carry provenance; ``truncated`` reports
    whether evidence was dropped to satisfy the budget.
    """

    query: str
    context: str
    sources: list[ContextSourceInfo] = Field(default_factory=list)
    chunks: list[ContextChunkInfo] = Field(default_factory=list)
    token_estimate: int = 0
    strategy: str = "hybrid_rrf"
    truncated: bool = False


# --- RAG Query (Intent-Specific) ---


class AskRequest(BaseModel):
    """Request body for POST /api/ask - general Q&A."""

    question: str = Field(..., description="The user's question")
    k: int = Field(5, ge=1, le=20, description="Number of retrieved chunks")
    document_type: str | None = Field(None, description="Filter by document type")
    tags: list[str] | None = Field(None, description="Filter by tags")


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

    document_type: str | None = Field(None, description="Filter by document type")
    start_date: str | None = Field(None, description="Start date (YYYY-MM-DD)")
    end_date: str | None = Field(None, description="End date (YYYY-MM-DD)")
    limit: int = Field(50, ge=1, le=200)


class Source(BaseModel):
    """Source document reference."""

    title: str
    path: str | None = None
    document_type: str | None = None


class RetrievalDiagnostics(BaseModel):
    """Debug information about retrieval scores."""

    document_id: str
    vector_score: float | None = None
    keyword_score: float | None = None
    rrf_score: float | None = None


class StructuredResponse(BaseModel):
    """Structured response with metadata for all intent endpoints."""

    answer: str = Field(
        ...,
        description="Generated answer or result",
        json_schema_extra={
            "example": "Feature leakage because the test set was used during feature selection."
        },
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Source document IDs or URLs",
    )
    snippets: list[str] = Field(default_factory=list, description="Retrieved context snippets")
    latency_ms: int = Field(
        0,
        description="Total response latency in milliseconds",
        json_schema_extra={"example": "142"},
    )
    retrieved_chunks: int = Field(
        0, description="Number of chunks retrieved", json_schema_extra={"example": "6"}
    )
    intent: str = Field(
        ...,
        description="Intent type: ask, summarize, interview, related, timeline",
        json_schema_extra={"example": "ask"},
    )
    provider: str | None = Field(
        None, description="LLM provider used", json_schema_extra={"example": "ollama"}
    )
    model: str | None = Field(
        None,
        description="Model name used",
        json_schema_extra={"example": "llama3.3:3b-instruct"},
    )
    temperature: float | None = Field(
        None,
        description="Temperature used for generation",
        json_schema_extra={"example": 0.2},
    )
    diagnostics: list[RetrievalDiagnostics] | None = Field(
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
    sources: list[str] = Field(default_factory=list, description="Source document IDs or URLs")
    snippets: list[str] = Field(default_factory=list, description="Retrieved context snippets")


# --- Agent ---


class AgentRequest(BaseModel):
    """Request body for agent endpoints."""

    topic: str = Field(..., description="The topic or question for the agent")
    k: int = Field(5, ge=1, le=20)


class AgentResponse(BaseModel):
    """Response from agent endpoints."""

    agent: str
    result: str
    sources: list[str] = Field(default_factory=list)
