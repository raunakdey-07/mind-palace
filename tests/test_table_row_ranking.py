"""The table row the question names, and the part of the question that lost it.

Two defects with one cause.

**A table row lost its place.** A question about the SDK's gzipped bundle size
was answered with the bundle sizes of two other chunks, and the row that
answers the question was last.

**The part of the question lost the subject.** "What is the gzipped bundle size
and keystroke-to-preview latency of the SDK?" splits into two parts, and the
first one -- "What is the gzipped bundle size?" -- names no subject at all.
Every row of the bundle-size table covers that part equally, so the row that
answers the question was ordered among its siblings by the embedding's opinion
of an unrelated sentence.

The coverage is measured against the question now, not against the part that
found the claim. A part is a lens for finding the claims, not a smaller
question.

The scoring is driven by a prescribed embedder rather than the real one, so the
regression does not depend on the model's opinion of a sentence: the sibling rows
are given a higher score for the part that lost the subject, which is what the
real corpus measured.
"""

from __future__ import annotations

from datetime import datetime, timezone

from api.models.memory import Claim, Evidence, MemoryRequest, MemoryResponse, State
from api.services import memory_relevance

CLOCK = datetime(2024, 6, 15, 12, tzinfo=timezone.utc)

#: The question from the report, over the bundle-size table of
#: `cv-embed/docs/PERFORMANCE.md`.
QUESTION = "What is the gzipped bundle size and the keystroke-to-preview latency of the SDK?"

#: The three rows of the bundle-size table, verbatim, and the latency row.
ROWS = {
    "md:bundle-sizes:0": "`react-vendor`: Raw: 231.90 kB; Gzip: 74.39 kB; When it loads: always",
    "md:bundle-sizes:1": "`index.css`: Raw: 35.38 kB; Gzip: 6.47 kB; When it loads: always",
    "md:bundle-sizes:2": (
        "`public/sdk.js`: Raw: 73.37 kB; Gzip: 20.40 kB; When it loads: host pages that embed"
    ),
    "md:bundle-sizes:3": (
        "`index` (app entry): Raw: 169.12 kB; Gzip: 42.66 kB; When it loads: always"
    ),
    "md:latency:0": "Builder keystroke reflected in preview: Value: 71 ms",
}

#: The row that answers the question, and the three rows that do not.
THE_SDK_ROW = "md:bundle-sizes:2"
THE_LATENCY_ROW = "md:latency:0"
THE_OTHER_ROWS = ("md:bundle-sizes:0", "md:bundle-sizes:1", "md:bundle-sizes:3")

#: The score of each row against each part of the question.
#:
#: The order is the question as a whole, then "What is the gzipped bundle
#: size?", then "the keystroke-to-preview latency of the SDK?".
#:
#: The sibling rows score higher against the part that lost the subject, which
#: is what the real corpus measured: 0.535 for `react-vendor` against "What is
#: the gzipped bundle size?" and 0.442 for the SDK's own row. Measured against
#: that part they cover the same terms, so the part's coverage cannot tell them
#: apart, and the row that answers the question is ordered among them by the
#: embedding's opinion of an unrelated sentence.
SCORES = {
    "md:bundle-sizes:0": [0.50, 0.62, 0.30],
    "md:bundle-sizes:1": [0.49, 0.60, 0.30],
    "md:bundle-sizes:2": [0.58, 0.55, 0.35],
    "md:bundle-sizes:3": [0.51, 0.61, 0.30],
    "md:latency:0": [0.50, 0.30, 0.80],
}


def _claim(key: str, text: str) -> Claim:
    return Claim(
        id=key,
        key=key,
        value=text,
        claim=text,
        status="CURRENT",
        version_id="a" * 64,
        path="docs/PERFORMANCE.md",
        observed_at=CLOCK,
        evidence_ids=["e-" + key],
    )


def _response(claims: list[Claim]) -> MemoryResponse:
    response = MemoryResponse(query="", corpus="test", state=State(valid_at=CLOCK))
    response.current_memories = list(claims)
    for claim in claims:
        response.evidence.append(
            Evidence(
                id=claim.evidence_ids[0],
                claim_id=claim.id,
                version_id=claim.version_id,
                document_id="d" * 64,
                path=claim.path,
                source_hash="f" * 64,
                chunk_id="c" * 64,
                heading=None,
                text=claim.claim,
                start_offset=0,
                end_offset=len(claim.claim),
                observed_at=CLOCK,
            )
        )
    return response


class PartAwareEmbedder:
    """A prescribed score per row and per part of the question.

    The question is split into parts before it is scored, and a part is a
    different question from the question as a whole. Each part is a separate
    axis, so a row can score higher against a part than against the question.

    The vectors are not unit vectors, because a test double is not a model: the
    relevance gate reads the dot product and nothing else.
    """

    def __init__(self, topics: tuple[str, ...], scores: dict[str, list[float]]) -> None:
        self.topics = list(topics)
        self.scores = scores

    def embed(self, texts: list[str]) -> list[list[float]]:
        width = len(self.topics)
        out: list[list[float]] = []
        for text in texts:
            if text in self.topics:
                vector = [0.0] * width
                vector[self.topics.index(text)] = 1.0
            else:
                vector = list(self.scores.get(text, [0.5] * width))
            out.append(vector)
        return out


def _order() -> list[str]:
    """The keys, best first."""
    from api.services.claim_embeddings import representation

    request = MemoryRequest(corpus="test", query=QUESTION)
    policy = memory_relevance.RelevancePolicy.configured()
    plan = memory_relevance.plan(request, "current", policy)
    claims = [_claim(key, text) for key, text in ROWS.items()]
    scores = {representation(claim): SCORES[claim.key] for claim in claims}
    embedder = PartAwareEmbedder(plan.topics, scores)
    ordered, _plan, _counters = memory_relevance.relevance(
        _response(claims), request, "current", embedder, policy
    )
    return [key for key, _ in sorted(ordered.items(), key=lambda item: -item[1])]


def test_the_row_that_names_the_component_ranks_above_its_siblings():
    """The SDK's row is the answer to a question about the SDK.

    Its two siblings are the same table's rows about other components, and they
    score higher against the part of the question that lost the subject.
    """
    order = _order()

    assert order.index(THE_SDK_ROW) < min(
        order.index(row) for row in THE_OTHER_ROWS
    ), f"the SDK's row is not above its siblings: {order}"


def test_the_matching_row_is_not_crowded_out_of_the_top_slots():
    """The siblings must not take every slot before the matching row is shown.

    A response that is truncated by its budget keeps the claims that come
    first, so a matching row that lands fifth is a row the reader never sees.
    """
    order = _order()

    assert order.index(THE_SDK_ROW) < 4, f"the SDK's row is not in the top four: {order}"


def test_the_row_that_answers_the_other_part_still_answers_it():
    """The fix is about the ordering, not about what the question selects.

    The keystroke row answers the second part, and it stays in the answer.
    """
    order = _order()

    assert THE_LATENCY_ROW in order
    assert set(ROWS) <= set(order), f"a row was dropped from the answer: {order}"


def test_the_coverage_is_measured_against_the_question():
    """The rule, stated on its own.

    The question names the SDK, and only the SDK's row carries the word. The
    part that lost the subject names no component at all, so measured against
    it every row of the table covers the same terms.
    """
    request = MemoryRequest(corpus="test", query=QUESTION)
    policy = memory_relevance.RelevancePolicy.configured()
    plan = memory_relevance.plan(request, "current", policy)

    assert len(plan.topics) == 3, f"the question did not split as expected: {plan.topics}"
    assert "sdk" in memory_relevance.terms(plan.topics[0])
    assert "sdk" not in memory_relevance.terms(
        plan.topics[1]
    ), "the part that lost the subject still names it"

    sdk = memory_relevance.claim_terms(f"{ROWS[THE_SDK_ROW]} {THE_SDK_ROW} docs/PERFORMANCE.md")
    sibling = memory_relevance.claim_terms(
        f"{ROWS['md:bundle-sizes:0']} md:bundle-sizes:0 docs/PERFORMANCE.md"
    )
    question = set(memory_relevance.terms(plan.topics[0]))
    assert (question & sdk) - (question & sibling) == {
        "sdk"
    }, "the SDK's row no longer carries a term the siblings do not"
