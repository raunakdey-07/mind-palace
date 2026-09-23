"""Question relevance over immutable claims; scores never establish authority.

Uses the configured sentence-transformer, not live chunk embeddings (which vanish
on deletion). Vectors are transient: no persistence contract or ingestion changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from api.models.memory import MemoryRequest, MemoryResponse


class ClaimEmbedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class RankedClaim:
    id: str
    score: float


def tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def rank_claims(
    full: MemoryResponse, question: str, embedder: ClaimEmbedder, strategy: str = "embedding"
) -> list[RankedClaim]:
    """Rank archived claim representations; return IDs, never reinterpret status.

    Hybrid uses the existing retrieval service's reciprocal rank fusion constant.
    Lexical is token overlap, with the original AND lookup measured separately.
    """
    from api.services.memory_public import _claims

    claims = {c.id: c for c in _claims(full)}
    ids = sorted(claims)
    if not ids:
        return []
    texts = [f"{claims[c].claim} {claims[c].key} {claims[c].path}" for c in ids]
    query_tokens = tokens(question)
    lexical = {
        cid: len(query_tokens & tokens(text)) / max(1, len(query_tokens))
        for cid, text in zip(ids, texts)
    }
    if strategy == "lexical":
        scores = lexical
    else:
        vectors = embedder.embed([question] + texts)
        semantic = {
            cid: sum(a * b for a, b in zip(vectors[0], vector))
            for cid, vector in zip(ids, vectors[1:])
        }
        if strategy == "embedding":
            scores = semantic
        elif strategy == "hybrid":
            scores = dict.fromkeys(ids, 0.0)
            for ranking in (lexical, semantic):
                for rank, cid in enumerate(sorted(ids, key=lambda c: (-ranking[c], c)), 1):
                    scores[cid] += 1 / (60 + rank)
        else:
            raise ValueError("Unknown memory ranking strategy")
    return [RankedClaim(cid, scores[cid]) for cid in sorted(ids, key=lambda c: (-scores[c], c))]


@dataclass(frozen=True)
class QueryIntent:
    name: str
    as_of: datetime | None = None


def interpret(request: MemoryRequest) -> QueryIntent:
    """Structured selectors win. Date-only questions mean midnight UTC observed time.

    Relative phrases select history, not invented valid dates. Stage labels need
    an explicit as_of/snapshot selector from the caller's own stage mapping.
    """
    from api.services.memory_public import MemoryError

    question = request.query.casefold()
    if not question:
        raise MemoryError("invalid_query", "A nonempty question is required", 422)
    cutoff = request.as_of
    explicit_time = request.as_of is not None or request.snapshot_id is not None
    if not explicit_time:
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", question)
        if len(dates) > 1:
            raise MemoryError("invalid_timestamp", "Use one explicit as_of timestamp", 422)
        if dates:
            try:
                cutoff = datetime.fromisoformat(dates[0]).replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise MemoryError("invalid_timestamp", "Invalid question date", 422) from exc
        elif re.search(r"\bstage\s+[a-z0-9]+\b", question):
            raise MemoryError("invalid_timestamp", "Stage names require as_of or snapshot_id", 422)
    if request.intent != "auto":
        name = request.intent
    elif cutoff or request.snapshot_id:
        name = "temporal"
    elif re.search(r"\b(conflict\w*|disagree\w*|contradict\w*)\b", question):
        name = "conflict"
    elif re.search(r"\b(evidence|provenance|source|sources|quote)\b", question):
        name = "provenance"
    elif re.search(r"\b(changed?|replaced?|supersed\w*)\b", question):
        name = "change"
    elif re.search(r"\b(now|currently|current)\b", question):
        name = "current"
    elif re.search(
        r"\b(before|after|previous|formerly|used to|historical|history|old|prior)\b", question
    ):
        name = "historical"
    else:
        name = "current"
    if name == "temporal" and cutoff is None and request.snapshot_id is None:
        raise MemoryError(
            "invalid_timestamp", "Temporal questions require as_of or snapshot_id", 422
        )
    return QueryIntent(name, cutoff)


def select(
    full: MemoryResponse,
    request: MemoryRequest,
    intent: QueryIntent,
    embedder: ClaimEmbedder,
    policy=None,
) -> MemoryResponse:
    """Resolve first, then select relevant authored keys and complete conflict groups.

    Historical vocabulary can find an authored key, but only the persistence
    projection determines which claim in that key is CURRENT. A 0.30 cosine floor
    and 90% top-score band are relevance heuristics, not truth/authority scores.
    """
    from api.services.memory_public import _attach, _claims

    claims = {c.id: c for c in _claims(full)}
    from api.services.memory_relevance import RelevancePolicy, relevance

    key_scores, _, _ = relevance(
        full, request, intent.name, embedder, policy or RelevancePolicy.configured()
    )
    keys = set(key_scores)
    selected = {
        c.id
        for c in claims.values()
        if c.key in keys and (not request.path or c.path == request.path)
    }
    result = MemoryResponse(query=request.query, corpus=full.corpus, state=full.state)
    fields = ["current_memories"]
    if intent.name in {"historical", "change", "provenance"}:
        fields += ["historical_memories", "uncertain_memories"]
    for field in fields:
        setattr(
            result,
            field,
            sorted(
                [c for c in getattr(full, field) if c.id in selected],
                key=lambda c: (-key_scores.get(c.key, 0), c.observed_at, c.id),
            ),
        )
    if intent.name in {"historical", "change"}:
        result.changes = [
            change
            for change in full.changes
            if any(c.id in selected for c in change.previous + change.current)
        ]
    connected = selected | {c.id for c in _claims(result)}
    while True:
        groups = [g for g in full.conflicts if any(c.id in connected for c in g.claims)]
        expanded = connected | {c.id for g in groups for c in g.claims}
        if expanded == connected:
            break
        connected = expanded
    result.conflicts = groups
    _attach(result, {e.id: e for e in full.evidence})
    if not _claims(result):
        result.constraints = ["NO_RELEVANT_MEMORY"]
    return result


async def query(full: MemoryResponse, request: MemoryRequest) -> MemoryResponse:
    """Keep model inference off the async database/event-loop thread."""
    from asyncio import to_thread

    from api.services.embedder import Embedder
    from api.services.memory_public import MemoryError, bounded_pack

    intent = interpret(request)

    def retrieve():
        return select(full, request, intent, Embedder())

    try:
        result = await to_thread(retrieve)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MemoryError("memory_unavailable", "Memory embedding model unavailable", 503) from exc
    return bounded_pack(result, request.budget)
