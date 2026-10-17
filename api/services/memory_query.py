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


def adjudicate(
    full: MemoryResponse,
    result: MemoryResponse,
    scored: dict[str, float],
    policy,
    *,
    dated: bool = False,
) -> None:
    """Say how far the ranking can be trusted, and carry the near-misses with it.

    A response that returns only its top hit cannot be acted on when that hit is
    wrong, and it cannot be detected as wrong either. On the 175 answerable dev
    questions, rank 1 is right for 116 and wrong for 59, and the median
    `score_1 - score_2` of a wrong rank 1 is 0.046 against 0.095 for a right one --
    overlapping distributions, so no single score separates them.

    So the answer carries its own confidence and the three best claims behind it,
    each with the exact quote that backs it. `ambiguous` is not a refusal: it says
    the ranking cannot choose on its own, and hands over what it would need to.

    Three states, in the order they are decided:

    `abstained`   nothing cleared the floor. Nothing to adjudicate, so nothing is
                  listed. An abstention that advertised sixty weak candidates
                  would be describing the corpus, not the answer.
    `ambiguous`   rank 1 is near the floor, or a competitor carrying a *different*
                  value came within the margin of it.
    `confident`   otherwise.

    The second ambiguous condition compares canonical values, not claim ids. Two
    claims holding the same value are interchangeable, so a near-tie between them
    needs no adjudication and does not earn the warning.

    `dated` says the question names a time. It switches off the one thing this
    function knows that the scorer does not: with no time constraint in the
    question, a claim scoped to an interval is not the standing answer. See
    `memory_relevance.authority_order` for what that is worth, and for why it is
    the only such rule that measured better than doing nothing.
    """
    from api.models.memory import Candidate
    from api.services.memory_relevance import (
        CANDIDATE_FLOOR,
        CANDIDATE_LIMIT,
        authority_order,
        margin_threshold,
        near_floor,
    )
    from api.services.memory_public import _claims

    result.match_status = "abstained"
    result.match_margin = None
    result.candidates = []
    if not scored or not _claims(result):
        return

    from api.services.memory import _canonical

    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    # Only claims the response can actually vouch for. A candidate the caller
    # cannot check the source of is worse than no candidate.
    shortlist = ranked[:CANDIDATE_LIMIT]
    live = {key: [c for c in _claims(result) if c.key == key] for key, _score in shortlist}
    # A key with no claim in the response cannot be shown to the caller, so it is
    # not a competitor they can adjudicate against and the margin must not be
    # quoted against it. `vouchable` is the shortlist the response can back up.
    vouchable = [entry for entry in shortlist if live.get(entry[0])]
    if not vouchable:
        return
    # Which one the caller reads first, and which pair the margin describes, are
    # two different questions. `authority_order` may put a lower-scoring claim
    # first, so the margin below is taken from the score order of `vouchable`,
    # never from the order the candidates are listed in. A near-tie that has been
    # re-ordered by scope is still a near-tie, and reporting it as `confident`
    # would launder the scorer's uncertainty into a confident-sounding number.
    listed = authority_order(vouchable, live, dated=dated, band=margin_threshold())
    quotes = {e.id: e.text for e in full.evidence}
    candidates: list[Candidate] = []
    for key, score in listed:
        claims = live.get(key) or []
        if not claims:
            continue
        seen: list[str] = []
        for claim in sorted(claims, key=lambda c: c.id):
            for eid in claim.evidence_ids:
                text = quotes.get(eid)
                if text and text not in seen:
                    seen.append(text)
        candidates.append(
            Candidate(
                key=key,
                # Six places, because a cosine printed at full precision implies
                # a discrimination the scorer does not have.
                score=round(float(score), 6),
                status=claims[0].status,
                value=claims[0].value,
                claim_ids=sorted(c.id for c in claims),
                version_ids=sorted({c.version_id for c in claims}),
                path=claims[0].path,
                quotes=seen[:3],
            )
        )
    if not candidates:
        return
    result.candidates = candidates

    # The margin describes the shortlist that was returned, not the whole ranking.
    # A key the caller cannot see is not a competitor it can adjudicate against,
    # and a margin quoted against one would describe a decision nobody is offered.
    best = vouchable[0][1]
    second = vouchable[1][1] if len(vouchable) > 1 else 0.0
    margin = best - second
    result.match_margin = round(margin, 6)

    if best < CANDIDATE_FLOOR:
        # Nothing this far above nothing is a near-tie. Reporting one would be
        # noise dressed as caution.
        result.match_status = "confident"
        return
    if best < max(policy.minimum, 0.0) + near_floor():
        # Weak in absolute terms, whatever beat it.
        result.match_status = "ambiguous"
        return
    if margin < margin_threshold():
        top_value = _canonical(candidates[0].value)
        for candidate in candidates[1:]:
            # Two claims holding the same value are interchangeable, so a near-tie
            # between them needs no adjudication and does not earn the warning.
            if _canonical(candidate.value) == top_value:
                continue
            result.match_status = "ambiguous"
            return
    result.match_status = "confident"


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

    policy = policy or RelevancePolicy.configured()
    key_scores, _, counters = relevance(
        full,
        request,
        intent.name,
        embedder,
        policy,
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
    if intent.name in {"historical", "change", "provenance"}:
        # Follow the supersession edges backwards. A claim read out of plain
        # Markdown carries a positional key derived from its own heading path, so
        # the claim it replaced almost always sits under a *different* key and was
        # never going to be selected on its own. Without this, `adr-002` superseding
        # `adr-001` retires the older decision correctly and then hides it: `history`
        # reports nothing, and the reader cannot see that anything was replaced.
        #
        # Only edges leaving a claim that was itself selected. Following every edge in
        # the corpus would drag unrelated history into any answer that mentioned the
        # word "database".
        selected |= {
            claim.supersedes_id
            for claim in claims.values()
            if claim.key in keys
            and claim.supersedes_id
            and (not request.path or claim.path == request.path)
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
    else:
        adjudicate(
            full,
            result,
            counters.get("scored") or {},
            policy,
            dated=intent.as_of is not None or request.as_of is not None,
        )
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
    except Exception as exc:  # noqa: BLE001 - a cache write must never fail a read
        logger.debug(
            "claim_embedding_cache_warm_failed error=%s",
            type(exc).__name__,
            # No traceback -- it would carry the claim text. See
            # `unexpected_error` in api/main.py.
        )


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
