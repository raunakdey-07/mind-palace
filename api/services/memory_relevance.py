"""Bounded, deterministic question variants and relevance gates (never state rules).

All vocabulary comes from the query/archive. No generated facts or domain alias
map. Config is process-level, so adapters cannot bypass the relevance policy.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from operator import mul
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


#: How many ranked claims a response carries for the caller to adjudicate.
CANDIDATE_LIMIT = 3

#: `score_1 - score_2` below which rank 1 is a near-tie rather than a hit.
#:
#: Measured on the 175 answerable dev questions that carry a gold key, over the
#: ungated output of this scorer (175/175 rankable):
#:
#:   gold at rank 1        116/175  (0.663)
#:   gold at rank 2        155/175  (0.886)
#:   gold at rank 3        162/175  (0.926)
#:   gold at rank 5        170/175  (0.971)
#:
#: Rank 1 is wrong for the other 59, and 46 of those are inside the top three.
#: That is the population a single-score response cannot address, which is what
#: `candidates` is for.
#:
#: The threshold is set where the split is most informative, not where it is
#: cleanest -- there is no clean split, because a correct rank 1 is usually a
#: near-tie too. Median `score_1 - score_2` is 0.0461 for a wrong rank 1 against
#: 0.0954 for a right one, which overlap enough that no cut separates them:
#:
#:   cut    fires on wrong rank 1    fires on right rank 1    precision
#:   0.05        32/59 (54%)              28/116 (24%)          0.533
#:   0.10        49/59 (83%)              61/116 (53%)          0.445
#:   0.15        53/59 (90%)              88/116 (76%)          0.376
#:
#: 0.10 has the best precision of the three and fires on 38 of the 46 rank-1
#: misses that `candidates` can rescue. The remaining false alarms are the
#: intended cost: `ambiguous` asks the caller to read three quotes, it does not
#: refuse to answer.
#:
#: `authority_order` re-orders that near-tie band on a signal the scorer does not
#: have, which lifts rank 1 to 129/175 without moving k >= 2. It does not change
#: this threshold: a claim promoted by scope is still a claim the scorer could not
#: separate, and reporting that as confident would be a lie with a number on it.
DEFAULT_MARGIN = 0.10

#: How far above `minimum` still counts as "near the floor".
#:
#: A top score just clear of the abstention floor is weak on its own terms,
#: independently of what beat it.
DEFAULT_NEAR_FLOOR = 0.10

#: Cosine below which a candidate is not worth listing at all. Without it, a
#: near-weak top answer advertises two more weak ones as if they were a shortlist.
CANDIDATE_FLOOR = 0.15

#: A token that appears in at least this share of the candidates' claims is
#: the corpus's own vocabulary: it is in so many of them that it cannot tell
#: one claim from another. The project's own name lands here by construction,
#: and a question that matched on it alone has not found its subject.
COMMON_TOKEN_SHARE = 0.065

#: The smallest number of claims a token has to be in before it counts as the
#: corpus's own vocabulary at all. A corpus of twenty claims has none: a word
#: in two of them is evidence, not noise, and a threshold that said otherwise
#: would turn ordinary questions into abstentions.
COMMON_TOKEN_FLOOR = 8

#: How close the best claim that names the subject has to be to the best claim
#: overall. A question whose subject the corpus barely mentions is a question
#: the corpus cannot answer, however well the corpus's own vocabulary matched.
#:
#: The band's own value is not the test. The test is that the corpus's best
#: answer to the question is about something else: "Mind Palace" is in the
#: corpus's vocabulary, so a question that names it matches everything, and
#: "Kafka" is not, so the one claim that mentions Kafka is a long way down.
SUBJECT_RATIO = 0.80

#: Verbs that name an action rather than a subject. "How is Kafka configured?"
#: asks about Kafka, and the verb is not the question. A corpus that never
#: says "configured" has not been asked about anything.
GENERIC_QUERY_TERMS = frozenset("""configur configure configured work setup support run
    """.split())


def boilerplate_tokens(documents: list[frozenset[str]], wanted: frozenset[str]) -> frozenset[str]:
    """The tokens among `wanted` that so many claims carry that they cannot tell one from another.

    Counted over the candidates the ranking is choosing between, because that
    is the population the question has to be separated from. A token in every
    claim is already removed from the ranking by `common`; this is the wider
    set, the tokens that are in enough claims to be worthless as evidence.

    Only `wanted` is counted. A token the question does not use cannot be the
    one it matched on, and counting every token of every claim is the second
    most expensive thing this function does.
    """
    if not documents or not wanted:
        return frozenset()
    counts = {token: 0 for token in wanted}
    for document in documents:
        for token in counts.keys() & document:
            counts[token] += 1
    floor = max(COMMON_TOKEN_FLOOR, int(len(documents) * COMMON_TOKEN_SHARE))
    return frozenset(token for token, count in counts.items() if count >= floor)


def margin_threshold() -> float:
    """`DEFAULT_MARGIN`, overridable for a corpus whose scores run tighter."""
    return _bounded("MEMORY_QUERY_MARGIN", DEFAULT_MARGIN)


def authority_order(shortlist, live, *, dated: bool, band: float) -> list:
    """Order a near-tie shortlist by how the claim is scoped, not by how it scored.

    Scores decide relevance. They do not decide whether a claim is the standing
    answer or a statement about one interval, and at the near-tie the two claims
    score so closely that the score cannot tell them apart.

    The rule is a single boolean: with no time constraint in the question, a claim
    that carries `valid_from` or `valid_until` is scoped to an interval, and a
    claim that carries neither stands as written. "What is the datastore?" is
    asking for the standing answer, so the scoped claim does not lead.

    Measured over the 175 answerable dev questions that carry a gold key, applying
    this inside the ambiguous band only, against the same corpus and the same
    scorer:

        rule                          @1        @2        @3        @5
        score only (no re-order)    116/175   155/175   162/175   170/175
        scope first                 129/175   155/175   162/175   170/175
        + newest observation        107/175   150/175   163/175   170/175
        + status CURRENT            127/175   154/175   161/175   170/175
        newest observation only      91/175   149/175   163/175   170/175
        status CURRENT only         111/175   153/175   161/175   170/175

    Scope-first is the only rule that gains at rank 1 and costs nothing at k >= 2:
    sixteen rank-1 changes, fourteen of which put the gold claim first, one of
    which took it off. Every rule that also re-orders on observation time or on
    status does worse, because both are properties of how the corpus was written
    rather than of the question, and they outrank the score inside the band.

    Only the band moves. A claim already `band` or more behind is not a near-tie,
    it lost, and promoting it would replace the scorer's answer with a heuristic
    about the archive's shape.

    `dated` switches it off entirely. A question that names a time is asking about
    an interval, and there the scoped claim is the answer.
    """
    if dated or len(shortlist) < 2:
        return list(shortlist)
    top = shortlist[0][1]
    close = [entry for entry in shortlist if top - entry[1] < band]
    rest = [entry for entry in shortlist if top - entry[1] >= band]
    if len(close) < 2:
        return list(shortlist)
    close.sort(key=lambda entry: (_scoped(live.get(entry[0])), -entry[1], entry[0]))
    return close + rest


def _scoped(claims) -> bool:
    """Every claim under this key is scoped to an interval.

    Scoped only when *all* of them are. A key holding both an interval claim and
    a standing one is standing: the caller would be told to look at a claim that
    is true outside the interval, which is the general case.
    """
    claims = claims or []
    return bool(claims) and all(
        c.valid_from is not None or c.valid_until is not None for c in claims
    )


def near_floor() -> float:
    """`DEFAULT_NEAR_FLOOR`, same override rule."""
    return _bounded("MEMORY_QUERY_NEAR_FLOOR", DEFAULT_NEAR_FLOOR)


def _bounded(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError:
        return default
    # A non-finite or negative threshold would make every answer look ambiguous
    # (or none of them). That is a configuration mistake, not a tuning knob.
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else default


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

    `sum(map(mul, ...))` rather than a generator expression, because this runs
    once per claim per topic and the generator's per-item overhead was a
    measurable part of a recall on a corpus of a thousand claims. The
    accumulation order, and therefore the result, is the same either way:
    `map` yields the products left to right and `sum` adds them left to right.
    """
    return sum(map(mul, topic_vector, claim_vector))


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

    The third return value carries counters plus ``scored``: the best score every
    key reached, *before* the acceptance gates. The gates decide what to answer
    from; ``scored`` is what the ranking actually looked like, and it is what makes
    a near-tie visible instead of silently dropping the runner-up.

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
        return (
            {},
            interpreted,
            {
                "candidates": 0,
                "embedding_calls": 0,
                "embedding_texts": 0,
                "scored": {},
            },
        )
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
    # The words in the question that name a subject: neither the corpus's own
    # vocabulary nor a verb that names an action.
    #
    # "How is Kafka configured in Mind Palace?" against a corpus that never
    # mentions Kafka is not a question about the project. The words that match
    # are "Mind" and "Palace", which are in the project's own name and in a
    # sixth of its claims, and the claims they reach say nothing about Kafka.
    # The subject of the question is the one word the corpus does not have.
    asked = frozenset().union(*question_terms) if question_terms else frozenset()
    boilerplate = boilerplate_tokens(documents, asked)
    subject_terms = [
        question_terms[index] - boilerplate - GENERIC_QUERY_TERMS
        for index in range(len(interpreted.topics))
    ]

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
    scored: dict[str, float] = {}
    for i in range(len(interpreted.topics)):
        by_key = {}
        # The best score any claim reached for this topic, and the best score a
        # claim that names the subject reached. A question whose subject the
        # corpus barely mentions is a question the corpus cannot answer, however
        # well the corpus's own vocabulary matched, and the two numbers are what
        # say so.
        best_overall = 0.0
        best_subject = 0.0
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
            #
            # The question's side is reduced, though: only the words that name
            # a subject count. A question whose subject the corpus does not have
            # cannot be answered by a claim that matched on the corpus's own
            # vocabulary, so those claims have to earn the answer on the score
            # alone.
            subject = subject_terms[i] or question_terms[i]
            overlap = bool(subject & documents[position])
            if value > best_overall:
                best_overall = value
            if overlap and value > best_subject:
                best_subject = value
            # The best any claim under this key scores for this topic, kept
            # whether or not it cleared a gate. `chosen` below is what the product
            # answers from; `scored` is what it ranked, and the difference is the
            # near-miss. A caller that only ever sees `chosen` cannot tell a
            # rejected key from a key that was never a candidate.
            scored[claim.key] = max(scored.get(claim.key, -1), value)
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
        if subject_terms[i] and best_subject > 0.0 and best_subject < best_overall * SUBJECT_RATIO:
            # The corpus's best answer to this question is about something else.
            # "How is Kafka configured in Mind Palace?" matched the corpus's own
            # name, and the one claim that mentions Kafka is a long way down.
            # Answering it with the claims that matched the project's name
            # would be answering a different question, so this topic contributes
            # nothing.
            #
            # A question whose subject is in no claim at all does not fire this.
            # "Which architecture decisions apply?" names a word the corpus does
            # not use, and the answer is still the architecture decisions: there
            # is no subject to be competitive with.
            continue
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
            # The ungated per-key ranking, for the caller-facing near-tie signal.
            "scored": scored,
        },
    )
