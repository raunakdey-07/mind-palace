"""The seven arms.

**Arm A is the shipped system.** It imports `api.services.memory_relevance` and
embeds through `api.services.claim_embeddings.representation_of` -- the exact
string the product caches vectors for -- so it cannot drift from the code it
claims to represent. The only thing removed is the database and the response
envelope.

**Arms B through F are new code** and are labelled as such everywhere they are
reported. Arm G is a reference embedder and is a separate question: does the
*embedder* limit this system, or does the *pipeline*?

## What each arm is testing

| arm | question it answers |
|---|---|
| A | where is the shipped system today |
| B | is the embedding channel earning anything at all |
| C | does lexical matching alone explain the shipped behaviour |
| D | do two mediocre rankers fuse into a good one |
| E | is there headroom a reranker can reach |
| F | is the failure "the answer was never a claim" |
| G | is the ceiling the embedder or the pipeline |

Arm F deserves its own note. It indexes **passages**, not claims, which is the
only way body text can answer a question. 17 of the 72 passages in the dev
corpus carry no authored claim at all. The dogfood's confident-wrong answer --
"does deleting a corpus remove the data from backups?" -- was exactly this: the
answer was in the body, and recall ranked claims. F is the arm that can find
it.

The hazard F introduces is authority. A passage with no claim is not evidence;
it is prose somebody wrote near a claim. So F **never promotes an unbacked
passage to a claim**. It may return one, flagged `unbacked_passage=True`, and
the count of those is reported next to every F number. An arm that improves
recall by inventing authority has not improved anything.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np

from api.services.claim_embeddings import representation_of
from api.services.memory_relevance import (
    RelevancePolicy,
    claim_terms,
    plan,
    terms,
)


from .corpus import Corpus, Passage, Question
from .metrics import Run
from .embeddings import SHIPPED_EMBEDDER, embed, rerank


def _tokenize(text: str) -> list[str]:
    """The product's stemmer, keeping multiplicity.

    `terms()` returns a set because the relevance gate asks "is this term
    present", which is a yes/no question. BM25 asks "how often", which is not,
    so it needs the tokens with their repeats intact. The stemmer is shared so
    that a difference between arm B and arm A is a difference in the *ranking
    function* and not in the vocabulary.
    """
    import re
    from api.services.memory_relevance import STOP

    words = re.findall(r"[a-z][a-z0-9]+", text.casefold())
    out = []
    for word in words:
        if word in STOP:
            continue
        for suffix in ("ation", "ing", "ed", "s"):
            if word.endswith(suffix) and len(word) > len(suffix) + 3:
                word = word[: -len(suffix)]
                break
        out.append(word)
    return out


#: Reciprocal-rank-fusion damping. 60 is the constant from the original RRF paper
#: and is kept rather than tuned: tuning it on dev and then reporting dev would
#: be circular, and the sensitivity of RRF to this constant is small.
RRF_K = 60

POLICY = RelevancePolicy()


class _Index:
    """Embeddings for every claim and every passage, computed once."""

    def __init__(self, corpus: Corpus, model: str = SHIPPED_EMBEDDER) -> None:
        self.model = model
        self.claims = corpus.claims
        self.passages = corpus.passages
        self.claim_texts = [representation_of(c.claim, c.key, c.path) for c in self.claims]
        self.passage_texts = [p.text for p in self.passages]
        self.claim_vectors = embed(self.claim_texts, model)
        self.passage_vectors = embed(self.passage_texts, model) if self.passage_texts else None
        self.claim_terms = [claim_terms(t) for t in self.claim_texts]
        # Token lists, not sets: BM25 needs real term frequency. See _BM25.
        self.claim_token_lists = [_tokenize(t) for t in self.claim_texts]
        self._claim_key_index: dict[str, list[int]] = {}
        for position, claim in enumerate(self.claims):
            self._claim_key_index.setdefault(claim.key, []).append(position)

    def keys_for(self, key: str) -> list[int]:
        return self._claim_key_index.get(key, [])

    def topic_texts(self, question: Question) -> list[str]:
        """The sub-queries the shipped planner would build for this question."""

        class _Request:
            query = question.text
            path = None
            as_of = None
            snapshot_id = None

        return list(plan(_Request(), "recall", POLICY).topics)


# --------------------------------------------------------------------------- #
# Arm names
# --------------------------------------------------------------------------- #

#: The lab's arms, named for what they do.
#:
#: They used to be `A` through `G`, and that was a mistake this module has now
#: paid for twice. `docs/performance/retrieval-lab-report.md` and the benchmark
#: in `scripts/benchmark/m0115_experiments.py` both had an "A": lab arm A was
#: the shipped scorer ranked at rank 1, benchmark strategy `A_semantic` was
#: semantic-always. Both were quoted next to each other, in the same
#: conversations, for sessions. A reader cannot resolve "arm A" without knowing
#: which document is being read, and one of those documents is the one that
#: reports the number.
#:
#: So the lab's arms are named. A bare letter in this repository now means the
#: benchmark strategy and nothing else, because the benchmark's keys are frozen
#: evidence cited by `check_evidence` and renaming them would edit a recorded
#: measurement to fix a naming problem.
ARMS: tuple[str, ...] = (
    "shipped-rank1",
    "bm25",
    "semantic",
    "rrf",
    "fusion-rerank",
    "passage",
    "bge-small",
)

#: What each one is, for a table of contents and for error messages.
ARM_MEANING: dict[str, str] = {
    "shipped-rank1": "the shipped relevance scorer, graded at rank 1, no lexical fallback",
    "bm25": "lexical only, real term frequency",
    "semantic": "dense only, the same shipped gate",
    "rrf": "reciprocal-rank fusion of the two above",
    "fusion-rerank": "fusion plus the shipped cross-encoder, the best MRR in the lab",
    "passage": "indexes passages, so body text can answer",
    "bge-small": "a 33M embedder instead of the shipped 22.7M one; unreachable offline",
}


# --------------------------------------------------------------------------- #
# arm `shipped-rank1` -- the shipped scorer
# --------------------------------------------------------------------------- #


def arm_shipped_rank1(index: _Index, question: Question, corpus: Corpus) -> Run:
    """`memory_relevance.relevance`, unwrapped.

    The product groups accepted claims by authored key, keeps the best score per
    key, then drops keys below `relative` of the best. That grouping is the
    interesting part and it is preserved exactly: ranking by raw claim score
    would let one key occupy three of the top five, which is a different system.
    """
    topics = index.topic_texts(question)
    topic_vectors = embed(topics, index.model)

    chosen: dict[str, float] = {}
    for topic_position, topic in enumerate(topics):
        question_terms = terms(topic)
        by_key: dict[str, float] = {}
        for position, claim in enumerate(index.claims):
            value = _cosine(topic_vectors[topic_position], index.claim_vectors[position])
            overlap = bool(question_terms & index.claim_terms[position])
            if value >= POLICY.minimum and (
                overlap or value >= POLICY.strong or math.isclose(value, POLICY.minimum)
            ):
                by_key[claim.key] = max(by_key.get(claim.key, -1.0), value)
        if not by_key:
            continue
        top = max(by_key.values())
        for key, value in by_key.items():
            if value >= top * POLICY.relative:
                chosen[key] = max(chosen.get(key, -1.0), value)

    ordered = sorted(chosen.items(), key=lambda kv: -kv[1])
    keys = [k for k, _ in ordered]
    scores = [v for _, v in ordered]
    return _finish(question, corpus, index, keys, scores)


# --------------------------------------------------------------------------- #
# arm `bm25` -- lexical only
# --------------------------------------------------------------------------- #


class _BM25:
    """Okapi BM25 over claim representations.

    Written here rather than pulled in because the alternative is a dependency
    whose BM25 does not necessarily tokenise the way this corpus does, and a
    mismatch there would be measured as a retrieval result rather than as a bug.
    The stemmer and stop list are the product's own `terms`.

    ## The first version of this was wrong, and the way it was wrong is the point

    The first implementation scored documents as *sets*, so every term had
    `tf = 1`. Claim representations average 8.6 distinct terms, and on a
    collection of 88 documents that produces enormous numbers of exact score
    ties -- three different keys all scoring 6.91 on "what is the current
    primary datastore". Ties broken by corpus order are not a ranking; they are a
    coin flip with a deterministic seed, and they put the gold key at rank 16 on
    a question whose top key was correct.

    Real term frequency is used instead. Claims here are one sentence long, so
    the effect is smaller than it would be on prose, but it is the difference
    between measuring BM25 and measuring an accident of its inputs.
    """

    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.documents = documents
        self.lengths = [len(d) for d in documents]
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        self.frequency: Counter = Counter()
        for document in documents:
            self.frequency.update(set(document))
        self.n = len(documents)

    def score(self, query_terms: set[str]) -> list[float]:
        out = []
        for position, document in enumerate(self.documents):
            counts = Counter(document)
            total = 0.0
            for term in query_terms:
                containing = self.frequency.get(term, 0)
                if not containing:
                    continue
                idf = math.log(1 + (self.n - containing + 0.5) / (containing + 0.5))
                tf = counts.get(term, 0)
                if not tf:
                    continue
                norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[position] / self.average)
                total += idf * (tf * (self.k1 + 1)) / norm
            out.append(total)
        return out


def arm_bm25(index: _Index, question: Question, corpus: Corpus, bm25: _BM25) -> Run:
    topics = index.topic_texts(question)
    best: dict[str, float] = {}
    for topic in topics:
        query_terms = terms(topic)
        if not query_terms:
            continue
        scores = bm25.score(query_terms)
        for position, claim in enumerate(index.claims):
            best[claim.key] = max(best.get(claim.key, -1.0), scores[position])
    ordered = sorted(best.items(), key=lambda kv: -kv[1])
    return _finish(question, corpus, index, [k for k, _ in ordered], [v for _, v in ordered])


# --------------------------------------------------------------------------- #
# arm `semantic` -- dense only
# --------------------------------------------------------------------------- #


def arm_semantic(index: _Index, question: Question, corpus: Corpus) -> Run:
    topics = index.topic_texts(question)
    topic_vectors = embed(topics, index.model)
    best: dict[str, float] = {}
    for topic_position in range(len(topics)):
        for position, claim in enumerate(index.claims):
            value = _cosine(topic_vectors[topic_position], index.claim_vectors[position])
            best[claim.key] = max(best.get(claim.key, -1.0), value)
    ordered = sorted(best.items(), key=lambda kv: -kv[1])
    return _finish(question, corpus, index, [k for k, _ in ordered], [v for _, v in ordered])


# --------------------------------------------------------------------------- #
# arm `rrf` -- reciprocal-rank fusion of `bm25` and `semantic`
# --------------------------------------------------------------------------- #


def arm_rrf(index: _Index, question: Question, corpus: Corpus, bm25: _BM25) -> Run:
    lexical = arm_bm25(index, question, corpus, bm25)
    semantic = arm_semantic(index, question, corpus)
    fused: dict[str, float] = {}
    for ranked in (lexical.keys, semantic.keys):
        for position, key in enumerate(ranked):
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + position + 1)
    ordered = sorted(fused.items(), key=lambda kv: -kv[1])
    return _finish(question, corpus, index, [k for k, _ in ordered], [v for _, v in ordered])


# --------------------------------------------------------------------------- #
# arm `fusion-rerank` -- `rrf` plus the cross-encoder
# --------------------------------------------------------------------------- #


#: How many fused candidates the reranker reads. 50 is half the dev corpus, so
#: the reranker genuinely reorders rather than rubber-stamping an already-short
#: list, and it is fixed here rather than swept, because sweeping it on dev and
#: reporting dev is how a number stops meaning anything.
RERANK_DEPTH = 50


def arm_fusion_rerank(index: _Index, question: Question, corpus: Corpus, bm25: _BM25) -> Run:
    fused = arm_rrf(index, question, corpus, bm25)
    head = fused.keys[:RERANK_DEPTH]
    if not head:
        return fused
    texts: list[str] = []
    for key in head:
        positions = index.keys_for(key)
        texts.append(index.claim_texts[positions[0]] if positions else key)
    scores = rerank(question.text, texts)
    combined = sorted(zip(head, scores), key=lambda kv: -kv[1])
    keys = [k for k, _ in combined] + fused.keys[RERANK_DEPTH:]
    values = [v for _, v in combined] + fused.scores[RERANK_DEPTH:]
    return _finish(question, corpus, index, keys, values)


# --------------------------------------------------------------------------- #
# arm `passage` -- passage index, projected back to claims
# --------------------------------------------------------------------------- #


def arm_passage(index: _Index, question: Question, corpus: Corpus) -> Run:
    """Rank passages, then report the claim each passage carries.

    Authority is preserved by construction: the score returned for a key is the
    best score among passages that actually contain a claim with that key. A
    passage with no claim still gets retrieved -- that is the whole point -- but
    it can only be returned as an unbacked passage, never as a key with a score.
    """
    if index.passage_vectors is None:
        return _finish(question, corpus, index, [], [])
    topic_vectors = embed(index.topic_texts(question), index.model)
    topic = topic_vectors[0] if len(topic_vectors) else None
    if topic is None:
        return _finish(question, corpus, index, [], [])

    ranked: list[tuple[float, Passage]] = sorted(
        (
            (_cosine(topic, index.passage_vectors[position]), passage)
            for position, passage in enumerate(index.passages)
        ),
        key=lambda t: -t[0],
    )
    best: dict[str, float] = {}
    passage_rank: list[str] = []
    for value, passage in ranked:
        passage_rank.append(passage.passage_id)
        if not passage.claim_keys:
            continue
        for key in passage.claim_keys:
            if value > best.get(key, -1.0):
                best[key] = value
    ordered = sorted(best.items(), key=lambda kv: -kv[1])
    keys = [k for k, _ in ordered]
    scores = [v for _, v in ordered]
    run = _finish(question, corpus, index, keys, scores)
    run.passages = passage_rank
    run.basis = "passage"
    # Only flagged when the TOP passage is unbacked. A body passage somewhere in
    # the tail is not a hazard; a body passage outranking every claim is.
    run.unbacked_passage = bool(ranked and not ranked[0][1].claim_keys)
    return run


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or not len(a) or not len(b):
        return 0.0
    return float(np.dot(a, b))


def _finish(
    question: Question, corpus: Corpus, index: _Index, keys: list[str], scores: list[float]
) -> Run:
    """Attach passage-level gold-proximity to a claim-ranked run.

    A passage list is derived from the returned keys so claim-level and
    passage-level recall are reported on the same run rather than as two
    different systems.
    """
    passages: list[str] = []
    seen: set[str] = set()
    for key in keys[:5]:
        for passage in corpus.passages_for(key):
            if passage.passage_id not in seen:
                seen.add(passage.passage_id)
                passages.append(passage.passage_id)
    return Run(
        qid=question.qid,
        slice=question.slice,
        answerable=question.answerable,
        keys=keys,
        scores=scores,
        passages=passages,
    )


# --------------------------------------------------------------------------- #
# arm `bge-small` -- the same pipeline, a stronger small embedder
# --------------------------------------------------------------------------- #


def arm_bge_small_available() -> tuple[bool, str]:
    """Can the reference embedder be loaded here? Recorded, never assumed."""
    from .embeddings import STRONGER_EMBEDDER, model_available

    return model_available(STRONGER_EMBEDDER)


def arm_bge_small(index: _Index, question: Question, corpus: Corpus, bm25: _BM25) -> Run:
    """Arm C's ranking with a different encoder.

    The point is attribution, not a better number. If this arm is much worse
    than C, the embedder is the limit; if it is about the same, the pipeline is.
    Both readings are reportable and neither is flattering by default.
    """
    from .embeddings import STRONGER_EMBEDDER

    if index.model != STRONGER_EMBEDDER:
        raise SystemExit(
            "arm G needs an index built with the reference embedder; "
            "pass --embedder to build one"
        )
    return arm_semantic(index, question, corpus)
