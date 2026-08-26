# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Retrieval service abstraction for hybrid search and reranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _to_pgvector(vector: List[float]) -> str:
    """Serialize a float vector to pgvector's text literal format."""
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


@dataclass
class RetrievalResult:
    """A single retrieval result with all metadata."""

    text: str
    heading_path: Optional[str]
    document_type: Optional[str]
    source_url: Optional[str]
    tags: List[str]
    source_title: str
    source_path: str
    source_document_type: str
    score: float
    doc_id: str
    vector_score: Optional[float] = None
    keyword_score: Optional[float] = None
    rrf_score: Optional[float] = None
    rerank_score: Optional[float] = None


class RetrievalService:
    """Abstraction layer for retrieval operations.

    Supports:
    - Pure vector search (cosine similarity)
    - Hybrid search (vector + keyword/pg_trgm)
    - Reciprocal Rank Fusion (RRF)
    - Metadata filtering
    - Cross-encoder reranking
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def search(
        self,
        query_vector: List[float],
        k: int = 5,
        document_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        hybrid: bool = False,
        query_text: Optional[str] = None,
        rrf: bool = True,
        rerank: bool = False,
        candidate_k: int = 20,
        debug: bool = False,
        corpus_id: Optional[str] = None,
    ) -> List[RetrievalResult]:
        """Search with optional hybrid (vector + keyword) mode, RRF, and reranking.

        When ``rerank`` is enabled, ``candidate_k`` candidates are retrieved first
        and then scored by a cross-encoder; the top ``k`` are returned.
        Results are scoped to ``corpus_id`` when provided — searches never leak
        across corpora.
        """
        fetch_k = max(k, candidate_k) if (rerank and query_text) else k

        where_clauses = ["c.embedding IS NOT NULL"]
        params: dict = {"query": _to_pgvector(query_vector), "k": fetch_k * 3 if rrf else fetch_k}

        if corpus_id:
            where_clauses.append("d.corpus_id = :corpus")
            params["corpus"] = corpus_id

        if document_type:
            where_clauses.append("c.document_type = :doc_type")
            params["doc_type"] = document_type

        if tags:
            where_clauses.append("c.tags && :tags")
            params["tags"] = tags

        where_sql = " AND ".join(where_clauses)

        if hybrid and query_text:
            # Hybrid: combine vector similarity with pg_trgm keyword search
            # Requires pg_trgm extension: CREATE EXTENSION pg_trgm;
            params["query_text"] = query_text
            if rrf:
                # RRF: fetch separate rankings and fuse
                sql = f"""
                    WITH vector_rank AS (
                        SELECT
                            c.id as chunk_id,
                            c.text,
                            c.heading_path,
                            c.document_type,
                            c.source_url,
                            c.tags,
                               d.title, d.path, d.document_type as doc_type, d.id as doc_id,
                               1 - (c.embedding <=> CAST(:query AS vector)) AS vector_score,
                               ROW_NUMBER() OVER (
                                   ORDER BY c.embedding <=> CAST(:query AS vector)
                               ) as vector_rank
                        FROM chunks c
                        JOIN documents d ON c.doc_id = d.id
                        WHERE {where_sql}
                    ),
                    keyword_rank AS (
                        SELECT
                            c.id as chunk_id,
                            similarity(c.text, :query_text) AS keyword_score,
                               ROW_NUMBER() OVER (
                                   ORDER BY similarity(c.text, :query_text) DESC
                               ) as keyword_rank
                        FROM chunks c
                        JOIN documents d ON c.doc_id = d.id
                        WHERE {where_sql}
                    )
                    SELECT
                           vr.chunk_id, vr.text, vr.heading_path, vr.document_type, vr.source_url,
                           vr.tags, vr.title, vr.path, vr.doc_type, vr.doc_id,
                           vr.vector_score, kr.keyword_score,
                           vr.vector_rank, kr.keyword_rank,
                           (
                               1.0 / (60 + vr.vector_rank)
                               + 1.0 / (60 + kr.keyword_rank)
                           ) as rrf_score
                    FROM vector_rank vr
                    JOIN keyword_rank kr ON vr.chunk_id = kr.chunk_id
                    ORDER BY rrf_score DESC
                    LIMIT :k
                """
            else:
                # Simple weighted average (legacy)
                sql = f"""
                    SELECT c.text, c.heading_path, c.document_type, c.source_url, c.tags,
                           d.title, d.path, d.document_type as doc_type, d.id as doc_id,
                           1 - (c.embedding <=> CAST(:query AS vector)) AS vector_score,
                           similarity(c.text, :query_text) AS keyword_score,
                           (
                               0.7 * (1 - (c.embedding <=> CAST(:query AS vector)))
                               + 0.3 * similarity(c.text, :query_text)
                           ) AS combined_score
                    FROM chunks c
                    JOIN documents d ON c.doc_id = d.id
                    WHERE {where_sql}
                    ORDER BY combined_score DESC
                    LIMIT :k
                """
        else:
            # Pure vector search
            sql = f"""
                SELECT c.text, c.heading_path, c.document_type, c.source_url, c.tags,
                       d.title, d.path, d.document_type as doc_type, d.id as doc_id,
                       1 - (c.embedding <=> CAST(:query AS vector)) AS score
                FROM chunks c
                JOIN documents d ON c.doc_id = d.id
                WHERE {where_sql}
                ORDER BY c.embedding <=> CAST(:query AS vector)
                LIMIT :k
            """

        result = await self.db.execute(text(sql), params)
        rows = result.fetchall()
        results = [
            self._row_to_result(row, hybrid=hybrid and bool(query_text), rrf=rrf) for row in rows
        ]

        # Apply cross-encoder reranking over the candidate set when enabled.
        if rerank and query_text and results:
            from .reranker import Reranker

            texts = [r.text for r in results]
            scores = Reranker().score(query_text, texts)

            for result_item, rerank_score in zip(results, scores):
                result_item.rerank_score = rerank_score
                result_item.score = rerank_score

            results.sort(key=lambda x: x.score if x.score is not None else 0.0, reverse=True)

        return results[:k]

    @staticmethod
    def _row_to_result(row: tuple, hybrid: bool, rrf: bool) -> RetrievalResult:
        """Map a database row to a RetrievalResult based on the query shape."""
        if hybrid and rrf:
            return RetrievalResult(
                text=row[1],
                heading_path=row[2],
                document_type=row[3],
                source_url=row[4],
                tags=row[5] or [],
                source_title=row[6],
                source_path=row[7],
                source_document_type=row[8],
                score=float(row[14]),
                doc_id=row[9],
                vector_score=float(row[10]) if row[10] is not None else None,
                keyword_score=float(row[11]) if row[11] is not None else None,
                rrf_score=float(row[14]) if row[14] is not None else None,
            )
        if hybrid:
            return RetrievalResult(
                text=row[0],
                heading_path=row[1],
                document_type=row[2],
                source_url=row[3],
                tags=row[4] or [],
                source_title=row[5],
                source_path=row[6],
                source_document_type=row[7],
                score=float(row[10]),
                doc_id=row[8],
                vector_score=float(row[9]) if row[9] is not None else None,
                keyword_score=float(row[10]) if row[10] is not None else None,
            )
        return RetrievalResult(
            text=row[0],
            heading_path=row[1],
            document_type=row[2],
            source_url=row[3],
            tags=row[4] or [],
            source_title=row[5],
            source_path=row[6],
            source_document_type=row[7],
            score=float(row[9]),
            doc_id=row[8],
        )

    async def get_document_chunks(self, doc_id: str) -> List[RetrievalResult]:
        """Get all chunks for a document (for summarization)."""
        result = await self.db.execute(
            text("""
                SELECT c.text, c.heading_path, c.document_type, c.source_url, c.tags,
                       d.title, d.path, d.document_type as doc_type, d.id as doc_id,
                       1.0 as score
                FROM chunks c
                JOIN documents d ON c.doc_id = d.id
                WHERE c.doc_id = :doc_id
                ORDER BY c.order_index
            """),
            {"doc_id": doc_id},
        )
        rows = result.fetchall()
        return [
            RetrievalResult(
                text=row[0],
                heading_path=row[1],
                document_type=row[2],
                source_url=row[3],
                tags=row[4] or [],
                source_title=row[5],
                source_path=row[6],
                source_document_type=row[7],
                score=float(row[9]),
                doc_id=row[8],
            )
            for row in rows
        ]

    async def get_related_documents(self, doc_id: str, k: int = 5) -> List[dict]:
        """Find related documents via shared tags and document type."""
        result = await self.db.execute(
            text("""
                SELECT d.id, d.title, d.path, d.document_type, d.tags,
                       COUNT(*) as shared_tags
                FROM documents d
                JOIN chunks c ON c.doc_id = d.id
                WHERE d.id != :doc_id
                  AND d.tags && (SELECT tags FROM documents WHERE id = :doc_id)
                GROUP BY d.id, d.title, d.path, d.document_type, d.tags
                ORDER BY shared_tags DESC, d.updated_at DESC
                LIMIT :k
            """),
            {"doc_id": doc_id, "k": k},
        )
        return [
            {
                "id": row[0],
                "title": row[1],
                "path": row[2],
                "document_type": row[3],
                "tags": row[4],
                "shared_tags": row[5],
            }
            for row in result.fetchall()
        ]

    async def get_timeline(
        self,
        document_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Get chronological document view."""
        where_clauses = ["1=1"]
        params: dict = {"limit": limit}

        if document_type:
            where_clauses.append("document_type = :doc_type")
            params["doc_type"] = document_type
        if start_date:
            where_clauses.append("date >= :start_date")
            params["start_date"] = start_date
        if end_date:
            where_clauses.append("date <= :end_date")
            params["end_date"] = end_date

        where_sql = " AND ".join(where_clauses)

        result = await self.db.execute(
            text(f"""
                SELECT id, title, path, document_type, date, tags
                FROM documents
                WHERE {where_sql}
                ORDER BY date DESC NULLS LAST, updated_at DESC
                LIMIT :limit
            """),
            params,
        )
        return [
            {
                "id": row[0],
                "title": row[1],
                "path": row[2],
                "document_type": row[3],
                "date": row[4],
                "tags": row[5],
            }
            for row in result.fetchall()
        ]
