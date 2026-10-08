"""Question relevance over immutable claims; scores never establish authority.

Uses the configured sentence-transformer, not live chunk embeddings (which vanish
on deletion). Vectors are transient: no persistence contract or ingestion changes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from api.models.memory import MemoryRequest, MemoryResponse

logger = logging.getLogger("mindpalace.ops")


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
        r"\b(before|after|previous(ly)?|former(ly)?|used to|histor(y|ical|ies)|"
        r"history|earlier|old(er)?|prior)\b",
        question,
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
    lexical: bool = False,
    claim_vectors: dict[str, list[float]] | None = None,
    vectorized: bool = False,
    fresh: dict[str, list[float]] | None = None,
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
        full,
        request,
        intent.name,
        embedder,
        policy or RelevancePolicy.configured(),
        lexical=lexical,
        claim_vectors=claim_vectors,
        vectorized=vectorized,
        fresh=fresh,
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


async def query(
    full: MemoryResponse,
    request: MemoryRequest,
    *,
    db=None,
    corpus_id: str | None = None,
    vectorized: bool = False,
) -> MemoryResponse:
    """Keep model inference off the async database/event-loop thread.

    If the embedding model cannot load, fall back to lexical relevance instead of
    failing. Ranking degrades; authority does not. Claim status, validity, conflicts
    and evidence all still come from the persistence projection.

    When a session and corpus are supplied, claim representations are read from the
    L2 cache so a warm corpus embeds only the question. The read is done here, on
    the async side, so the scoring thread does no I/O.

    Vectors this query has to embed are written back to that cache. The encoder
    already computed them, so persisting them costs one insert per claim and saves
    the next query the whole batch. The alternative -- filling the cache only on
    write -- leaves any corpus authored without embeddings re-encoded on every
    single read: measured at 3.2 s per recall at 200 statements, against 94 ms once
    the cache is warm. `mindpalace remember` deliberately does not embed, because
    importing the model costs 5.3 s and a first memory must not pay that.

    The write-back is bounded three ways, because a read that writes is a real
    hazard and has to be contained rather than merely intended. It runs in its own
    SAVEPOINT, so a failure cannot poison the caller's transaction. It is skipped on
    a read-only session, so a read-only connection still answers. And it is
    best-effort, so nothing here can fail a query that has already been answered.
    The cache holds no authority -- deleting the table costs latency and nothing else
    -- and `mindpalace reindex` still rebuilds every row from immutable claim text.
    """
    from asyncio import to_thread

    from api.services.memory_public import MemoryError, bounded_pack
    from api.services.retrieval_mode import lexical_requested

    intent = interpret(request)

    # The one place the two relevance modes diverge. Both go through `select` and
    # through the same acceptance gates; this only chooses the scorer.
    lexical_only = lexical_requested()

    embedder = None
    cached: dict[str, list[float]] = {}
    wanted: dict[str, str] = {}
    # In lexical mode the cache is not even read. Answering that read needs the
    # model's dimension, and reaching for the model is the thing being avoided --
    # which is the whole point of asking for this mode.
    if db is not None and corpus_id is not None and not lexical_only:
        try:
            from api.services.claim_embeddings import (
                load_cached,
                representation,
                representation_hash,
            )
            from api.services.embedder import Embedder
            from api.services.memory_public import _claims

            embedder = Embedder()
            wanted = {c.id: representation_hash(representation(c)) for c in _claims(full)}
            cached = await load_cached(
                db, corpus_id, wanted, embedder.model_name, embedder.dimension
            )
        except Exception:  # noqa: BLE001 - a cache miss or a dead model is not a failure
            embedder, cached = None, {}

    fresh: dict[str, list[float]] = {}

    def retrieve():
        if lexical_only:
            # The model-free rung, reached on purpose rather than by failure. This is
            # the same call the no-model degradation makes.
            return select(full, request, intent, None, lexical=True)
        if embedder is None:
            from api.services.embedder import Embedder as Lazy

            return select(
                full,
                request,
                intent,
                Lazy(),
                claim_vectors=cached or None,
                vectorized=vectorized,
                fresh=fresh,
            )
        return select(
            full,
            request,
            intent,
            embedder,
            claim_vectors=cached or None,
            vectorized=vectorized,
            fresh=fresh,
        )

    def retrieve_lexically():
        return select(full, request, intent, None, lexical=True)

    try:
        result = await to_thread(retrieve)
    except (OSError, RuntimeError, ValueError, ImportError):
        # A partial batch must not be cached: the fallback scored a different way.
        fresh.clear()
        _note_degraded()
        try:
            result = await to_thread(retrieve_lexically)
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            raise MemoryError("memory_unavailable", "Memory retrieval unavailable", 503) from exc

    # `wanted` is empty when the cache lookup above failed, and `store` matches on
    # it, so warming then would encode the corpus and persist nothing.
    if fresh and wanted and embedder is not None and db is not None and corpus_id is not None:
        await _warm_claim_cache(db, corpus_id, wanted, fresh, embedder)

    return bounded_pack(result, request.budget)


def _note_degraded() -> None:
    """Say, once, that a semantic read answered lexically because no model loaded.

    The degradation is correct and long-standing: ranking degrades, authority does
    not. What was missing was that it was silent, so a user could not tell a lexical
    answer from a semantic one -- and the two select differently. On stderr, once
    per process, because a query loop would otherwise repeat it forever.
    """
    import sys

    global _DEGRADED_NOTED
    if _DEGRADED_NOTED:
        return
    _DEGRADED_NOTED = True
    print(
        "retrieval: lexical (the embedding model could not be loaded, so relevance "
        "fell back to token overlap; set MIND_PALACE_LEXICAL=1 to choose this on "
        "purpose, or install the model for semantic ranking)",
        file=sys.stderr,
    )


_DEGRADED_NOTED = False


async def _warm_claim_cache(db, corpus_id: str, wanted: dict, fresh: dict, embedder) -> None:
    """Persist the vectors this query just encoded. Best-effort, and never raises.

    A cache that cannot be written costs the next query some latency. Failing the
    query that has already been answered would cost a great deal more.
    """
    from api.services.claim_embeddings import store

    try:
        if _read_only(db):
            return
        async with db.begin_nested():
            await store(
                db,
                corpus_id,
                wanted,
                fresh,
                embedder.model_name,
                embedder.dimension,
                getattr(embedder, "version", "query"),
                commit=False,
            )
    except Exception:  # noqa: BLE001 - a cache write must never fail a read
        logger.debug("claim_embedding_cache_warm_failed", exc_info=True)


def _read_only(db) -> bool:
    """True when this session was opened for reading, so it must not write.

    Asked of the engine rather than carried on a flag, because the session may have
    been created by a caller this code never sees.
    """
    try:
        engine = db.get_bind()
        return bool(engine.sync_engine.get_execution_options().get("postgresql_readonly"))
    except Exception:  # noqa: BLE001 - a probe about the session never raises
        return False
