# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Context packing: turn retrieval results into model-ready context.

Search answers "what is relevant?"; context packing answers "what should I
give the model?". The packer retrieves candidate evidence, deduplicates
document-level evidence, applies an explicit budget, preserves strongest
evidence first, and maintains source attribution throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# ~4 characters per token is the standard rough estimate for English text.
CHARS_PER_TOKEN = 4

DEFAULT_BUDGET_TOKENS = 4096


def estimate_tokens(text: str) -> int:
    """Rough token estimate for a text string."""
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


@dataclass
class ContextSource:
    """Provenance for one document contributing to the context."""

    title: str
    path: str
    doc_id: str
    heading_path: Optional[str] = None
    document_type: Optional[str] = None


@dataclass
class ContextChunk:
    """One evidence chunk in the pack."""

    text: str
    score: float
    rank: int
    source: ContextSource


@dataclass
class ContextPack:
    """Model-ready context with full attribution."""

    query: str
    context: str
    sources: List[ContextSource] = field(default_factory=list)
    chunks: List[ContextChunk] = field(default_factory=list)
    token_estimate: int = 0
    strategy: str = "hybrid_rrf"
    truncated: bool = False
    empty_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "context": self.context,
            "sources": [
                {
                    "title": s.title,
                    "path": s.path,
                    "doc_id": s.doc_id,
                    "heading_path": s.heading_path,
                    "document_type": s.document_type,
                }
                for s in self.sources
            ],
            "chunks": [
                {
                    "text": c.text,
                    "score": c.score,
                    "rank": c.rank,
                    "source": {
                        "title": c.source.title,
                        "path": c.source.path,
                        "doc_id": c.source.doc_id,
                        "heading_path": c.source.heading_path,
                        "document_type": c.source.document_type,
                    },
                }
                for c in self.chunks
            ],
            "token_estimate": self.token_estimate,
            "strategy": self.strategy,
            "truncated": self.truncated,
        }


def pack_context(
    query: str,
    results,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    strategy: str = "hybrid_rrf",
) -> ContextPack:
    """Pack retrieval results into model-ready context within a token budget.

    Rules:
    - Results are assumed ordered strongest-first (retrieval guarantees this).
    - Chunks are added until the budget is hit; overflow is dropped from the
      bottom (weakest evidence), never silently truncated mid-chunk unless a
      single chunk alone exceeds the entire budget.
    - A chunk whose text duplicates already-packed text from the same document
      is skipped (deduplication).
    - Sources derive only from kept chunks — attribution can never reference
      dropped evidence.
    - ``truncated`` reports whether any evidence was dropped or cut.
    """
    if not results:
        return ContextPack(
            query=query,
            context="",
            strategy=strategy,
            empty_reason="no_retrieval_results",
        )

    budget_chars = budget_tokens * CHARS_PER_TOKEN
    kept: List[ContextChunk] = []
    kept_texts: set[str] = set()
    seen_doc_texts: dict[str, set[str]] = {}
    total_chars = 0
    truncated = False

    for rank, r in enumerate(results, start=1):
        text_body = r.text.strip()
        if not text_body:
            continue

        # Deduplicate identical evidence.
        if text_body in kept_texts:
            truncated = True
            continue

        source = ContextSource(
            title=r.source_title or r.source_path or "unknown",
            path=r.source_path or "",
            doc_id=r.doc_id,
            heading_path=r.heading_path,
            document_type=r.source_document_type,
        )

        chunk_len = len(text_body)

        if total_chars + chunk_len <= budget_chars:
            kept.append(ContextChunk(text=text_body, score=r.score, rank=rank, source=source))
            kept_texts.add(text_body)
            seen_doc_texts.setdefault(r.doc_id, set()).add(text_body)
            total_chars += chunk_len
            continue

        # Budget exceeded by this chunk.
        if not kept:
            # Single oversized chunk: truncate it deterministically so the
            # caller still receives their best evidence.
            cut = text_body[:budget_chars]
            kept.append(ContextChunk(text=cut, score=r.score, rank=rank, source=source))
            total_chars = budget_chars
            truncated = True
            break

        truncated = True
        continue  # drop weakest remainder

    context_text = "\n\n---\n\n".join(c.text for c in kept)
    sources: list[ContextSource] = []
    seen_ids: set[str] = set()
    for c in kept:
        if c.source.doc_id not in seen_ids:
            seen_ids.add(c.source.doc_id)
            sources.append(c.source)

    return ContextPack(
        query=query,
        context=context_text,
        sources=sources,
        chunks=kept,
        token_estimate=estimate_tokens(context_text),
        strategy=strategy,
        truncated=truncated,
    )
