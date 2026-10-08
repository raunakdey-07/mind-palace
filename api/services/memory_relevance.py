"""Bounded, deterministic question variants and relevance gates (never state rules).

All vocabulary comes from the query/archive. No generated facts or domain alias
map. Config is process-level, so adapters cannot bypass the relevance policy.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any

from api.models.memory import MemoryRequest, MemoryResponse

# Function/temporal words are not independent evidence of a matching subject.
STOP = frozenset("""a an the of for to in on at by from with without and or as well
what which who where when why how is are was were be been do does did we our it
its them they their this that these those use uses used currently current now
previous previously before after then than rather during once still already
have has had can could would should will may might selected decision decisions
architecture project system application documented fact contract assertion
source sources document documents claim claims evidence exact show tell list
about regarding both also same all any each one two know known knowledge
stage checkpoint pilot cutover expansion federation clarification restored
restoration deleted deletion historical history formerly earlier later old
looking back today tomorrow setting aside ignore regardless between until
""".split())


def terms(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9]+", text.casefold())
    result = set()
    for word in words:
        if word in STOP:
            continue
        for suffix in ("ation", "ing", "ed", "s"):
            if word.endswith(suffix) and len(word) > len(suffix) + 3:
                word = word[: -len(suffix)]
                break
        result.add(word)
    return result


@dataclass(frozen=True)
class RelevancePolicy:
    approach: str = "E"
    minimum: float = 0.30
    strong: float = 0.65
    relative: float = 0.90
    max_topics: int = 4

    def __post_init__(self):
        if self.approach not in "ABCDE" or len(self.approach) != 1:
            raise ValueError("Unknown relevance approach")
        if not all(
            math.isfinite(v) and 0 <= v <= 1 for v in (self.minimum, self.strong, self.relative)
        ):
            raise ValueError("Relevance thresholds must be finite values in [0,1]")
        if self.strong < self.minimum or not 1 <= self.max_topics <= 4:
            raise ValueError("Invalid relevance configuration")

    @classmethod
    def configured(cls):
        return cls(
            minimum=float(os.getenv("MEMORY_QUERY_MINIMUM", "0.30")),
            strong=float(os.getenv("MEMORY_QUERY_STRONG", "0.65")),
            relative=float(os.getenv("MEMORY_QUERY_RELATIVE", "0.90")),
        )


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    topics: tuple[str, ...]
    intent: str
    temporal_selector: str | None
    path: str | None


def subject(question: str) -> str:
    """Remove bounded temporal framing, never interpret source text as instructions."""
    text = question
    # Introductory framing is not the requested subject.
    text = re.sub(
        r"^(?:looking back|viewed|setting aside|ignore|regardless|without|"
        r"instead of|rather than|as distinct from|as opposed to)[^,;]*[,;]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?:at|before|after|prior to|following|during|once)\s+[^,;]+[,;]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?:looking back|viewed|setting aside|ignore|regardless|without|"
        r"instead of|rather than|as distinct from|as opposed to)[^,;]*[,;]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:at|in|on|during)\s+(?:the\s+)?stage\s+[a-z0-9]+\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:t[^\s?]+)?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(?:before|after|at|in|during|once)\s+(?:the\s+)?"
        r"(?:pilot|cutover|expansion|federation|ownership-clarification|"
        r"restored-runbook|capacity expansion|restoration)(?:\s+checkpoint)?\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(text.split()).strip(" ,;") or question


def plan(request: MemoryRequest, intent: str, policy: RelevancePolicy) -> QueryPlan:
    text = subject(request.query) if policy.approach == "E" else request.query
    if policy.approach == "E":
        text = re.split(
            r",?\s+(?:rather than|as distinct from|as opposed to)\b",
            text,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
    variants = [text]
    if policy.approach in {"D", "E"}:
        parts = re.split(r"\s+(?:and|or|as well as)\s+|[,;]", text, maxsplit=policy.max_topics - 1)
        # Do not turn alternatives inside a question into independent requests.
        if re.search(r"\bor\b", text):
            meaningful = [text]
        else:
            # A part with too few distinct words cannot identify anything, so it
            # is dropped on its own. It must not cancel decomposition for the
            # parts that are specific, or one trailing conjunction silently
            # collapses a three-part question into a single topic.
            meaningful = [part.strip(" ,;") for part in parts if len(terms(part)) >= 2]
            if len(meaningful) > 1:
                # The whole question stays as a topic. A sub-part such as "what
                # tier does it run at" has no subject of its own, so scoring it
                # alone loses which service is meant. Keeping the unsplit form
                # preserves subject resolution; the parts add attribute detail.
                meaningful = [text] + meaningful[: policy.max_topics - 1]
        if len(meaningful) > 1:
            variants = meaningful[: policy.max_topics]
    return QueryPlan(
        request.query,
        tuple(variants),
        intent,
        str(request.as_of or request.snapshot_id) if request.as_of or request.snapshot_id else None,
        request.path,
    )


def _dot_scalar(topic_vector, claim_vector) -> float:
    """The shipped scorer: a left-to-right float64 accumulation.

    Kept as the reference implementation. The vectorised scorer must reproduce
    this at the pack boundary, not merely approximate it.
    """
    return sum(a * b for a, b in zip(topic_vector, claim_vector))


_TERM_CACHE: dict[str, frozenset[str]] = {}
_TERM_CACHE_LIMIT = 200_000


def claim_terms(text: str) -> frozenset[str]:
    """Terms of a claim representation, memoised on the immutable text.

    Claim representations derive from immutable claim text, so their tokenisation
    is stable for the life of the archive. Re-running the regex for every claim
    on every query was a second material cost, and this is the same kind of cache
    the embeddings use. Bounded so a pathological corpus cannot grow it without
    limit; an evicted entry is simply recomputed.
    """
    cached = _TERM_CACHE.get(text)
    if cached is not None:
        return cached
    value = frozenset(terms(text))
    if len(_TERM_CACHE) < _TERM_CACHE_LIMIT:
        _TERM_CACHE[text] = value
    return value


def relevance(
    full: MemoryResponse,
    request: MemoryRequest,
    intent: str,
    embedder,
    policy: RelevancePolicy,
    *,
    lexical: bool = False,
    claim_vectors: dict[str, list[float]] | None = None,
    vectorized: bool = False,
    fresh: dict[str, list[float]] | None = None,
) -> tuple[dict[str, float], QueryPlan, dict]:
    """Score authored keys against the question, then apply the acceptance gates.

    ``lexical=True`` replaces the embedding similarity with normalized token overlap.
    Both scores live in [0, 1], so the same minimum/strong/relative gates apply
    unchanged. This is the model-free rung: it only chooses which authored keys are
    relevant, and never touches claim status, validity, conflicts or evidence.

    ``claim_vectors`` supplies pre-computed vectors for claims, keyed by claim id.
    Only a claim present in the mapping is served from it; anything missing is
    embedded here, so a partial or empty cache costs latency and never changes the
    answer. Vectors are consumed read-only.

    ``fresh`` is an optional out-parameter: the vectors this call had to embed are
    added to it, keyed by claim id, so a caller that owns a session can persist them
    rather than making the next query pay for the same encoding again. Nothing is
    read back from it.

    ``vectorized=True`` computes the same inner product with NumPy. It is off by
    default because it was measured at 1.00x to 1.07x end to end and bought
    nothing: the inner product was never the cost. It is kept because the switch
    and the equivalence proof are reusable, and all 202 benchmark questions give
    byte-identical canonical Memory Packs either way.
    """
    from api.services.claim_embeddings import representation
    from api.services.memory_public import _claims

    interpreted = plan(request, intent, policy)
    claims = {c.id: c for c in _claims(full)}
    ids = sorted(claims)
    if not ids:
        return {}, interpreted, {"candidates": 0, "embedding_calls": 0, "embedding_texts": 0}
    representations = [representation(claims[c]) for c in ids]
    documents = [claim_terms(text) for text in representations]
    # Terms occurring in every candidate (e.g. project name) cannot DISCRIMINATE
    # between candidates, so ranking is computed on documents with them removed.
    # They are not evidence that the question is off-topic, so the acceptance gate
    # still sees the full documents -- see `overlap` below.
    common = frozenset.intersection(*documents) if len(documents) > 1 else frozenset()
    # Depends on neither the topic nor the gate, so it is computed once. Rebuilding
    # this per candidate and per topic was the dominant cost at scale.
    reduced = [document - common for document in documents]
    topic_terms = [terms(topic) - common for topic in interpreted.topics]
    # The question's own terms, un-reduced, for the same reason.
    question_terms = [terms(topic) for topic in interpreted.topics]

    embedding_calls, embedding_texts = 0, 0
    topic_vectors = None
    cached = dict(claim_vectors or {})
    missing = [position for position, cid in enumerate(ids) if cid not in cached]
    if not lexical:
        # One batch, question first, and only the claims the cache did not supply.
        # The ordering convention is unchanged from the uncached path, so any
        # embedder that treats the first text as the query still works.
        topics = list(interpreted.topics)
        texts = topics + [representations[p] for p in missing]
        vectors = embedder.embed(texts)
        embedding_calls, embedding_texts = 1, len(texts)
        if len(vectors) != len(texts) or not vectors or not vectors[0]:
            raise ValueError("Embedding output count/dimension mismatch")
        topic_vectors = vectors[: len(topics)]
        for position, vector in zip(missing, vectors[len(topics) :]):
            cached[ids[position]] = vector
        dimension = len(vectors[0])
        if any(len(v) != dimension or not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError("Invalid embedding vectors")
        if fresh is not None and missing:
            # Hand back only what the caller did not already have, so the expensive
            # work this query just did can be persisted instead of repeated.
            fresh.update(
                {ids[position]: vector for position, vector in zip(missing, vectors[len(topics) :])}
            )

    # Relevance is the dominant cost at scale, and the inner product is an
    # interpreted multiply-accumulate per dimension. When asked for, the same
    # product is computed once per topic for every candidate, and the gate loop
    # below is left exactly as it was, so only the number's origin changes.
    columns: list[Any] | None = None
    if vectorized and not lexical and len(ids) > 1:
        import numpy as np

        matrix = np.asarray([cached[cid] for cid in ids], dtype=np.float64)
        columns = [
            matrix @ np.asarray(topic_vectors[i], dtype=np.float64)
            for i in range(len(interpreted.topics))
        ]

    def score(i: int, position: int) -> float:
        if lexical:
            query_terms = topic_terms[i]
            if not query_terms:
                return 0.0
            return len(query_terms & reduced[position]) / len(query_terms)
        if columns is not None:
            return float(columns[i][position])
        return _dot_scalar(topic_vectors[i], cached[ids[position]])

    chosen = {}
    for i in range(len(interpreted.topics)):
        by_key = {}
        for position, cid in enumerate(ids):
            claim = claims[cid]
            if request.path and claim.path != request.path:
                continue
            value = score(i, position)
            # Overlap asks "is the question about this claim?", so it is tested
            # against the full claim terms. Reducing them first would delete the
            # subject the question is asking about whenever a second claim shares
            # it, and every such question would then need the strong-score bar
            # instead of the normal minimum.
            overlap = bool(question_terms[i] & documents[position])
            if policy.approach == "A":
                accepted = value >= 0.30
            elif policy.approach == "B":
                accepted = value >= 0.30 and overlap
            elif policy.approach == "C":
                accepted = value >= policy.minimum
            else:
                accepted = value >= policy.minimum and (
                    overlap or value >= policy.strong or math.isclose(value, policy.minimum)
                )
            if accepted:
                by_key[claim.key] = max(by_key.get(claim.key, -1), value)
        top = max(by_key.values(), default=0)
        for key, value in by_key.items():
            if value >= top * policy.relative:
                chosen[key] = max(chosen.get(key, -1), value)
    return (
        chosen,
        interpreted,
        {
            "candidates": len(ids),
            "embedding_calls": embedding_calls,
            "embedding_texts": embedding_texts,
            "topics": len(interpreted.topics),
            "scorer": "lexical" if lexical else "embedding",
            "claim_vectors_cached": 0 if lexical else len(ids) - len(missing),
        },
    )
