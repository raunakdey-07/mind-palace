# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Context packing: turn memory into model-ready context.

Search answers "what is relevant?"; context packing answers "what should I
give the model?".

Two paths share one pack so callers do not have to know which one ran:

- Authoritative. The archive resolves current state, history, validity,
  supersession and conflicts first. Those answers are the pack's ``memories``,
  ``conflicts`` and ``changes``, each with exact evidence. If the archive holds
  nothing relevant, the pack says so and stays empty. Retrieval results never
  promote a chunk into a memory.
- Retrieval-only. A corpus with no archive has no authority to assert absence,
  so ranked chunks are the pack and ``status`` reports what they are.

Retrieval in the authoritative path is an optimization: it adds raw source
material, never a claim, and is skipped entirely when the model is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

# ~4 characters per token is the standard rough estimate for English text.
CHARS_PER_TOKEN = 4

DEFAULT_BUDGET_TOKENS = 4096

# Pack status. Absence is a value, not an empty list.
RESOLVED = "resolved"
CONFLICTING = "conflicting"
NO_RELEVANT_MEMORY = "no_relevant_memory"
EMPTY_CORPUS = "empty_corpus"


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
class ContextEvidence:
    """One exact quote from one immutable document version."""

    quote: str
    path: str
    version_id: str
    observed_at: str
    start_offset: int
    end_offset: int


@dataclass
class ContextMemory:
    """One authoritative statement, with the evidence that supports it."""

    status: str
    key: str
    claim: str
    value: Any = None
    path: str = ""
    observed_at: str = ""
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    supersedes_id: Optional[str] = None
    evidence: List[ContextEvidence] = field(default_factory=list)


@dataclass
class ContextConflict:
    """Authored keys whose sources disagree. Never silently flattened."""

    key: str
    options: List[ContextMemory] = field(default_factory=list)


@dataclass
class ContextChange:
    """One recorded lifecycle or supersession event."""

    event: str
    path: str
    observed_at: str
    relationship: str
    from_value: Optional[str] = None
    to_value: Optional[str] = None


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
    # Authoritative structure. Populated when the corpus has an archive.
    status: str = RESOLVED
    memories: List[ContextMemory] = field(default_factory=list)
    conflicts: List[ContextConflict] = field(default_factory=list)
    changes: List[ContextChange] = field(default_factory=list)
    as_of: Optional[str] = None
    valid_at: Optional[str] = None
    budget_unit: str = "token_estimate"

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
            "status": self.status,
            "budget_unit": self.budget_unit,
            "as_of": self.as_of,
            "valid_at": self.valid_at,
            "memories": [_memory_dict(m) for m in self.memories],
            "conflicts": [
                {
                    "key": c.key,
                    "options": [_memory_dict(o) for o in c.options],
                }
                for c in self.conflicts
            ],
            "changes": [
                {
                    "event": c.event,
                    "path": c.path,
                    "observed_at": c.observed_at,
                    "relationship": c.relationship,
                    "from_value": c.from_value,
                    "to_value": c.to_value,
                }
                for c in self.changes
            ],
        }


def _memory_dict(memory: ContextMemory) -> dict:
    return {
        "status": memory.status,
        "key": memory.key,
        "claim": memory.claim,
        "value": memory.value,
        "path": memory.path,
        "observed_at": memory.observed_at,
        "valid_from": memory.valid_from,
        "valid_until": memory.valid_until,
        "supersedes_id": memory.supersedes_id,
        "evidence": [
            {
                "quote": e.quote,
                "path": e.path,
                "version_id": e.version_id,
                "observed_at": e.observed_at,
                "start_offset": e.start_offset,
                "end_offset": e.end_offset,
            }
            for e in memory.evidence
        ],
    }


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _evidence_for(claim, evidence_by_id: dict) -> List[ContextEvidence]:
    out = []
    for eid in claim.evidence_ids:
        row = evidence_by_id.get(eid)
        if row is None:
            continue
        out.append(
            ContextEvidence(
                quote=row.text,
                path=row.path,
                version_id=row.version_id,
                observed_at=_iso(row.observed_at) or "",
                start_offset=row.start_offset,
                end_offset=row.end_offset,
            )
        )
    return out


def _to_memory(claim, evidence_by_id: dict) -> ContextMemory:
    return ContextMemory(
        status=claim.status,
        key=claim.key,
        claim=claim.claim,
        value=claim.value,
        path=claim.path,
        observed_at=_iso(claim.observed_at) or "",
        valid_from=_iso(claim.valid_from),
        valid_until=_iso(claim.valid_until),
        supersedes_id=claim.supersedes_id,
        evidence=_evidence_for(claim, evidence_by_id),
    )


def _describe(memory: ContextMemory) -> str:
    line = f"- {memory.claim} [{memory.status}]"
    if memory.valid_from:
        line += f" (valid from {memory.valid_from[:10]}"
        line += f" to {memory.valid_until[:10]})" if memory.valid_until else ")"
    elif memory.supersedes_id:
        line += " (supersedes an earlier statement)"
    where = sorted({e.path for e in memory.evidence if e.path})
    if where:
        line += f" source: {', '.join(where)}"
    return line


def _evidence_line(memory: ContextMemory, indent: str = "  ") -> Optional[str]:
    if not memory.evidence:
        return None
    first = memory.evidence[0]
    return (
        f'{indent}evidence: "{first.quote}" ({first.path} '
        f"[{first.start_offset}:{first.end_offset}], observed {first.observed_at[:10]})"
    )


def _sections(memory, evidence_by_id: dict) -> list[tuple[str, list[str]]]:
    """Ordered, named blocks. Absence is reported, never padded."""
    current = [_to_memory(c, evidence_by_id) for c in memory.current_memories]
    historical = [_to_memory(c, evidence_by_id) for c in memory.historical_memories]
    uncertain = [_to_memory(c, evidence_by_id) for c in memory.uncertain_memories]
    conflicts = [
        ContextConflict(
            key=group.key,
            options=[_to_memory(c, evidence_by_id) for c in group.claims],
        )
        for group in memory.conflicts
    ]
    changes = [
        ContextChange(
            event=change.event,
            path=change.path,
            observed_at=_iso(change.observed_at) or "",
            relationship=change.relationship,
            from_value=next((str(c.value) for c in change.previous), None),
            to_value=next((str(c.value) for c in change.current), None),
        )
        for change in memory.changes
    ]

    out: list[tuple[str, list[str]]] = []
    if current:
        lines: list[str] = []
        for item in current:
            lines.append(_describe(item))
            quote = _evidence_line(item)
            if quote:
                lines.append(quote)
        out.append(("CURRENT", lines))
    if conflicts:
        lines = []
        for group in conflicts:
            lines.append(f"- {group.key}: sources disagree")
            for option in group.options:
                lines.append(f"    * {option.claim} ({option.value}) [{option.status}]")
                quote = _evidence_line(option, indent="      ")
                if quote:
                    lines.append(quote)
        out.append(("CONFLICT", lines))
    if changes:
        lines = []
        for change in changes:
            movement = ""
            if change.from_value and change.to_value:
                movement = f": {change.from_value} -> {change.to_value}"
            lines.append(
                f"- {change.observed_at[:10]} {change.path} ({change.relationship}){movement}"
            )
        out.append(("CHANGES", lines))
    if historical:
        out.append(("HISTORY", [_describe(item) for item in historical]))
    if uncertain:
        out.append(("UNCERTAIN", [_describe(item) for item in uncertain]))
    return out


def _assemble_authoritative(
    query: str,
    memory,
    results,
    budget_tokens: int,
    strategy: str,
) -> ContextPack:
    """Build the pack from archive authority, with retrieval as raw material only."""
    evidence_by_id = {e.id: e for e in memory.evidence}
    sections = _sections(memory, evidence_by_id)
    has_conflict = any(name == "CONFLICT" for name, _ in sections)

    memories = [_to_memory(claim, evidence_by_id) for claim in memory.current_memories]
    historical = [_to_memory(c, evidence_by_id) for c in memory.historical_memories]
    uncertain = [_to_memory(c, evidence_by_id) for c in memory.uncertain_memories]
    conflicts = [
        ContextConflict(key=g.key, options=[_to_memory(c, evidence_by_id) for c in g.claims])
        for g in memory.conflicts
    ]
    changes = [
        ContextChange(
            event=c.event,
            path=c.path,
            observed_at=_iso(c.observed_at) or "",
            relationship=c.relationship,
            from_value=next((str(x.value) for x in c.previous), None),
            to_value=next((str(x.value) for x in c.current), None),
        )
        for c in memory.changes
    ]

    if not sections:
        # The archive either holds nothing relevant, or resolved claims that the
        # pack budget dropped. Both are honest answers; neither is padded.
        resolved_nothing = NO_RELEVANT_MEMORY in memory.constraints
        return ContextPack(
            query=query,
            context="",
            status=NO_RELEVANT_MEMORY,
            empty_reason=NO_RELEVANT_MEMORY if resolved_nothing else "budget",
            strategy=strategy,
            truncated=not resolved_nothing or memory.truncated,
            budget_unit="token_estimate",
            as_of=_iso(memory.state.as_of),
            valid_at=_iso(memory.state.valid_at),
        )

    status = CONFLICTING if has_conflict else RESOLVED
    budget_chars = budget_tokens * CHARS_PER_TOKEN
    rendered: list[str] = []
    used = 0
    truncated = memory.truncated
    for name, lines in sections:
        block = name + "\n" + "\n".join(lines)
        if used + len(block) + 5 <= budget_chars:
            rendered.append(block)
            used += len(block) + 5
            continue
        truncated = True
        break

    if not rendered and sections:
        # Even the first section exceeds the budget; cut it deterministically
        # rather than returning nothing.
        name, lines = sections[0]
        block = (name + "\n" + "\n".join(lines))[:budget_chars]
        rendered.append(block)
        truncated = True

    # Remaining budget goes to raw source material. Retrieval never becomes a memory.
    chunks: List[ContextChunk] = []
    sources: List[ContextSource] = []
    if rendered and results:
        for rank, r in enumerate(results, start=1):
            body = (r.text or "").strip()
            if not body:
                continue
            if used + len(body) + 5 > budget_chars:
                truncated = True
                break
            source = ContextSource(
                title=r.source_title or r.source_path or "unknown",
                path=r.source_path or "",
                doc_id=r.doc_id,
                heading_path=r.heading_path,
                document_type=r.source_document_type,
            )
            chunks.append(ContextChunk(text=body, score=r.score, rank=rank, source=source))
            used += len(body) + 5
            if source.doc_id not in {s.doc_id for s in sources}:
                sources.append(source)

    context_text = "\n\n---\n\n".join(rendered)
    if not sources:
        # Attribution comes from kept evidence and kept chunks only.
        for item in memories + historical + uncertain + [o for c in conflicts for o in c.options]:
            for e in item.evidence:
                doc_id = e.version_id
                if doc_id in {s.doc_id for s in sources}:
                    continue
                sources.append(
                    ContextSource(
                        title=e.path.rsplit("/", 1)[-1],
                        path=e.path,
                        doc_id=doc_id,
                    )
                )
    return ContextPack(
        query=query,
        context=context_text,
        sources=sources,
        chunks=chunks,
        token_estimate=estimate_tokens(context_text),
        strategy=strategy,
        truncated=truncated,
        status=status,
        memories=memories,
        conflicts=conflicts,
        changes=changes,
        as_of=_iso(memory.state.as_of),
        valid_at=_iso(memory.state.valid_at),
    )


def pack_context(
    query: str,
    results,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    strategy: str = "hybrid_rrf",
    memory=None,
) -> ContextPack:
    """Pack memory into model-ready context within a token budget.

    Pass ``memory`` (an authoritative ``MemoryResponse``) when the corpus has an
    archive. The archive decides what is true and what is absent; ``results``
    only add raw source material. Omit it for a corpus with no archive, where
    ranked chunks are the pack.
    """
    if memory is not None:
        return _assemble_authoritative(query, memory, results, budget_tokens, strategy)
    return _pack_retrieval_only(query, results, budget_tokens, strategy)


def _pack_retrieval_only(
    query: str,
    results,
    budget_tokens: int,
    strategy: str,
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
            status=EMPTY_CORPUS,
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
        status=RESOLVED,
    )
